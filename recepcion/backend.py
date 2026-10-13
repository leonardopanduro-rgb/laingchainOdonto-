import json
from hashlib import sha256
from uuid import uuid4

import httpx


class Backend:
    def __init__(self, settings, store, client=None):
        self.settings, self.store = settings, store
        self.client = client or httpx.AsyncClient(base_url=settings.odonto_backend_url, timeout=30)

    async def post(self, path, body, token, key):
        identifier, cached = self.store.call(key)
        if cached is not None:
            return cached
        request_id, correlation_id = str(uuid4()), str(uuid4())
        if path == "/agent-tools/call":
            body = {**body, "request_id": request_id, "correlation_id": correlation_id,
                    "idempotency_key": identifier if body["tool_version"] == "1.1" else None}
        response = await self.client.post(path, json=body, headers={
            "Authorization": "Bearer " + token.get_secret_value(),
            "Idempotency-Key": identifier, "X-Request-Id": request_id,
            "X-Correlation-Id": correlation_id,
        })
        response.raise_for_status()
        result = response.json()
        # Fresh reads reflect handoff, current availability and confirmation state.
        if path != "/agent-tools/call" or (body["tool_version"] == "1.1" and result.get("status") == "success"):
            self.store.settle(key, result)
        return result

    async def tool(self, name, arguments, turn, mutation=False):
        arguments = {**arguments}
        if name in {"propose_cancellation", "confirm_cancellation"}:
            arguments["source_message_id"] = turn.message_id
        digest = sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()
        return await self.post("/agent-tools/call", {
            "tool_version": "1.1" if mutation else "1.0", "tool_name": name,
            "conversation_id": turn.conversation_id, "arguments": arguments,
        }, self.settings.odonto_agent_token, f"tool:{turn.conversation_id}:{turn.message_id}:{turn.event_key}:{name}:{digest}")
