# Notion WhatsApp Bot

Bot personal de WhatsApp que gestiona tu Notion (tareas, eventos, apuntes de facultad, diagramas) usando Claude con tool use.

## Stack

- **FastAPI** webhook receiver
- **Twilio WhatsApp Sandbox** (gratis, para empezar)
- **Anthropic API** con tool use clasico (no MCP)
- **notion-client** directo contra Notion API

## Setup

### 1. Variables de entorno

```bash
cp .env.example .env
# editar .env con tus valores reales
```

Mapeá los 3 database IDs (entrá a cada DB en Notion como full page para identificarla).

**IMPORTANTE:** rotá el `NOTION_TOKEN` si alguna vez quedó expuesto en chat o en un commit.

### 2. Instalar dependencias

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Correr local

```bash
uvicorn main:app --reload --port 8000
```

### 4. Exponer el webhook

Para probar local con Twilio necesitás un tunel:

```bash
# en otra terminal
ngrok http 8000
```

Copiá la URL HTTPS de ngrok (algo como `https://abc123.ngrok.io`).

### 5. Configurar Twilio Sandbox

1. Ir a [Twilio Console > Messaging > Try it out > WhatsApp Sandbox](https://console.twilio.com/us1/develop/sms/try-it-out/whatsapp-learn)
2. Unirte al sandbox mandando el codigo (algo tipo "join xxx-yyy") al numero que te muestran, desde tu WhatsApp
3. En la config del sandbox, en "When a message comes in" pegar: `https://abc123.ngrok.io/webhook`
4. Method: HTTP POST

### 6. Probar

Mandate un mensaje al numero de sandbox:

> agregá tarea estudiar algoritmos para el viernes prioridad alta

Deberia responderte algo como `✓ tarea creada para el 16/05`.

## Comandos utiles

- `nuevo` o `/reset` → limpia el historial de la sesion
- Cualquier otra cosa → procesado por Claude

## Deploy a Railway

```bash
# requiere railway CLI
railway login
railway init
railway up
```

Setear todas las env vars en el dashboard de Railway. Cambiar el webhook de Twilio a la URL de Railway.

## Limites del MVP

- Solo texto (sin audio, sin imagenes)
- Sesion guardada en `/tmp/` (se pierde si Railway reinicia el contenedor)
- Solo respondes a un numero (`MY_WHATSAPP`)
- Si renombras un proyecto en Notion, restart del proceso para limpiar el cache

## Proximos pasos (Fase 2)

- Whisper para transcribir audios
- Vision API para procesar fotos del pizarron → apuntes
- Persistencia de sesiones en Postgres
- Resumen automatico cuando el historial pasa de 30 vueltas
