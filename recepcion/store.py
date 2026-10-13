import json
import sqlite3
from uuid import uuid4


class Store:
    """Durable retry keys and completed replies; contains personal data."""

    def __init__(self, path):
        self.claim_cursor = 0
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS calls (key TEXT PRIMARY KEY, id TEXT NOT NULL, result TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS replies (key TEXT PRIMARY KEY, result TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS routes (backend_id INTEGER PRIMARY KEY, chatwoot_id INTEGER NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS inbox (key TEXT PRIMARY KEY, chatwoot_id INTEGER, body TEXT, due REAL, attempts INTEGER DEFAULT 0, state TEXT DEFAULT 'pending')")
        self.db.execute("CREATE TABLE IF NOT EXISTS proposals (conversation_id INTEGER, action TEXT, proposal_id INTEGER, source_message_id INTEGER, expires_at TEXT, outbound_id INTEGER, presented_at TEXT, PRIMARY KEY(conversation_id,action,proposal_id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS owned_outbounds (id INTEGER PRIMARY KEY, conversation_id INTEGER, purpose TEXT NOT NULL, source_message_id INTEGER, terminal INTEGER DEFAULT 0)")
        self.db.execute("CREATE TABLE IF NOT EXISTS handoff_jobs (chatwoot_id INTEGER PRIMARY KEY, due REAL NOT NULL, attempts INTEGER DEFAULT 0)")
        self.db.execute("CREATE TABLE IF NOT EXISTS delivery_results (outbound_id INTEGER PRIMARY KEY, attempt INTEGER, body TEXT NOT NULL)")
        self.db.commit()

    def call(self, key):
        self.db.execute("INSERT OR IGNORE INTO calls(key,id) VALUES (?,?)", (key, str(uuid4())))
        self.db.commit()
        identifier, result = self.db.execute("SELECT id,result FROM calls WHERE key=?", (key,)).fetchone()
        return identifier, json.loads(result) if result else None

    def settle(self, key, result):
        self.db.execute("UPDATE calls SET result=? WHERE key=?", (json.dumps(result), key))
        self.db.commit()

    def clear_result(self, key):
        self.db.execute("UPDATE calls SET result=NULL WHERE key=?", (key,))
        self.db.commit()

    def reply(self, key):
        row = self.db.execute("SELECT result FROM replies WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_reply(self, key, result):
        self.db.execute("INSERT OR REPLACE INTO replies VALUES (?,?)", (key, json.dumps(result)))
        self.db.commit()

    def delete_reply(self, key):
        self.db.execute("DELETE FROM replies WHERE key=?", (key,))
        self.db.commit()

    def close(self):
        self.db.close()

    def route(self, backend_id, chatwoot_id=None):
        if chatwoot_id is not None:
            self.db.execute("INSERT OR REPLACE INTO routes VALUES (?,?)", (backend_id, chatwoot_id))
            self.db.commit()
        row = self.db.execute("SELECT chatwoot_id FROM routes WHERE backend_id=?", (backend_id,)).fetchone()
        return row[0] if row else None

    def backend_route(self, chatwoot_id):
        row = self.db.execute("SELECT backend_id FROM routes WHERE chatwoot_id=? ORDER BY backend_id DESC LIMIT 1", (chatwoot_id,)).fetchone()
        return row[0] if row else None

    def enqueue(self, key, chatwoot_id, body, due):
        cursor = self.db.execute("INSERT OR IGNORE INTO inbox(key,chatwoot_id,body,due) VALUES (?,?,?,?)", (key, chatwoot_id, json.dumps(body), due))
        if cursor.rowcount:
            self.db.execute("UPDATE inbox SET due=? WHERE chatwoot_id=? AND state='pending'", (due, chatwoot_id))
        self.db.commit()

    def pending(self, now):
        rows = self.db.execute("SELECT key,chatwoot_id,body,attempts FROM inbox WHERE state='pending' AND due<=? ORDER BY rowid LIMIT 100", (now,)).fetchall()
        return [(key, route, json.loads(body), attempts) for key, route, body, attempts in rows]

    def finish(self, key):
        self.db.execute("UPDATE inbox SET state='done' WHERE key=?", (key,))
        self.db.commit()

    def retry(self, key, due, attempts):
        self.db.execute("UPDATE inbox SET due=?,attempts=?,state=? WHERE key=? AND state!='done'", (due, attempts, "failed" if attempts >= 5 else "pending", key))
        self.db.commit()

    def own_outbound(self, outbound_id, conversation_id, purpose, source_message_id):
        self.db.execute("INSERT OR IGNORE INTO owned_outbounds VALUES (?,?,?,?,0)",
                        (outbound_id, conversation_id, purpose, source_message_id))
        self.db.commit()

    def owned_ids(self):
        # Round robin prevents old jobs (e.g. a lease expired remotely) starving later replies.
        rows = self.db.execute("SELECT id FROM owned_outbounds WHERE terminal=0 ORDER BY (id<=?),id LIMIT 50", (self.claim_cursor,)).fetchall()
        result = [r[0] for r in rows]
        if result:
            self.claim_cursor = result[-1]
        return result

    def delivery_result(self, outbound_id, attempt, body):
        self.db.execute("INSERT OR REPLACE INTO delivery_results VALUES (?,?,?)", (outbound_id, attempt, json.dumps(body)))
        self.db.commit()

    def delivery_result_done(self, outbound_id):
        self.db.execute("DELETE FROM delivery_results WHERE outbound_id=?", (outbound_id,))
        self.db.commit()

    def outbound_purpose(self, outbound_id):
        row = self.db.execute("SELECT purpose FROM owned_outbounds WHERE id=?", (outbound_id,)).fetchone()
        return row[0] if row else None

    def terminal_outbound(self, outbound_id):
        self.db.execute("UPDATE owned_outbounds SET terminal=1 WHERE id=?", (outbound_id,))
        self.db.commit()

    def register_proposal(self, conversation_id, action, proposal, source_message_id, outbound_id):
        self.db.execute("INSERT OR IGNORE INTO proposals VALUES (?,?,?,?,?,?,NULL)",
            (conversation_id, action, proposal["id"], source_message_id, proposal["expires_at"], outbound_id))
        self.db.commit()

    def presented(self, outbound_id, timestamp):
        self.db.execute("UPDATE proposals SET presented_at=COALESCE(presented_at,?) WHERE outbound_id=?", (timestamp, outbound_id))
        self.db.commit()

    def proposal(self, conversation_id, action, proposal_id):
        row = self.db.execute("SELECT source_message_id,expires_at,presented_at FROM proposals WHERE conversation_id=? AND action=? AND proposal_id=?",
                              (conversation_id, action, proposal_id)).fetchone()
        return dict(zip(("source_message_id", "expires_at", "presented_at"), row)) if row else None

    def latest_presented_proposal(self, conversation_id):
        # presented_at is always stored in UTC by both supported delivery paths.
        return self.db.execute("SELECT action,proposal_id FROM proposals WHERE conversation_id=? AND presented_at IS NOT NULL ORDER BY presented_at DESC,outbound_id DESC LIMIT 1", (conversation_id,)).fetchone()

    def invalidate_proposals(self, conversation_id):
        self.db.execute("DELETE FROM proposals WHERE conversation_id=?", (conversation_id,))
        self.db.commit()

    def queue_counts(self):
        return dict(self.db.execute("SELECT state,count(*) FROM inbox GROUP BY state").fetchall())

    def handoff_job(self, conversation_id):
        self.db.execute("INSERT OR IGNORE INTO handoff_jobs VALUES (?,0,0)", (conversation_id,))
        self.db.commit()

    def handoff_done(self, conversation_id):
        self.db.execute("DELETE FROM handoff_jobs WHERE chatwoot_id=?", (conversation_id,))
        self.db.commit()

    def latest_source(self, conversation_id):
        row = self.db.execute("SELECT max(source_message_id) FROM owned_outbounds WHERE conversation_id=?", (conversation_id,)).fetchone()
        return row[0] if row else None
