from contextlib import asynccontextmanager
import asyncio
import time
import secrets

from fastapi import FastAPI, Header, HTTPException
import httpx
from langchain_google_genai import ChatGoogleGenerativeAI

from .agent import Reception, ReceptionUnavailable
from .backend import Backend
from .config import Settings
from .message_contracts import InboundMessageCreate
from .store import Store
from .chatwoot import Chatwoot, normalize


@asynccontextmanager
async def lifespan(app):
    settings = Settings()
    if any(not value.get_secret_value() for value in [settings.reception_api_key,
           settings.odonto_inbound_token, settings.odonto_agent_token, settings.google_api_key]):
        raise RuntimeError("Configure las credenciales en .env antes de iniciar.")
    if settings.chatwoot_url and (not all(value.get_secret_value() for value in [settings.chatwoot_token,
        settings.chatwoot_webhook_secret, settings.odonto_delivery_token, settings.odonto_operator_token])
        or not settings.odonto_channel_external_id):
        raise RuntimeError("Complete las credenciales y el canal Chatwoot.")
    store = Store(settings.reception_db)
    backend = Backend(settings, store)
    model = ChatGoogleGenerativeAI(model=settings.reception_model, temperature=0,
                                  api_key=settings.google_api_key.get_secret_value(), timeout=60, max_retries=2)
    app.state.settings = settings
    app.state.reception = Reception(backend, model, store)
    worker_task = None
    app.state.chatwoot = None
    if settings.chatwoot_url:
        adapter = Chatwoot(settings, app.state.reception, store)
        app.state.chatwoot = adapter
        worker_task = asyncio.create_task(adapter.run())
    try:
        yield
    finally:
        if worker_task:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
            await app.state.chatwoot.client.aclose()
        await backend.client.aclose()
        store.close()


app = FastAPI(title="ODONTO SMART — Recepción LangChain", lifespan=lifespan)


@app.post("/webhooks/chatwoot")
async def chatwoot_webhook(payload: dict, key: str = ""):
    adapter = app.state.chatwoot
    if adapter is None:
        raise HTTPException(503, "Chatwoot no configurado.")
    if not secrets.compare_digest(key, adapter.settings.chatwoot_webhook_secret.get_secret_value()):
        raise HTTPException(401, "Webhook no autorizado.")
    conversation = payload.get("conversation", {})
    # Resume is allowed only for a private note authored by a Chatwoot User.
    if (payload.get("event") == "message_created" and payload.get("private") is True
        and payload.get("content", "").strip().lower() == "/reanudar"
        and payload.get("sender", {}).get("type") == "user"
        and payload.get("account", {}).get("id") == adapter.settings.chatwoot_account_id
        and conversation.get("inbox_id") == adapter.settings.chatwoot_inbox_id):
        try:
            await adapter.resume(conversation["id"], payload["id"])
        except (ValueError, KeyError, httpx.HTTPError):
            raise HTTPException(503, "No se pudo reanudar la conversación.") from None
        return {"status": "resumed"}
    try:
        normalized = normalize(payload, adapter.settings)
    except (ValueError, TypeError, KeyError):
        raise HTTPException(422, "Evento sin texto, teléfono o identificadores válidos.") from None
    if normalized is None:
        return {"status": "ignored"}
    conversation_id, inbound = normalized
    body = inbound.model_dump(mode="json")
    if payload.get("attachments"):
        body["_audio"] = payload["attachments"][0]
    adapter.store.enqueue(inbound.provider_message_id, conversation_id, body,
                          time.time() + adapter.settings.reception_buffer_seconds)
    return {"status": "queued"}


@app.get("/health")
def health():
    return {"status": "ok", "orchestrator": "langchain"}


@app.get("/status")
async def status(authorization: str = Header(default="")):
    expected = "Bearer " + app.state.settings.reception_api_key.get_secret_value()
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Credencial inválida.")
    store = app.state.reception.store
    return {"inbound_queue": store.queue_counts(), "chatwoot_enabled": app.state.chatwoot is not None,
        "pending_human_assignments": store.db.execute("SELECT count(*) FROM handoff_jobs").fetchone()[0],
        "pending_delivery_acknowledgements": store.db.execute("SELECT count(*) FROM delivery_results").fetchone()[0],
        "outbound_follow_up": store.db.execute("SELECT count(*) FROM owned_outbounds WHERE terminal=0").fetchone()[0],
        "uncertain_deliveries": store.db.execute("SELECT count(*) FROM calls WHERE key LIKE 'delivery:%' AND json_extract(result,'$.error_code')='delivery_uncertain'").fetchone()[0]}


@app.post("/messages")
async def receive(payload: InboundMessageCreate, authorization: str = Header(default="")):
    expected = "Bearer " + app.state.settings.reception_api_key.get_secret_value()
    if not secrets.compare_digest(authorization, expected):
        raise HTTPException(401, "Credencial inválida.")
    if payload.message_type != "text":
        raise HTTPException(422, "Este endpoint recibe texto; el adaptador debe transcribir el audio.")
    try:
        return await app.state.reception.receive(payload)
    except (httpx.HTTPError, ReceptionUnavailable):
        raise HTTPException(503, "Recepción temporalmente no disponible; requiere revisión o reintento.") from None
