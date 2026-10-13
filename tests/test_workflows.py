"""Executable acceptance scenarios; real LangChain loop, synthetic providers."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from langchain_core.messages import AIMessage

from recepcion.agent import Reception, Turn, tools_for
from recepcion.backend import Backend
from recepcion.policy import confirmation_allowed
from test_reception import ToolModel, settings, message, store

TOKEN = '00000000-0000-4000-8000-000000000001'


@pytest.mark.parametrize('operation,action,verb', [
    ('appointment', 'BOOK', 'reservada'), ('cancellation', 'CANCEL', 'cancelada'),
    ('reschedule', 'RESCHEDULE', 'reprogramada')])
async def test_propose_present_then_confirm_once_and_recover_checkpoint(store, operation, action, verb):
    future = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0).isoformat()
    args = {'appointment_id': 1}
    if operation == 'appointment':
        args = dict(full_name='Paciente Ficticio', service_id=1, location_id=1, practitioner_id=1, start=future)
    if operation == 'reschedule':
        args['new_start'] = future
    pending, calls, checkpoints, inputs = {}, [], [], {}
    proposal = {**args, 'id': 4, 'confirmation_token': TOKEN,
        'expires_at': (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()}
    def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith('inbound'):
            inputs.setdefault(body['provider_message_id'], len(inputs) + 80)
            return httpx.Response(201, json={'message_id': inputs[body['provider_message_id']], 'conversation_id': 12})
        if request.url.path.endswith('outbound'):
            checkpoints.append(body['reception_state'])
            return httpx.Response(201, json={'outbound_id': 98 + len(checkpoints)})
        name = body['tool_name']
        calls.append(name)
        data = {}
        if name == 'get_reception_context':
            data = {'pending_action': pending or None, 'reception_state': checkpoints[-1] if checkpoints else None,
                'services': [{'id': 1, 'name': 'Limpieza'}], 'locations': [{'id': 1, 'name': 'Sede Lima'}],
                'appointments': [{'id': 1, 'start': future}]}
        elif name.startswith('propose_'):
            data = {'proposal': proposal}
            pending.update(action_type=action, proposal_id=4, confirmation_token=TOKEN)
        elif name.startswith('confirm_'):
            if operation == 'cancellation':
                assert body['arguments']['source_message_id'] == 81
            data = {'appointment': {'id': 1, 'start': future}}
        return httpx.Response(200, json={'status': 'success', 'data': data})
    model = ToolModel(responses=[AIMessage(content='', tool_calls=[{'name': 'propose_' + operation, 'args': args, 'id': 'p'}]),
        AIMessage(content='Texto que será sustituido por el resumen verificable.')])
    async with httpx.AsyncClient(base_url='http://backend', transport=httpx.MockTransport(handle)) as client:
        reception = Reception(Backend(settings(), store, client), model, store)
        first = await reception.receive(message())
        assert 'Sí, confirmo' in first['reply']
        assert checkpoints[0]['phase'] == 'awaiting_confirmation'
        assert 'confirm_' + operation not in calls
        yes = message().model_copy(update={'provider_message_id': 'second', 'text': 'Sí, confirmo',
            'occurred_at': datetime.now(timezone.utc) + timedelta(seconds=1)})
        second = await reception.receive(yes)
        assert verb in second['reply']
        assert checkpoints[1]['phase'] == 'completed'
        await reception.receive(yes)
        assert calls.count('confirm_' + operation) == 1


@pytest.mark.parametrize('fault', ['not_presented', 'expired', 'same_message', 'before_delivery', 'conditional_yes', 'wrong_token', 'other_action', 'different_named_action', 'newer_proposal', 'newer_negative'])
def test_confirmation_rejects_unproven_or_ambiguous_consent(store, fault):
    now = datetime.now(timezone.utc)
    proposal = {'id': 4, 'expires_at': (now + timedelta(minutes=15)).isoformat()}
    if fault == 'expired': proposal['expires_at'] = (now - timedelta(minutes=1)).isoformat()
    store.register_proposal(12, 'BOOK', proposal, 80, 99)
    if fault != 'not_presented': store.presented(99, now.isoformat())
    turn = Turn(12, 81, 'yes', text='Sí, confirmo', occurred_at=now + timedelta(seconds=1),
        context={'pending_action': {'action_type': 'BOOK', 'proposal_id': 4, 'confirmation_token': TOKEN}})
    if fault == 'same_message': turn.message_id = 80
    if fault == 'before_delivery': turn.occurred_at = now - timedelta(seconds=1)
    if fault == 'conditional_yes': turn.text = 'Sí, pero cambia la hora'
    if fault == 'different_named_action': turn.text = 'Confirmo la cancelación'
    if fault == 'other_action': turn.context['pending_action']['action_type'] = 'CANCEL'
    if fault == 'newer_proposal':
        store.register_proposal(12, 'CANCEL', {'id': 5, 'expires_at': proposal['expires_at']}, 81, 100)
        store.presented(100, now.isoformat())
    if fault == 'newer_negative':
        turn.context['recent_messages'] = [{'direction': 'inbound', 'text': 'No, no canceles',
            'occurred_at': (now + timedelta(seconds=2)).isoformat()}]
    token = 'different' if fault == 'wrong_token' else TOKEN
    assert not confirmation_allowed(store, turn, 'confirm_appointment', {'proposal_id': 4, 'confirmation_token': token})


@pytest.mark.parametrize('text,reason', [('Quiero hablar con una persona', 'requested_by_contact'),
    ('Tengo un reclamo', 'complaint'), ('Me duele una muela', 'clinical_case'), ('Tengo sangrado', 'urgent_symptoms')])
async def test_policy_handoff_does_not_depend_on_model_obedience(store, text, reason):
    calls = []
    def handle(request):
        body = json.loads(request.content)
        if request.url.path.endswith('inbound'): return httpx.Response(201, json={'message_id': 81, 'conversation_id': 12})
        if request.url.path.endswith('outbound'): return httpx.Response(201, json={'outbound_id': 99})
        calls.append(body)
        return httpx.Response(200, json={'status': 'success', 'data': {}})
    model = ToolModel(responses=[AIMessage(content='El modelo no debe utilizarse')])
    async with httpx.AsyncClient(base_url='http://backend', transport=httpx.MockTransport(handle)) as client:
        result = await Reception(Backend(settings(), store, client), model, store).receive(message().model_copy(update={'text': text}))
    assert result['handoff'] and 'humana' in result['reply']
    assert calls[-1]['arguments']['reason_code'] == reason and model.i == 0


async def test_available_slots_are_checkpointed_without_internal_tokens(store):
    start = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    end = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    class B:
        async def tool(self, *args):
            return {'status': 'success', 'data': {'slots': [{'practitioner_id': 1, 'start': start}]}}
    turn = Turn(12, 81, 'slots')
    tool = {t.name: t for t in tools_for(B(), turn)}['query_available_slots']
    await tool.ainvoke(dict(service_id=1, location_id=1, window_start=start, window_end=end))
    assert turn.progress['offered_slots'] == [{'practitioner_id': 1, 'start': start}]
    assert turn.progress['phase'] == 'awaiting_slot'
