import json
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from recepcion.chatwoot import Chatwoot, normalize
from recepcion.backend import Backend
from recepcion.store import Store


def settings():
    return SimpleNamespace(chatwoot_account_id=1, chatwoot_inbox_id=2, chatwoot_url="http://chatwoot",
        chatwoot_token=SecretStr("cw"), chatwoot_team_id=3, odonto_channel_external_id="whatsapp-test",
        odonto_delivery_token=SecretStr("delivery"), odonto_operator_token=SecretStr("operator"),
        odonto_agent_token=SecretStr("agent"), odonto_inbound_token=SecretStr("inbound"),
        odonto_backend_url="http://backend", media_allowed_origins=[])


def event():
    return {"event": "message_created", "id": 100, "account": {"id": 1},
        "conversation": {"id": 20, "inbox_id": 2, "labels": []},
        "sender": {"id": 30, "phone_number": "+51999000001"},
        "message_type": "incoming", "content": "Hola", "created_at": 1791900000}


@pytest.mark.parametrize("change", ["outgoing", "private", "account", "inbox", "paused", "event"])
def test_ignored_events(change):
    body = event()
    if change == "outgoing": body["message_type"] = "outgoing"
    if change == "private": body["private"] = True
    if change == "account": body["account"]["id"] = 99
    if change == "inbox": body["conversation"]["inbox_id"] = 99
    if change == "paused": body["conversation"]["labels"] = ["apagado"]
    if change == "event": body["event"] = "conversation_updated"
    assert normalize(body, settings()) is None


def test_contact_has_real_phone_and_preserves_old_provider_ids():
    body = event()
    route, inbound = normalize(body, settings())
    assert route == 20
    assert inbound.provider_message_id == "100"
    body["sender"]["phone_number"] = ""
    with pytest.raises(ValueError): normalize(body, settings())


def test_debounce_survives_restart_and_duplicates_do_not_extend_window(tmp_path):
    path = tmp_path / "queue.db"
    first = Store(path)
    first.enqueue("1", 20, {"text": "Hola"}, 10)
    first.enqueue("2", 20, {"text": "Quiero cita"}, 15)
    first.enqueue("2", 20, {"text": "Quiero cita"}, 100)
    first.close()
    second = Store(path)
    assert not second.pending(14)
    assert len(second.pending(16)) == 2
    second.close()


async def test_uncertain_delivery_is_not_sent_twice(tmp_path):
    store = Store(tmp_path / "delivery.db")
    store.route(12, 20)
    store.own_outbound(99, 12, "reply", 81)
    sends = []
    settlements = []
    item = {"outbound_id": 99, "conversation_id": 12, "payload": {"channel_account_external_id": "whatsapp-test", "text": "Hola"}}
    def backend_handle(request):
        if request.url.path.endswith("claim"):
            return httpx.Response(200, json=[item])
        settlements.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "dead_letter"})
    def chatwoot_handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"labels": []})
        sends.append(request)
        raise httpx.ReadTimeout("Response lost after possible send")
    async with httpx.AsyncClient(base_url="http://backend", transport=httpx.MockTransport(backend_handle)) as backend_client:
        async with httpx.AsyncClient(base_url="http://chatwoot", transport=httpx.MockTransport(chatwoot_handle)) as cw_client:
            backend = Backend(settings(), store, backend_client)
            adapter = Chatwoot(settings(), SimpleNamespace(backend=backend), store, cw_client)
            await adapter.dispatch()
            await adapter.dispatch()
    assert len(sends) == 1
    assert settlements[0]["error_code"] == "delivery_uncertain"
    store.close()


async def test_audio_from_unknown_origin_never_downloaded(tmp_path):
    store = Store(tmp_path / "audio.db")
    async with httpx.AsyncClient(base_url="http://chatwoot") as client:
        adapter = Chatwoot(settings(), None, store, client)
        with pytest.raises(ValueError, match="no autorizado"):
            await adapter.transcribe("x", {"data_url": "http://169.254.169.254/metadata"})
    store.close()
