"""A dispatcher may lease only the messages its adapter owns."""
from fastapi.testclient import TestClient
from sqlalchemy import text
import pytest

from test_messaging_phase2 import _app_for, _seed_channel, _inbound, _idempotency_headers


def test_claim_selection_preserves_unselected_jobs_and_expired_leases(migrated_engine, session):
    _seed_channel(session)
    with TestClient(_app_for(migrated_engine)) as client:
        receipt = client.post('/internal/messages/inbound', json=_inbound(), headers=_idempotency_headers())
        conversation = receipt.json()['conversation_id']
        ids = []
        for label in ['owned', 'other adapter', 'other expired lease']:
            response = client.post(f'/internal/conversations/{conversation}/outbound',
                json={'text': label}, headers=_idempotency_headers())
            assert response.status_code == 201, response.text
            ids.append(response.json()['outbound_id'])
        session.execute(text("UPDATE outbound_messages SET status='processing',attempt_count=3,next_attempt_at=now()-interval '1 hour' WHERE id=:id"), {'id': ids[2]})
        session.commit()
        result = client.post('/internal/outbound/claim', json={'limit': 50, 'outbound_ids': [ids[0]]}, headers=_idempotency_headers())
        assert result.status_code == 200, result.text
        assert [row['outbound_id'] for row in result.json()] == [ids[0]]
        rows = session.execute(text('SELECT id,status,attempt_count FROM outbound_messages ORDER BY id')).all()
        assert rows == [(ids[0], 'processing', 1), (ids[1], 'pending', 0), (ids[2], 'processing', 3)]
        session.commit()
        expired = client.post('/internal/outbound/claim', json={'outbound_ids': [ids[2]]}, headers=_idempotency_headers())
        assert expired.status_code == 200 and expired.json() == []
        assert session.execute(text('SELECT status FROM outbound_messages WHERE id=:id'), {'id': ids[2]}).scalar_one() == 'dead_letter'


@pytest.mark.parametrize('ids', [[], [0], [-1], [True], ['1'], list(range(1, 52))])
def test_invalid_selection_is_rejected_without_widening(migrated_engine, ids):
    with TestClient(_app_for(migrated_engine)) as client:
        response = client.post('/internal/outbound/claim', json={'outbound_ids': ids}, headers=_idempotency_headers())
    assert response.status_code == 422


def test_nonexistent_selection_does_not_claim_other_messages(migrated_engine, session):
    _seed_channel(session)
    with TestClient(_app_for(migrated_engine)) as client:
        receipt = client.post('/internal/messages/inbound', json=_inbound(), headers=_idempotency_headers())
        conversation = receipt.json()['conversation_id']
        client.post(f'/internal/conversations/{conversation}/outbound', json={'text': 'untouched'}, headers=_idempotency_headers())
        response = client.post('/internal/outbound/claim', json={'outbound_ids': [999999]}, headers=_idempotency_headers())
    assert response.status_code == 200 and response.json() == [], response.text
    assert session.execute(text('SELECT status,attempt_count FROM outbound_messages')).one() == ('pending', 0)


def test_selected_ids_remain_bound_to_authenticated_tenant(migrated_engine, session):
    from uuid import uuid4
    from app.context import default_context
    from app.organization.service import create_organization
    from app.messaging.schemas import InboundMessageCreate
    from app.messaging.service import ingest_inbound_message, enqueue_outbound_message

    other = create_organization(session, 'Other clinic').id
    _seed_channel(session, organization_id=other, external_id='other-channel')
    incoming = ingest_inbound_message(session,
        InboundMessageCreate(**_inbound(external_account_id='other-channel')), ctx=default_context(other))
    outgoing = enqueue_outbound_message(session, conversation_id=incoming.conversation_id,
        text_body='Other clinic message', idempotency_key=str(uuid4()), ctx=default_context(other))
    with TestClient(_app_for(migrated_engine)) as client:
        response = client.post('/internal/outbound/claim', json={'outbound_ids': [outgoing.outbound_id]}, headers=_idempotency_headers())
    assert response.status_code == 200 and response.json() == []
    assert session.execute(text('SELECT status,attempt_count FROM outbound_messages')).one() == ('pending', 0)
