"""Real FastAPI + PostgreSQL + LangChain; only LLM and Chatwoot are synthetic."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json

import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import SecretStr
from sqlalchemy import text

from conftest import TEST_INTEGRATION_TOKEN
from test_messaging_phase2 import _app_for
from test_agent_booking_phase4 import _seed_booking_conversation
from recepcion.agent import Reception
from recepcion.backend import Backend
from recepcion.chatwoot import Chatwoot
from recepcion.message_contracts import InboundMessageCreate
from recepcion.store import Store


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs): return self


def script(model, name, args):
    model.i = 0
    model.responses = [AIMessage(content='', tool_calls=[{'name': name, 'args': args, 'id': name}]),
                       AIMessage(content='Respuesta de prueba')]


async def test_reception_lifecycle_with_real_postgresql(migrated_engine, session, tmp_path):
    seeded = _seed_booking_conversation(session, suffix='langchain', phone='+51999000001')
    token = SecretStr(TEST_INTEGRATION_TOKEN)
    settings = SimpleNamespace(odonto_agent_token=token, odonto_inbound_token=token,
        odonto_delivery_token=token, odonto_operator_token=token, chatwoot_account_id=1,
        chatwoot_team_id=3, odonto_channel_external_id='wa-langchain')
    now = datetime.now(timezone(timedelta(hours=-5)))
    start = (now + timedelta(days=(7 - now.weekday()) % 7 or 7)).replace(hour=9, minute=0, second=0, microsecond=0)
    store = Store(tmp_path / 'integration.db')
    model = ScriptedModel(responses=[AIMessage(content='Hola')])
    cw_labels, cw_messages = set(), []
    def chatwoot(request):
        if request.url.path.endswith('/messages'):
            cw_messages.append(json.loads(request.content))
            return httpx.Response(200, json={'id': 1000 + len(cw_messages)})
        if request.url.path.endswith('/labels'):
            if request.method == 'POST':
                cw_labels.clear()
                cw_labels.update(json.loads(request.content)['labels'])
            return httpx.Response(200, json={'payload': sorted(cw_labels)})
        return httpx.Response(200, json={'labels': sorted(cw_labels)})
    async with httpx.AsyncClient(base_url='http://backend', transport=httpx.ASGITransport(app=_app_for(migrated_engine))) as client:
        async with httpx.AsyncClient(base_url='http://chatwoot', transport=httpx.MockTransport(chatwoot)) as cw_client:
            reception = Reception(Backend(settings, store, client), model, store)
            adapter = Chatwoot(settings, reception, store, cw_client)
            sequence = 0
            async def send(content):
                nonlocal sequence
                sequence += 1
                incoming = InboundMessageCreate(schema_version='1.0', provider='whatsapp',
                    channel_account_external_id='wa-langchain', provider_message_id=f'lc-{sequence}',
                    external_contact_id='contact-langchain', phone_e164='+51999000001', message_type='text',
                    text=content, occurred_at=datetime.now(timezone.utc))
                reply = await reception.receive(incoming)
                store.route(reply['conversation_id'], 20)
                await adapter.dispatch()
                assert (await reception.receive(incoming))['duplicate']
                return reply

            script(model, 'register_contact_profile', {'full_name': 'Paciente Sintético'})
            await send('Me llamo Paciente Sintético')
            script(model, 'query_available_slots', {'service_id': seeded['service'].id,
                'location_id': seeded['location'].id, 'window_start': start.isoformat(),
                'window_end': (start + timedelta(hours=3)).isoformat()})
            await send('¿Qué horarios tienen el lunes?')
            checkpoint = session.execute(text("SELECT payload->'_reception_state' FROM outbound_messages ORDER BY id DESC LIMIT 1")).scalar_one()
            assert checkpoint['phase'] == 'awaiting_slot' and len(checkpoint['offered_slots']) == 3
            session.commit()

            script(model, 'propose_appointment', {'full_name': 'Paciente Sintético',
                'service_id': seeded['service'].id, 'location_id': seeded['location'].id,
                'practitioner_id': seeded['practitioner'].id, 'start': start.isoformat()})
            assert 'Sí, confirmo' in (await send('Quiero la cita de las nueve'))['reply']
            assert session.execute(text('SELECT count(*) FROM appointments')).scalar_one() == 0
            session.commit()
            assert 'reservada' in (await send('Sí, confirmo'))['reply']
            appointment = session.execute(text('SELECT id,state,start_utc FROM appointments')).one()
            assert appointment.state == 'confirmed' and appointment.start_utc == start
            session.commit()

            script(model, 'propose_reschedule', {'appointment_id': appointment.id, 'new_start': (start + timedelta(hours=1)).isoformat()})
            assert 'Sí, confirmo' in (await send('Quiero cambiarla a las diez'))['reply']
            assert 'reprogramada' in (await send('Sí, confirmo'))['reply']
            assert session.execute(text('SELECT start_utc FROM appointments')).scalar_one() == start + timedelta(hours=1)
            session.commit()

            script(model, 'propose_cancellation', {'appointment_id': appointment.id})
            assert 'Sí, confirmo' in (await send('Quiero cancelar mi cita'))['reply']
            assert 'cancelada' in (await send('Sí, confirmo'))['reply']
            assert session.execute(text('SELECT state FROM appointments')).scalar_one() == 'cancelled'
            session.commit()

            result = await send('Quiero hablar con una persona')
            assert result['handoff']
            await adapter.sync_handoff(20)
            assert cw_labels == {'apagado', 'atencion-humana'}
            assert session.execute(text('SELECT status FROM conversations')).scalar_one() == 'human_handoff'
            session.commit()
            await adapter.resume(20, 'operator-note-1')
            assert not cw_labels
            assert session.execute(text('SELECT status FROM conversations')).scalar_one() == 'open'
            assert session.execute(text("SELECT count(*) FROM outbound_messages WHERE status!='sent'")).scalar_one() == 0
            assert session.execute(text('SELECT count(*) FROM appointments')).scalar_one() == 1
            assert len(cw_messages) == sequence
    store.close()
