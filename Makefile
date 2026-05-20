.DEFAULT_GOAL := help

# Detecta docker compose v2 (`docker compose`) o v1 (`docker-compose`).
COMPOSE ?= $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" || echo "docker-compose")

.PHONY: help up down logs ps build rebuild migrate shell psql test test-docker smoke clean backup restore

help: ## Mostrar comandos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up: ## Levantar la stack (postgres + web) en background
	$(COMPOSE) up -d --build
	@echo "Esperando a que web este healthy..."
	@for i in $$(seq 1 30); do \
	  status=$$($(COMPOSE) ps --format json web 2>/dev/null | python -c "import sys,json; [print(o.get('Health','')) for o in (json.loads(sys.stdin.read()) if sys.stdin.isatty()==False else [])]" 2>/dev/null | head -1); \
	  if [ "$$status" = "healthy" ]; then echo "✓ web healthy"; exit 0; fi; \
	  sleep 2; \
	done; \
	echo "⚠ web no llego a healthy; revisá: make logs"

down: ## Bajar la stack (mantiene volumenes)
	$(COMPOSE) down

logs: ## Tail de logs de todos los servicios
	$(COMPOSE) logs -f --tail=100

ps: ## Estado de los servicios
	$(COMPOSE) ps

build: ## Build de la imagen web sin cache
	$(COMPOSE) build --no-cache web

rebuild: ## Down + build + up
	$(MAKE) down
	$(MAKE) build
	$(MAKE) up

migrate: ## Aplicar migraciones Alembic dentro del contenedor web
	$(COMPOSE) exec web alembic upgrade head

shell: ## Shell bash dentro del contenedor web
	$(COMPOSE) exec web bash

psql: ## psql dentro del contenedor postgres
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-wpp} -d $${POSTGRES_DB:-wpp}

test: ## Correr pytest en LOCAL (sin Docker, rapido)
	TWILIO_VALIDATE=false python -m pytest -v

test-docker: ## Correr pytest dentro del contenedor (mas lento, usa la imagen real)
	$(COMPOSE) exec web bash -c "pip install --user -q -r requirements-dev.txt && TWILIO_VALIDATE=false python -m pytest -v"

smoke: ## Smoke check: /health + simulacion de webhook contra el contenedor
	@echo "→ /health"
	@curl -sS http://localhost:$${WEB_PORT:-8000}/health | python -m json.tool
	@echo "→ webhook (TWILIO_VALIDATE=false en .env para esto)"
	@WEBHOOK_URL=http://localhost:$${WEB_PORT:-8000}/webhook bash scripts/simulate_webhook.sh "gasto 100 cafe"

clean: ## Down + borrar volumenes (PIERDE datos de Postgres y /data)
	$(COMPOSE) down -v

backup: ## pg_dump comprimido en ./backups/ (rotacion BACKUP_RETAIN, def 14)
	bash scripts/backup.sh

restore: ## Restaurar backup: make restore FILE=backups/wpp_xxx.sql.gz
	@test -n "$(FILE)" || (echo "uso: make restore FILE=backups/wpp_xxx.sql.gz"; exit 2)
	@test -f "$(FILE)" || (echo "no existe: $(FILE)"; exit 2)
	gunzip -c "$(FILE)" | $(COMPOSE) exec -T postgres psql -U $${POSTGRES_USER:-wpp} -d $${POSTGRES_DB:-wpp}
