import json
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

import httpx
import pytest
from langchain_core.messages import AIMessage

from recepcion.agent import Reception
from recepcion.backend import Backend
from recepcion.chatwoot import Chatwoot
from recepcion.store import Store
from test_reception import ToolModel, message
from test_chatwoot import settings


async def test_committed_handoff_with_lost_response_recovers_after_restart(tmp_path):
    path = tmp_path / 'handoff.db'
    handoffs, contexts = [], []
    def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith('inbound'): return httpx.Response(201, json={'message_id': 81, 'conversation_id': 12})
        if request.url.path.endswith('outbound'): return httpx.Response(201, json={'outbound_id': 99})
        if body['tool_name'] == 'request_human_handoff':
            handoffs.append(request.headers['Idempotency-Key'])
            if len(handoffs) == 1: raise httpx.ReadTimeout('Committed; response lost', request=request)
            return httpx.Response(200, json={'status': 'success', 'data': {'handoff': {'id': 1}}, 'replayed': True})
        contexts.append(body)
        return httpx.Response(200, json={'status': 'error' if handoffs else 'success', 'data': {}})
    model = ToolModel(responses=[AIMessage(content='No debe usarse')])
    incoming = message().model_copy(update={'text': 'Quiero hablar con una persona'})
    async with httpx.AsyncClient(base_url='http://backend', transport=httpx.MockTransport(handle)) as client:
        store = Store(path)
        with pytest.raises(httpx.ReadTimeout):
            await Reception(Backend(settings(), store, client), model, store).receive(incoming)
        store.close()
        store = Store(path)
        reply = await Reception(Backend(settings(), store, client), model, store).receive(incoming)
        assert reply['handoff'] and len(contexts) == 1
        assert len(handoffs) == 2 and handoffs[0] == handoffs[1]
        store.close()


async def test_lost_delivery_ack_is_replayed_without_reclaim_or_resend(tmp_path):
    store = Store(tmp_path / 'delivery.db')
    store.route(12, 20)
    store.own_outbound(99, 12, 'reply', 81)
    settled, sent, claims = [], [], []
    item = {'outbound_id': 99, 'conversation_id': 12, 'attempt_count': 1,
            'payload': {'channel_account_external_id': 'whatsapp-test', 'text': 'Hola'}}
    def backend(request):
        if request.url.path.endswith('claim'):
            claims.append(json.loads(request.content))
            return httpx.Response(200, json=[] if settled else [item])
        settled.append(request.headers['Idempotency-Key'])
        if len(settled) == 1: raise httpx.ReadTimeout('Committed result; response lost', request=request)
        return httpx.Response(200, json={'status': 'sent'})
    def cw(request):
        if request.method == 'GET': return httpx.Response(200, json={'labels': []})
        sent.append(request)
        return httpx.Response(200, json={'id': 400})
    async with httpx.AsyncClient(base_url='http://backend', transport=httpx.MockTransport(backend)) as bc:
        async with httpx.AsyncClient(base_url='http://cw', transport=httpx.MockTransport(cw)) as cc:
            adapter = Chatwoot(settings(), SimpleNamespace(backend=Backend(settings(), store, bc)), store, cc)
            await adapter.dispatch()
            await adapter.dispatch()
    assert len(sent) == 1 and len(claims) == 1
    assert len(settled) == 2 and settled[0] == settled[1]
    assert store.owned_ids() == []
    store.close()


def test_terminal_remote_leases_cannot_starve_new_messages(tmp_path):
    store = Store(tmp_path / 'rotation.db')
    for i in range(1, 102): store.own_outbound(i, 12, 'reply', i)
    selected = set(store.owned_ids()) | set(store.owned_ids()) | set(store.owned_ids())
    assert selected == set(range(1, 102))
    store.close()


async def test_safe_connection_retry_sends_on_second_lease(tmp_path):
    store = Store(tmp_path / 'retry.db')
    store.route(12, 20)
    store.own_outbound(99, 12, 'reply', 81)
    attempts, results, sent = [], [], []
    def backend(request):
        if request.url.path.endswith('claim'):
            attempts.append(1)
            return httpx.Response(200, json=[{'outbound_id': 99, 'conversation_id': 12, 'attempt_count': len(attempts),
                'payload': {'channel_account_external_id': 'whatsapp-test', 'text': 'Hola'}}])
        results.append((json.loads(request.content), request.headers['Idempotency-Key']))
        return httpx.Response(200, json={'status': 'failed' if len(results) == 1 else 'sent'})
    def cw(request):
        if request.method == 'GET': return httpx.Response(200, json={'labels': []})
        sent.append(1)
        if len(sent) == 1: raise httpx.ConnectError('Not connected', request=request)
        return httpx.Response(200, json={'id': 400})
    async with httpx.AsyncClient(base_url='http://backend', transport=httpx.MockTransport(backend)) as bc:
        async with httpx.AsyncClient(base_url='http://cw', transport=httpx.MockTransport(cw)) as cc:
            adapter = Chatwoot(settings(), SimpleNamespace(backend=Backend(settings(), store, bc)), store, cc)
            await adapter.dispatch()
            await adapter.dispatch()
    assert [r[0]['outcome'] for r in results] == ['transient_failure', 'sent']
    assert results[0][1] != results[1][1] and len(sent) == 2
    store.close()


async def test_audio_download_transcribe_and_cache_without_token(tmp_path):
    store = Store(tmp_path / 'audio.db')
    downloads = []
    def media(request):
        assert 'api_access_token' not in request.headers and 'authorization' not in request.headers
        downloads.append(request)
        return httpx.Response(200, content=b'fake audio bytes', headers={'content-type': 'audio/ogg'})
    model = ToolModel(responses=[AIMessage(content='Quisiera una limpieza')])
    async with httpx.AsyncClient(base_url='http://cw') as cc:
        adapter = Chatwoot(settings(), SimpleNamespace(model=model), store, cc, httpx.MockTransport(media))
        assert await adapter.transcribe('1', {'data_url': 'http://chatwoot/audio'}) == 'Quisiera una limpieza'
        assert await adapter.transcribe('1', {'data_url': 'http://chatwoot/audio'}) == 'Quisiera una limpieza'
    assert len(downloads) == 1
    store.close()


async def test_older_confirmation_is_ignored_when_newer_inbound_exists(tmp_path):
    store = Store(tmp_path / 'old.db')
    calls = []
    def backend(request):
        if request.url.path.endswith('inbound'): return httpx.Response(201, json={'message_id': 82, 'conversation_id': 12})
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'success', 'data': {'conversation': {'latest_inbound_message_id': 81}}})
    model = ToolModel(responses=[AIMessage(content='No debe responder')])
    async with httpx.AsyncClient(base_url='http://backend', transport=httpx.MockTransport(backend)) as bc:
        reply = await Reception(Backend(settings(), store, bc), model, store).receive(message().model_copy(update={'text': 'Sí, confirmo'}))
    assert reply['ignored'] and reply['outbound_id'] is None
    assert [x['tool_name'] for x in calls] == ['get_reception_context']
    store.close()


def test_last_presented_proposal_uses_delivery_order_not_creation_order(tmp_path):
    store = Store(tmp_path / 'order.db')
    now = datetime.now(timezone.utc)
    for action, pid, source, outgoing in [('BOOK', 1, 80, 99), ('CANCEL', 2, 81, 100)]:
        store.register_proposal(12, action, {'id': pid, 'expires_at': (now + timedelta(minutes=15)).isoformat()}, source, outgoing)
    store.presented(100, now.isoformat())
    store.presented(99, (now + timedelta(seconds=5)).isoformat())
    assert store.latest_presented_proposal(12) == ('BOOK', 1)
    store.close()


async def test_new_fragment_does_not_discard_unfinished_handoff(tmp_path):
    store = Store(tmp_path / 'fragments.db')
    received = []
    first = message().model_copy(update={'provider_message_id': '1', 'text': 'Quiero una persona'})
    second = message().model_copy(update={'provider_message_id': '2', 'text': 'Por favor'})
    key = json.dumps([first.provider, first.channel_account_external_id, '1'], separators=(',', ':'))
    store.save_reply('mutation:' + key, {'name': 'request_human_handoff'})
    store.enqueue('1', 20, first.model_dump(mode='json'), 0)
    store.enqueue('2', 20, second.model_dump(mode='json'), 0)
    class B:
        async def post(self, *args): return {'conversation_id': 12, 'message_id': 81}
    async def receive(incoming):
        received.append(incoming.provider_message_id)
        return {'conversation_id': 12, 'handoff': True}
    def cw(request): return httpx.Response(200, json={'labels': [], 'payload': []})
    async with httpx.AsyncClient(base_url='http://cw', transport=httpx.MockTransport(cw)) as cc:
        await Chatwoot(settings(), SimpleNamespace(backend=B(), receive=receive), store, cc).process()
    assert received == ['1']
    assert store.queue_counts() == {'done': 2}
    store.close()
