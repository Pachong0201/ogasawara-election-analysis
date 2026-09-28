"""Durable research checkpoints, leases and shared daily API budgets."""
from __future__ import annotations

import json
import time
import uuid

from .news_store import NewsStore, dumps, iso


class ResearchStore(NewsStore):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS research_runs (
                job_id INTEGER PRIMARY KEY, updated REAL NOT NULL, result TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_calls (
                id INTEGER PRIMARY KEY, job_id INTEGER, at REAL NOT NULL,
                provider TEXT NOT NULL, units INTEGER NOT NULL, actual_tokens INTEGER,
                status TEXT NOT NULL, detail TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS research_calls_day ON research_calls(at,provider);
            ''')

    def schedule(self, key, payload, ttl, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT j.*,r.updated FROM jobs j LEFT JOIN research_runs r ON r.job_id=j.id WHERE j.kind='research' AND j.key=?", (key,)).fetchone()
            if row and row['status'] == 'done' and now - (row['updated'] or 0) >= ttl:
                db.execute("UPDATE jobs SET status='pending',payload=?,due=?,attempts=0,last_error=NULL WHERE id=?", (dumps(payload), now, row['id']))
                db.execute('DELETE FROM research_runs WHERE job_id=?', (row['id'],))
            db.execute("INSERT OR IGNORE INTO jobs(kind,key,payload,due) VALUES('research',?,?,?)", (key, dumps(payload), now))
            return db.execute("SELECT id FROM jobs WHERE kind='research' AND key=?", (key,)).fetchone()['id']

    def claim_research(self, job_id=None, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # One research worker across bot threads and collector processes.
            if db.execute("SELECT 1 FROM jobs WHERE kind='research' AND status='running' AND lease_until>?", (now,)).fetchone():
                return None
            row = db.execute("SELECT * FROM jobs WHERE kind='research' AND (? IS NULL OR id=?) AND ((status='pending' AND due<=?) OR (status='running' AND lease_until<=?)) ORDER BY due,id LIMIT 1", (job_id, job_id, now, now)).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='running',token=?,lease_until=?,attempts=attempts+1 WHERE id=?", (token, now + 120, row['id']))
            return {**dict(row), 'payload': json.loads(row['payload']), 'token': token, 'attempts': row['attempts'] + 1}

    def checkpoint(self, job, result):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute("UPDATE jobs SET lease_until=? WHERE id=? AND token=? AND status='running' AND lease_until>?", (now + 120, job['id'], job['token'], now)).rowcount
            if not changed:
                raise RuntimeError('research_lease_lost')
            db.execute('INSERT INTO research_runs VALUES(?,?,?) ON CONFLICT(job_id) DO UPDATE SET updated=excluded.updated,result=excluded.result', (job['id'], now, dumps(result)))

    def result(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT j.status,j.last_error,j.due,r.result,r.updated FROM jobs j LEFT JOIN research_runs r ON r.job_id=j.id WHERE j.id=?', (job_id,)).fetchone()
        if not row:
            return {'status': 'missing'}
        result = json.loads(row['result']) if row['result'] else {}
        if row['status'] != 'done':
            result['status'] = 'retry_pending' if row['last_error'] else row['status']
        result.update(job_id=job_id, last_error=row['last_error'], updated_at=iso(row['updated']) if row['updated'] else None)
        return result

    def reserve_call(self, job, provider, units, limit, detail):
        """Reserve before sending. Failed/ambiguous calls keep their reservation."""
        now = time.time()
        day = int(now // 86400) * 86400
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owned = db.execute("SELECT 1 FROM jobs WHERE id=? AND token=? AND status='running' AND lease_until>?", (job['id'], job['token'], now)).fetchone()
            if not owned:
                raise RuntimeError('research_lease_lost')
            used = db.execute('SELECT COALESCE(SUM(units),0) FROM research_calls WHERE at>=? AND provider=?', (day, provider)).fetchone()[0]
            if used + units > limit:
                return None
            return db.execute('INSERT INTO research_calls(job_id,at,provider,units,status,detail) VALUES(?,?,?,?,?,?)', (job['id'], now, provider, units, 'reserved', dumps(detail))).lastrowid

    def finish_call(self, call_id, status, actual_tokens=None):
        with self.connect() as db:
            # Keep conservative reservations for the daily hard cap; report actual usage separately.
            db.execute('UPDATE research_calls SET status=?,actual_tokens=? WHERE id=?', (status, actual_tokens, call_id))
