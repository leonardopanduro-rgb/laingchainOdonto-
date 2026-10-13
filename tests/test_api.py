from types import SimpleNamespace

import httpx
from pydantic import SecretStr

from recepcion.api import app
from recepcion.config import Settings


def test_example_optional_empty_settings_load(monkeypatch):
    for key in ("ODONTO_INBOUND_TOKEN", "ODONTO_AGENT_TOKEN", "RECEPTION_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(key, "test-secret")
    settings = Settings(_env_file=".env.example")
    assert settings.chatwoot_team_id is None
    assert settings.chatwoot_url is None


async def test_api_requires_service_authentication():
    app.state.settings = SimpleNamespace(reception_api_key=SecretStr("secret"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/messages", json={"schema_version": "1.0", "provider": "test",
            "channel_account_external_id": "lab", "provider_message_id": "1", "external_contact_id": "1",
            "phone_e164": "+51999000001", "message_type": "text", "text": "Hola",
            "occurred_at": "2026-10-13T14:00:00-05:00"})
    assert response.status_code == 401


async def test_webhook_rejects_wrong_secret():
    app.state.chatwoot = SimpleNamespace(settings=SimpleNamespace(chatwoot_webhook_secret=SecretStr("secret")))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/chatwoot?key=incorrect", json={})
    assert response.status_code == 401


async def test_lifespan_starts_with_configured_model_and_all_tool_schemas(tmp_path, monkeypatch):
    from recepcion import api
    from recepcion.agent import tools_for, Turn
    config = Settings(_env_file=None, odonto_inbound_token='test-inbound', odonto_agent_token='test-agent',
        reception_api_key='test-api', google_api_key='synthetic-key-no-network', reception_db=str(tmp_path / 'startup.db'))
    monkeypatch.setattr(api, 'Settings', lambda: config)
    async with app.router.lifespan_context(app):
        reception = app.state.reception
        tools = tools_for(reception.backend, Turn(12, 81, 'startup'))
        assert len(tools) == 15
        reception.model.bind_tools(tools)  # Validate Gemini tool schema conversion without calling Google.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://service') as client:
            assert (await client.get('/health')).json()['orchestrator'] == 'langchain'
            status = await client.get('/status', headers={'Authorization': 'Bearer test-api'})
            assert status.status_code == 200
            assert status.json()['pending_human_assignments'] == 0
