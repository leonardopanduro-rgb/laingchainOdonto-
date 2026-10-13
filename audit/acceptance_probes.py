"""Regression probes from the 2026-10-13 review.

Run explicitly: python -m pytest audit/acceptance_probes.py -q --tb=short
Assertions describe required behavior. Failures reproduce gaps in the delivery.
All external services and patient records below are synthetic.
"""
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from recepcion.agent import Reception
from recepcion.api import app
from recepcion.backend import Backend
from recepcion.chatwoot import Chatwoot
from recepcion.message_contracts import InboundMessageCreate
from recepcion.store import Store


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def settings():
    return SimpleNamespace(
        odonto_backend_url="http://backend", odonto_agent_token=SecretStr("agent"),
        odonto_inbound_token=SecretStr("inbound"), odonto_delivery_token=SecretStr("delivery"),
        odonto_operator_token=SecretStr("operator"), reception_api_key=SecretStr("service"),
        chatwoot_url="http://chatwoot", chatwoot_token=SecretStr("chatwoot"),
        chatwoot_account_id=1, chatwoot_inbox_id=2, chatwoot_team_id=3,
        odonto_channel_external_id="audit-channel", media_allowed_origins=[],
    )


def inbound():
    return InboundMessageCreate(
        schema_version="1.0", provider="whatsapp", channel_account_external_id="audit-channel",
        provider_message_id="audit-1", external_contact_id="synthetic-contact",
        phone_e164="+51999000001", message_type="text", text="Quisiera conocer un horario disponible",
        occurred_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "audit.db")
    yield value
    value.close()


@pytest.mark.parametrize("operation", ["appointment", "reschedule"])
async def test_confirmation_is_blocked_until_a_later_inbound(store, operation):
    """A misbehaving model must not bypass the second-message boundary."""
    token = "00000000-0000-4000-8000-000000000001"
    proposed = {"full_name": "Paciente Ficticio", "service_id": 1, "location_id": 1,
                "practitioner_id": 1, "start": "2026-11-02T10:00:00-05:00"}
    if operation == "reschedule":
        proposed = {"appointment_id": 1, "new_start": "2026-11-02T10:00:00-05:00"}
    calls = []
    def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith("inbound"):
            calls.append("inbound")
            return httpx.Response(201, json={"message_id": 81, "conversation_id": 12})
        if request.url.path.endswith("outbound"):
            return httpx.Response(201, json={"outbound_id": 99})
        name = body["tool_name"]
        calls.append(name)
        data = {"proposal": {"id": 4, "confirmation_token": token,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()}}
        return httpx.Response(200, json={"status": "success", "data": data})
    model = ScriptedModel(responses=[
        AIMessage(content="", tool_calls=[{"name": "propose_" + operation, "args": proposed, "id": "p"}]),
        AIMessage(content="", tool_calls=[{"name": "confirm_" + operation,
            "args": {"proposal_id": 4, "confirmation_token": token}, "id": "c"}]),
        AIMessage(content="La operación fue confirmada."),
    ])
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(handle)) as client:
        await Reception(Backend(settings(), store, client), model, store).receive(inbound())
    assert calls.count("inbound") == 1
    assert "confirm_" + operation not in calls, f"Forwarded in a single turn: {calls}"


async def test_status_route_reads_sqlite_from_valid_thread(store, monkeypatch):
    monkeypatch.setattr(app.state, "settings", settings(), raising=False)
    monkeypatch.setattr(app.state, "reception", SimpleNamespace(store=store), raising=False)
    monkeypatch.setattr(app.state, "chatwoot", None, raising=False)
    async with httpx.AsyncClient(base_url="http://service", transport=httpx.ASGITransport(app=app)) as client:
        response = await client.get("/status", headers={"Authorization": "Bearer service"})
    assert response.status_code == 200


async def test_connection_refusal_remains_retryable(store):
    store.route(12, 20)
    store.own_outbound(99, 12, "reply", 81)
    results = []
    def backend_handle(request):
        if request.url.path.endswith("claim"):
            return httpx.Response(200, json=[{"outbound_id": 99, "conversation_id": 12,
                "payload": {"channel_account_external_id": "audit-channel", "text": "Hola"}}])
        results.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "dead_letter"})
    def cw_handle(request):
        raise httpx.ConnectError("Connection refused before sending request", request=request)
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(backend_handle)) as backend_client:
        async with httpx.AsyncClient(base_url="http://chatwoot", transport=httpx.MockTransport(cw_handle)) as cw_client:
            adapter = Chatwoot(settings(), SimpleNamespace(backend=Backend(settings(), store, backend_client)), store, cw_client)
            await adapter.dispatch()
    assert results[0]["outcome"] == "transient_failure", results


async def test_partial_handoff_retries_team_assignment(store):
    store.enqueue("audit-1", 20, inbound().model_dump(mode="json"), 0)
    labels = set()
    assignments = []
    class SyntheticBackend:
        async def post(self, *args):
            return {"conversation_id": 12, "message_id": 81}
    async def receive(message):
        return {"conversation_id": 12, "handoff": True}
    def cw_handle(request):
        if request.url.path.endswith("/assignments"):
            assignments.append(1)
            return httpx.Response(503, json={"error": "temporary"})
        if request.url.path.endswith("/labels"):
            if request.method == "POST":
                labels.update(json.loads(request.content)["labels"])
            return httpx.Response(200, json={"payload": sorted(labels)})
        return httpx.Response(200, json={"labels": sorted(labels)})
    reception = SimpleNamespace(backend=SyntheticBackend(), receive=receive)
    async with httpx.AsyncClient(base_url="http://chatwoot", transport=httpx.MockTransport(cw_handle)) as client:
        adapter = Chatwoot(settings(), reception, store, client)
        await adapter.process()
        assert assignments == [1]
        store.db.execute("UPDATE inbox SET due=0")
        store.db.commit()
        await adapter.process()
    state = store.db.execute("SELECT state FROM inbox").fetchone()[0]
    assert len(assignments) == 2, f"Assignment attempted once, labels={labels}, inbox state={state}"


async def test_old_automated_reply_is_suppressed_after_human_pause(store):
    store.route(12, 20)
    store.own_outbound(99, 12, "reply", 81)
    sent = []
    def backend_handle(request):
        if request.url.path.endswith("claim"):
            return httpx.Response(200, json=[{"outbound_id": 99, "conversation_id": 12,
                "payload": {"channel_account_external_id": "audit-channel", "text": "Old automatic answer"}}])
        return httpx.Response(200, json={"status": "sent"})
    def cw_handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"labels": ["apagado", "atencion-humana"]})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": 400})
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(backend_handle)) as backend_client:
        async with httpx.AsyncClient(base_url="http://chatwoot", transport=httpx.MockTransport(cw_handle)) as cw_client:
            adapter = Chatwoot(settings(), SimpleNamespace(backend=Backend(settings(), store, backend_client)), store, cw_client)
            await adapter.dispatch()
    assert not sent, f"Sent despite paused conversation: {sent}"
