import asyncio
from types import SimpleNamespace
import httpx

from recepcion.chatwoot import Chatwoot
from recepcion.store import Store
from test_chatwoot import settings


async def test_resume_serializes_with_selected_handoff_recovery(tmp_path):
    store = Store(tmp_path / 'race.db')
    store.route(12, 20)
    store.handoff_job(20)
    started, release = asyncio.Event(), asyncio.Event()
    labels, events = set(), []
    class Backend:
        async def post(self, *args):
            events.append('backend-resumed')
            return {}
    async with httpx.AsyncClient(base_url='http://cw') as client:
        adapter = Chatwoot(settings(), SimpleNamespace(backend=Backend()), store, client)
        async def set_labels(conversation, paused):
            if paused:
                started.set()
                await release.wait()
                labels.update({'apagado', 'atencion-humana'})
                events.append('paused')
            else:
                labels.clear()
                events.append('unpaused')
        adapter.labels = set_labels
        recovery = asyncio.create_task(adapter.sync_handoff(20, recovery=True))
        await started.wait()
        resume = asyncio.create_task(adapter.resume(20, 'note-1'))
        release.set()
        await asyncio.gather(recovery, resume)
        # A job selected before resume must not be resurrected afterwards.
        await adapter.sync_handoff(20, recovery=True)
    assert not labels and events == ['paused', 'backend-resumed', 'unpaused']
    assert store.db.execute('SELECT count(*) FROM handoff_jobs').fetchone()[0] == 0
    store.close()
