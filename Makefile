.DEFAULT_GOAL := help

# Detecta docker compose v2 (`docker compose`) o v1 (`docker-compose`).
# Sin fallback ciego: si ninguno resuelve, COMPOSE queda VACIO y check-docker
# reporta la causa real. Devolver "docker-compose" a ciegas producia un
# "No such file or directory" que hacia parecer que faltaba instalar algo,
# cuando lo normal es que Docker Desktop simplemente no este arrancado (y en
# instalaciones modernas el binario v1 directamente no existe: Homebrew lo
# linkea como plugin fuera del PATH).
COMPOSE ?= $(shell \
  if docker compose version >/dev/null 2>&1; then echo "docker compose"; \
  elif command -v docker-compose >/dev/null 2>&1; then echo "docker-compose"; \
  fi)

# macOS no trae /usr/bin/python desde Monterey 12.3; solo python3. Sin esto,
# el chequeo de "healthy" de `up` fallaba silencioso (va con 2>/dev/null) y
# siempre imprimia el warning aunque el contenedor hubiera arrancado bien.
PY ?= $(shell command -v python3 >/dev/null 2>&1 && echo python3 || echo python)

.PHONY: help check-docker up down logs ps build rebuild migrate shell psql test test-docker smoke clean backup restore tunnel-up tunnel-down tunnel-logs tunnel-status reactivate quick-tunnel quick-logs quick-down

help: ## Mostrar comandos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# Guard interno (sin ## a proposito: no aparece en `make help`). Distingue las
# tres causas que antes colapsaban en el mismo error incomprensible.
check-docker:
	@command -v docker >/dev/null 2>&1 || { \
	  echo "✗ docker no esta instalado."; \
	  echo "  macOS:  brew install --cask docker-desktop"; \
	  echo "  Linux:  https://docs.docker.com/engine/install/"; \
	  exit 1; }
	@docker info >/dev/null 2>&1 || { \
	  echo "✗ Docker esta instalado pero el daemon no responde."; \
	  echo "  Docker Desktop no esta corriendo. Abrilo:"; \
	  echo "      open /Applications/Docker.app"; \
	  echo "  y espera a que la ballena de la barra deje de animarse."; \
	  exit 1; }
	@test -n "$(COMPOSE)" || { \
	  echo "✗ No se encontro docker compose v2 ni docker-compose v1."; \
	  echo "  Docker Desktop registra el plugin en su primer arranque."; \
	  echo "  Abrilo una vez y reintenta; verifica con: docker compose version"; \
	  exit 1; }

up: check-docker ## Levantar la stack (postgres + web) en background
	$(COMPOSE) up -d --build
	@echo "Esperando a que web este healthy..."
	@for i in $$(seq 1 30); do \
	  status=$$($(COMPOSE) ps --format json web 2>/dev/null | $(PY) -c "import sys,json; [print(o.get('Health','')) for o in (json.loads(sys.stdin.read()) if sys.stdin.isatty()==False else [])]" 2>/dev/null | head -1); \
	  if [ "$$status" = "healthy" ]; then echo "✓ web healthy"; exit 0; fi; \
	  sleep 2; \
	done; \
	echo "⚠ web no llego a healthy; revisá: make logs"

down: check-docker ## Bajar la stack (mantiene volumenes)
	$(COMPOSE) down

logs: check-docker ## Tail de logs de todos los servicios
	$(COMPOSE) logs -f --tail=100

ps: check-docker ## Estado de los servicios
	$(COMPOSE) ps

build: check-docker ## Build de la imagen web sin cache
	$(COMPOSE) build --no-cache web

rebuild: ## Down + build + up
	$(MAKE) down
	$(MAKE) build
	$(MAKE) up

migrate: check-docker ## Aplicar migraciones Alembic dentro del contenedor web
	$(COMPOSE) exec web alembic upgrade head

shell: check-docker ## Shell bash dentro del contenedor web
	$(COMPOSE) exec web bash

psql: check-docker ## psql dentro del contenedor postgres
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-wpp} -d $${POSTGRES_DB:-wpp}

test: ## Correr pytest en LOCAL (sin Docker, rapido)
	TWILIO_VALIDATE=false $(PY) -m pytest -v

test-docker: check-docker ## Correr pytest dentro del contenedor (mas lento, usa la imagen real)
	$(COMPOSE) exec web bash -c "pip install --user -q -r requirements-dev.txt && TWILIO_VALIDATE=false python -m pytest -v"

smoke: check-docker ## Smoke check: /health + simulacion de webhook contra el contenedor
	@echo "→ /health"
	@curl -sS http://localhost:$${WEB_PORT:-8000}/health | $(PY) -m json.tool
	@echo "→ webhook (TWILIO_VALIDATE=false en .env para esto)"
	@WEBHOOK_URL=http://localhost:$${WEB_PORT:-8000}/webhook bash scripts/simulate_webhook.sh "gasto 100 cafe"

reactivate: ## Diagnostico end-to-end tras un periodo parado (ver docs/REACTIVATION.md)
	bash scripts/reactivate.sh

quick-tunnel: check-docker ## Tunel efimero trycloudflare.com (validar sin dominio propio)
	bash scripts/quick-tunnel.sh

quick-logs: check-docker ## Tail de logs del quick tunnel
	$(COMPOSE) logs -f --tail=100 cloudflared-quick

quick-down: check-docker ## Bajar solo el quick tunnel (web/postgres siguen)
	$(COMPOSE) stop cloudflared-quick && $(COMPOSE) rm -f cloudflared-quick

clean: check-docker ## Down + borrar volumenes (PIERDE datos de Postgres y /data)
	$(COMPOSE) down -v

backup: check-docker ## pg_dump comprimido en ./backups/ (rotacion BACKUP_RETAIN, def 14)
	bash scripts/backup.sh

restore: check-docker ## Restaurar backup: make restore FILE=backups/wpp_xxx.sql.gz
	@test -n "$(FILE)" || (echo "uso: make restore FILE=backups/wpp_xxx.sql.gz"; exit 2)
	@test -f "$(FILE)" || (echo "no existe: $(FILE)"; exit 2)
	gunzip -c "$(FILE)" | $(COMPOSE) exec -T postgres psql -U $${POSTGRES_USER:-wpp} -d $${POSTGRES_DB:-wpp}

tunnel-up: check-docker ## Levantar stack + cloudflared (requiere CF_TUNNEL_TOKEN en .env)
	$(COMPOSE) --profile tunnel up -d --build

tunnel-down: check-docker ## Bajar solo el contenedor cloudflared (web/postgres siguen)
	$(COMPOSE) stop cloudflared && $(COMPOSE) rm -f cloudflared

tunnel-logs: check-docker ## Tail de logs de cloudflared
	$(COMPOSE) logs -f --tail=100 cloudflared

tunnel-status: ## Chequeo del tunel: dominio publico responde a /health
	@test -n "$$PUBLIC_WEBHOOK_HOST" || (echo "PUBLIC_WEBHOOK_HOST vacio en .env"; exit 2)
	@echo "→ https://$$PUBLIC_WEBHOOK_HOST/health"
	@curl -sS "https://$$PUBLIC_WEBHOOK_HOST/health" | $(PY) -m json.tool
