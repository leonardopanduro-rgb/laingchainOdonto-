import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from recepcion.agent import Reception, ReceptionUnavailable, Turn, tools_for
from recepcion.backend import Backend
from recepcion.message_contracts import InboundMessageCreate
from recepcion.store import Store


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def settings():
    return SimpleNamespace(odonto_backend_url="http://backend", odonto_agent_token=SecretStr("agent"),
                           odonto_inbound_token=SecretStr("inbound"))


def message():
    return InboundMessageCreate(schema_version="1.0", provider="test",
        channel_account_external_id="lab", provider_message_id="external-1",
        external_contact_id="contact-1", phone_e164="+51999000001", message_type="text",
        text="¿Qué servicios ofrecen?", occurred_at=datetime.now(timezone.utc))


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "reception.db")
    yield value
    value.close()


async def test_scope_trace_and_retry_key_survive_restart(tmp_path):
    path = tmp_path / "retry.db"
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["conversation_id"] == 12
        assert body["arguments"]["source_message_id"] == 81
        assert body["request_id"] == request.headers["X-Request-Id"]
        assert body["correlation_id"] == request.headers["X-Correlation-Id"]
        assert UUID(body["idempotency_key"]).version == 4
        if len(seen) == 1:
            raise httpx.ReadTimeout("Response lost")
        return httpx.Response(200, json={"status": "success", "data": {"proposal_id": 4}})
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(handle)) as client:
        first = Store(path)
        backend = Backend(settings(), first, client)
        with pytest.raises(httpx.ReadTimeout):
            await backend.tool("propose_cancellation", {"appointment_id": 2, "source_message_id": 999}, Turn(12,81,"x"), True)
        first.close()
        second = Store(path)
        backend = Backend(settings(), second, client)
        await backend.tool("propose_cancellation", {"appointment_id": 2}, Turn(12,81,"x"), True)
        await backend.tool("propose_cancellation", {"appointment_id": 2}, Turn(12,81,"x"), True)
        second.close()
    assert len(seen) == 2
    assert seen[0]["idempotency_key"] == seen[1]["idempotency_key"]


async def test_real_langchain_loop_and_duplicate_reply(store):
    seen = []
    def handle(request):
        body = json.loads(request.content)
        seen.append((request.url.path, body))
        if request.url.path.endswith("inbound"):
            return httpx.Response(201, json={"message_id": 81, "conversation_id": 12, "duplicate": False})
        if request.url.path.endswith("outbound"):
            assert body["text"] == "Ofrecemos limpieza dental."
            return httpx.Response(201, json={"outbound_id": 99})
        assert body["conversation_id"] == 12
        return httpx.Response(200, json={"status": "success", "data": {"services": [{"name": "Limpieza dental"}]}})
    model = ToolModel(responses=[AIMessage(content="", tool_calls=[
        {"name": "list_services", "args": {}, "id": "call1", "type": "tool_call"}]),
        AIMessage(content="Ofrecemos limpieza dental.")])
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(handle)) as client:
        reception = Reception(Backend(settings(), store, client), model, store)
        first = await reception.receive(message())
        count = len(seen)
        second = await reception.receive(message())
    assert first["reply"] == second["reply"]
    assert second["duplicate"]
    assert len(seen) == count
    assert any(body.get("tool_name") == "list_services" for _, body in seen)


async def test_backend_denial_prevents_model_and_outbound(store):
    def handle(request):
        if request.url.path.endswith("inbound"):
            return httpx.Response(201, json={"message_id": 81, "conversation_id": 12})
        return httpx.Response(200, json={"status": "error", "error": {"code": "forbidden"}})
    model = ToolModel(responses=[AIMessage(content="Nunca debe responder")])
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ReceptionUnavailable):
            await Reception(Backend(settings(), store, client), model, store).receive(message())
    assert model.i == 0


async def test_handoff_blocks_further_tools_and_source_id_hidden(store):
    calls = []
    class FakeBackend:
        async def tool(self, name, args, turn, mutation):
            calls.append(name)
            return {"status": "success", "data": {}}
    turn = Turn(12, 81, "x")
    tools = {tool.name: tool for tool in tools_for(FakeBackend(), turn)}
    assert "source_message_id" not in tools["confirm_cancellation"].args
    assert "conversation_id" not in tools["propose_appointment"].args
    assert "resume_automation" not in tools
    await tools["request_human_handoff"].ainvoke({"reason_code": "requested_by_contact", "reason_summary": "Solicita atención humana"})
    result = await tools["list_services"].ainvoke({})
    assert result["status"] == "error"
    assert calls == ["request_human_handoff"]


async def test_outbound_retry_reuses_draft_without_rerunning_model(store):
    outbounds = []
    def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith("inbound"):
            return httpx.Response(201, json={"message_id": 81, "conversation_id": 12})
        if request.url.path.endswith("outbound"):
            outbounds.append((body, request.headers["Idempotency-Key"]))
            if len(outbounds) == 1:
                raise httpx.ReadTimeout("Reply lost")
            return httpx.Response(201, json={"outbound_id": 99})
        return httpx.Response(200, json={"status": "success", "data": {}})
    model = ToolModel(responses=[AIMessage(content="Hola, ¿cómo puedo ayudarte?"), AIMessage(content="Texto distinto")])
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(handle)) as client:
        reception = Reception(Backend(settings(), store, client), model, store)
        with pytest.raises(httpx.ReadTimeout): await reception.receive(message())
        reply = await reception.receive(message())
    assert reply["reply"] == "Hola, ¿cómo puedo ayudarte?"
    assert outbounds[0] == outbounds[1]
