# Recepción ODONTO SMART — Python y LangChain

Recepción conversacional sin dependencia de n8n. LangChain coordina 15 herramientas;
OdontoFlow valida y guarda operaciones en PostgreSQL; Chatwoot conecta WhatsApp.

**Estado:** código local corregido y verificado; conexión y aceptación con
Gemini/Chatwoot/WhatsApp reales pendientes. No se ha cambiado el webhook activo.

## ¿Cuál backend usa?

La recepción es un servicio Python nuevo. El backend de OdontoFlow ya existía;
ahora también está incluido en este repositorio dentro de
`OdontoFlow-Backend-github-clean/`. En el disco, su ruta es
`C:\Users\leona\OneDrive\Documentos\ChatGPT\Proyecto Odonto Smart\recepcion-langchain\OdontoFlow-Backend-github-clean`.
El agente envía las solicitudes a la API FastAPI del backend, que valida y guarda
los cambios en PostgreSQL. Son dos procesos y el backend conserva su base de datos.

- `GUIA_RECEPCION_CODIGO.md`: cada tarea, flujo, función y ejemplos de código.
- `VALIDACION.md`: resultados, límites y comandos reproducibles.
- `audit/INFORME_INICIAL.md`: auditoría histórica previa a las correcciones.

## Instalar y comprobar

Desde esta carpeta, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
# Solo si todavía no existe .env:
Copy-Item .env.example .env
# Completar las variables en un editor; no publicar credenciales.
.\.venv\Scripts\python.exe -m recepcion.doctor
.\.venv\Scripts\python.exe -m recepcion.doctor --online
.\start.ps1
```

`start.ps1 -CheckOnly` verifica la configuración. La API corre en
http://127.0.0.1:8010; documentación interactiva en `/docs`.
Siempre usar `--workers 1`; este servicio utiliza SQLite y locks del proceso.

## Variables y conexiones

| Variable | Uso |
|---|---|
| `ODONTO_BACKEND_URL` | URL de la API FastAPI del backend en `OdontoFlow-Backend-github-clean/` |
| `ODONTO_INBOUND_TOKEN` | Credencial del perfil de ingreso de mensajes |
| `ODONTO_AGENT_TOKEN` | Credencial del agente de recepción y su organización |
| `RECEPTION_API_KEY` | Clave privada para `/messages` y `/status` |
| `GOOGLE_API_KEY` | Clave de Gemini válida para el modelo seleccionado |
| `RECEPTION_MODEL` | Modelo de Gemini; valor inicial `gemini-2.5-flash`, validar disponibilidad en la cuenta |
| `RECEPTION_DB` | Ruta del archivo SQLite persistente |
| `CHATWOOT_URL`, `CHATWOOT_TOKEN` | URL y token de la instancia Chatwoot |
| `CHATWOOT_WEBHOOK_SECRET` | Secreto exclusivo del webhook entrante |
| `CHATWOOT_ACCOUNT_ID`, `CHATWOOT_INBOX_ID` | Cuenta y bandeja de WhatsApp que atenderá este proceso |
| `CHATWOOT_TEAM_ID` | Equipo humano receptor; opcional, sin él solo se etiqueta/pausa |
| `ODONTO_CHANNEL_EXTERNAL_ID` | Canal activo `provider=whatsapp` correspondiente a esa bandeja |
| `ODONTO_DELIVERY_TOKEN` | Credencial del perfil `outbound-dispatcher` de la misma organización |
| `ODONTO_OPERATOR_TOKEN` | Credencial autorizada para reanudar atención automática |
| `RECEPTION_BUFFER_SECONDS` | Agrupación de fragmentos; 30 por defecto, 0–120 |
| `MEDIA_ALLOWED_ORIGINS` | Lista JSON de orígenes exactos para audios externos |

Para laboratorio HTTP se puede dejar `CHATWOOT_URL` vacía. El modelo y el backend
siguen necesitando sus credenciales. Las pruebas automatizadas no las requieren.

El backend incluido contiene el contrato `OutboundClaimRequest.outbound_ids`;
el doctor online lo comprueba. Inicia este backend desde su carpeta para que el
servicio use la versión correcta. Mantén allí la organización, el catálogo y sus
credenciales. No reutilices la base `odontoflow_test` para pacientes.

## Iniciar los dos procesos en Windows

Desde la raíz del repositorio, abre una ventana PowerShell para OdontoFlow:

```powershell
Set-Location .\OdontoFlow-Backend-github-clean
docker compose up -d db
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
Copy-Item .env.example .env  # completar solo si todavía no existe
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

En otra ventana, desde la raíz del repositorio, inicia la recepción con
`start.ps1`. Para WhatsApp, `ODONTO_BACKEND_URL` apunta a ese proceso en
`http://127.0.0.1:8000`; sus credenciales deben corresponder a la organización
del canal. El backend conserva su base PostgreSQL y se ejecuta como proceso
separado. Si ejecutas la recepción en Docker, usa `host.docker.internal` en
`ODONTO_BACKEND_URL` para llegar al backend de Windows.

## Activar la bandeja

Chatwoot debe tener WhatsApp conectado. Configurar el evento `message_created`:

```text
https://<host-del-servicio>/webhooks/chatwoot?key=<CHATWOOT_WEBHOOK_SECRET>
```

El proxy debe ocultar la query string en logs. El servicio inicia Uvicorn con
`--no-access-log`. Para probar primero, usar una bandeja de prueba. Al pasar el
canal a este webhook, retirar el workflow anterior como consumidor para evitar
que dos automatizaciones respondan al mismo paciente. Este cambio no se realizó.

Una nota privada `/reanudar` de un usuario de Chatwoot reabre una conversación
pausada. El paciente y el modelo no tienen esa facultad.

## Prueba por HTTP

`POST /messages` requiere `Authorization: Bearer <RECEPTION_API_KEY>`:

```json
{
  "schema_version": "1.0",
  "provider": "test",
  "channel_account_external_id": "odonto-smart-lab",
  "provider_message_id": "prueba-001",
  "external_contact_id": "contacto-prueba",
  "phone_e164": "+51999000001",
  "message_type": "text",
  "text": "Quiero reservar una limpieza",
  "occurred_at": "2026-10-13T14:00:00-05:00"
}
```

Crear previamente el canal de laboratorio del backend; usar hora actual y un
ID nuevo para cada mensaje. Reutilizar el mismo ID solo al reintentar ese evento.
`provider=test` devuelve la respuesta por HTTP y nunca se entrega a WhatsApp.
Para confirmar una propuesta, primero leer la respuesta y después enviar otro
mensaje con `Sí, confirmo` y un timestamp posterior.

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

Incluye los seis probes de auditoría original. Usa LangChain real y modelos/API
controlados de prueba. Audio simulado verifica descarga y circuito, no precisión
acústica ni disponibilidad de Gemini.

Integración opcional con PostgreSQL local, sin otra suite de backend ejecutándose:

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.\OdontoFlow-Backend-github-clean[dev]'
.\.venv\Scripts\python.exe integration/run.py
```

El runner admite únicamente `localhost`/`127.0.0.1:5434/odontoflow_test`. Reinicia
el esquema de esa base de pruebas mediante las fixtures del backend. Prueba
perfil, disponibilidad, reserva, reprogramación, cancelación, entrega, handoff y
reanudación con API y PostgreSQL reales; Gemini y Chatwoot siguen simulados.

## Docker

```powershell
docker build -t odonto-recepcion-langchain:local .
docker compose up -d --build
```

Completar `.env` antes de iniciar. Dentro del contenedor, `127.0.0.1` apunta al
propio contenedor: para un backend del host Windows, usar una URL como
`http://host.docker.internal:<puerto>`; para otro servicio Docker, su nombre DNS.
El compose expone únicamente `127.0.0.1:8010` y conserva SQLite en un volumen.
El reverse proxy/TLS y el acceso externo se configuran en el despliegue elegido.

## Operación y recuperación

- `/health`: proceso vivo. `/status`: conteos operativos, exige la clave privada.
- Después de cinco fallos al procesar una entrada, queda `failed` y se intenta
  derivar a una persona. Revisar esos casos; no se reintentan indefinidamente.
- Las asignaciones humanas pendientes se conservan y reintentan con espera.
- Las salidas reclaman solo sus propios IDs. Un fallo seguro previo al envío
  permite reintento; los intentos y esperas los controla el backend.
- `delivery_uncertain`: comprobar la conversación en Chatwoot y el
  `odonto_outbound_id` antes de decidir un reenvío manual. No eliminar la tabla
  de claves ni repetir el mensaje a ciegas. No hay reconciliación automática con
  WhatsApp ni herramienta de reenvío manual en este servicio.
- `sent` registra aceptación de Chatwoot; no equivale a `delivered` o `read`.
- `outbound_follow_up` puede incluir un lease agotado remotamente: es un conteo
  local para revisión, no el estado autoritativo de todas las entregas.
- Guardar y respaldar el volumen, con acceso restringido. Contiene datos personales
  y claves de deduplicación. La retención automatizada de SQLite queda pendiente
  para producción; el backend tiene su mecanismo propio de retención.

No incluye recordatorios, campañas, cobros, facturación, Google Calendar,
Telegram ni múltiples réplicas. Los contratos locales deben mantenerse alineados
con los esquemas del backend. La nueva recepción no llama a n8n.
