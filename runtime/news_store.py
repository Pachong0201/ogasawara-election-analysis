"""Durable local news index and lease-based queue; no network operations."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def iso(now):
    return dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat()


class NewsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sources (
                id TEXT PRIMARY KEY, config TEXT NOT NULL, last_success REAL,
                last_error TEXT, failures INTEGER NOT NULL DEFAULT 0,
                etag TEXT, modified TEXT, first_success REAL);
            CREATE TABLE IF NOT EXISTS source_runs (
                id INTEGER PRIMARY KEY, source_id TEXT, at REAL, status TEXT,
                discovered INTEGER, error TEXT);
            CREATE TABLE IF NOT EXISTS articles (
                url TEXT PRIMARY KEY, record TEXT NOT NULL, first_seen REAL NOT NULL,
                last_seen REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS article_versions (
                id INTEGER PRIMARY KEY, url TEXT NOT NULL, observed_at REAL NOT NULL,
                digest TEXT NOT NULL, record TEXT NOT NULL,
                UNIQUE(url, observed_at, digest));
            CREATE INDEX IF NOT EXISTS versions_url ON article_versions(url, observed_at);
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
                payload TEXT NOT NULL, due REAL NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, lease_until REAL, token TEXT,
                last_error TEXT, UNIQUE(kind, key));
            CREATE TABLE IF NOT EXISTS host_limits (host TEXT PRIMARY KEY, next_allowed REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, record TEXT NOT NULL, updated REAL);
            CREATE TABLE IF NOT EXISTS event_evidence (
                scope TEXT, url TEXT, event_id TEXT, PRIMARY KEY(scope, url));
            CREATE TABLE IF NOT EXISTS event_versions (
                event_id TEXT, digest TEXT, record TEXT, observed_at REAL,
                PRIMARY KEY(event_id, digest));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def register_sources(self, sources, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            # Disabled/removed sources cannot retain runnable discovery jobs.
            active = {s['id'] for s in sources}
            for row in db.execute("SELECT key FROM jobs WHERE kind='discover'").fetchall():
                if row['key'] not in active:
                    db.execute("UPDATE jobs SET status='disabled',token=NULL WHERE kind='discover' AND key=?", (row['key'],))
            for source in sources:
                prior = db.execute('SELECT config FROM sources WHERE id=?', (source['id'],)).fetchone()
                if prior and json.loads(prior['config']).get('url') != source['url']:
                    db.execute('UPDATE sources SET etag=NULL,modified=NULL,last_success=NULL,first_success=NULL,failures=0,last_error=NULL WHERE id=?', (source['id'],))
                    db.execute("UPDATE jobs SET due=?,status='pending',token=NULL WHERE kind='discover' AND key=?", (now, source['id']))
                db.execute('INSERT INTO sources(id,config) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET config=excluded.config',
                           (source['id'], dumps(source)))
                db.execute("INSERT INTO jobs(kind,key,payload,due) VALUES('discover',?,?,?) ON CONFLICT(kind,key) DO UPDATE SET payload=excluded.payload,status=CASE WHEN jobs.status='disabled' THEN 'pending' ELSE jobs.status END",
                           (source['id'], dumps(source), now))

    def source(self, source_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM sources WHERE id=?', (source_id,)).fetchone()
            return dict(row) if row else {}

    def record_run(self, source_id, now, status, count=0, error='', headers=None):
        headers = headers or {}
        with self.connect() as db:
            db.execute('INSERT INTO source_runs(source_id,at,status,discovered,error) VALUES(?,?,?,?,?)',
                       (source_id, now, status, count, error))
            if status in ('ok', 'not_modified'):
                db.execute('UPDATE sources SET last_success=?,first_success=COALESCE(first_success,?),last_error=NULL,failures=0,etag=COALESCE(?,etag),modified=COALESCE(?,modified) WHERE id=?',
                           (now, now, headers.get('ETag'), headers.get('Last-Modified'), source_id))
            else:
                db.execute('UPDATE sources SET failures=failures+1,last_error=? WHERE id=?', (error, source_id))

    def discover(self, record, now):
        """Upsert a link and enqueue its body atomically; never erase read content."""
        url = record['url']
        with self.connect() as db:
            old = db.execute('SELECT record FROM articles WHERE url=?', (url,)).fetchone()
            if old:
                merged = json.loads(old['record'])
                # Feed refreshes must not relabel old content with a new date/title.
                merged['discovered_via'] = sorted(set(merged.get('discovered_via', [])) | {record['source_id']})
            else:
                merged = {**record, 'first_seen_at': iso(now), 'retrieved_at': iso(now),
                          'body_status': 'pending', 'verification_status': 'lead_only',
                          'discovered_via': [record['source_id']]}
            db.execute('INSERT INTO articles VALUES(?,?,?,?) ON CONFLICT(url) DO UPDATE SET record=excluded.record,last_seen=excluded.last_seen',
                       (url, dumps(merged), now, now))
            db.execute("INSERT OR IGNORE INTO jobs(kind,key,payload,due) VALUES('body',?,?,?)",
                       (url, dumps({'url': url, 'source_id': record['source_id']}), now))

    def claim(self, kind, now, lease_seconds=300):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE kind=? AND ((status='pending' AND due<=?) OR (status='running' AND lease_until<=?)) ORDER BY due,id LIMIT 1", (kind, now, now)).fetchone()
            if not row:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='running',token=?,lease_until=?,attempts=attempts+1 WHERE id=?", (token, now + lease_seconds, row['id']))
            return {**dict(row), 'payload': json.loads(row['payload']), 'token': token, 'attempts': row['attempts'] + 1}

    def finish(self, job, due=None, error=''):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status=?,due=?,lease_until=NULL,token=NULL,last_error=?,attempts=CASE WHEN ?='' THEN 0 ELSE attempts END WHERE id=? AND token=?",
                       ('pending' if due is not None else 'done', due or 0, error, error, job['id'], job['token']))

    def enqueue(self, kind, key, payload, now):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO jobs(kind,key,payload,due) VALUES(?,?,?,?)', (kind, key, dumps(payload), now))

    def article(self, url):
        with self.connect() as db:
            row = db.execute('SELECT record FROM articles WHERE url=?', (url,)).fetchone()
            return json.loads(row['record']) if row else None

    def save_body(self, url, body, now):
        with self.connect() as db:
            row = db.execute('SELECT record FROM articles WHERE url=?', (url,)).fetchone()
            if not row:
                return
            record = json.loads(row['record'])
            if body.get('body_status') != 'read' and record.get('body_status') == 'read':
                record['refresh_error'] = body.get('body_status')
            else:
                record.update(body)
                record['retrieved_at'] = iso(now)
                record['verification_status'] = 'body_read_unverified' if body.get('body_status') == 'read' else 'lead_only'
                if body.get('body_status') == 'read':
                    digest = hashlib.sha256(dumps({k: record.get(k) for k in ('content', 'page_date', 'page_title')}).encode()).hexdigest()
                    record['article_version'] = digest
                    latest = db.execute('SELECT digest FROM article_versions WHERE url=? ORDER BY observed_at DESC,id DESC LIMIT 1', (url,)).fetchone()
                    if not latest or latest['digest'] != digest:
                        db.execute('INSERT OR IGNORE INTO article_versions(url,observed_at,digest,record) VALUES(?,?,?,?)', (url, now, digest, dumps(record)))
            db.execute('UPDATE articles SET record=? WHERE url=?', (dumps(record), url))

    def records_at(self, cutoff):
        """Only versions actually observed by cutoff are eligible for a replay."""
        with self.connect() as db:
            rows = db.execute('''SELECT a.record AS lead, v.record AS body FROM articles a
                LEFT JOIN article_versions v ON v.id=(SELECT id FROM article_versions
                WHERE url=a.url AND observed_at<=? ORDER BY observed_at DESC,id DESC LIMIT 1)
                WHERE a.first_seen<=? ORDER BY a.first_seen DESC''', (cutoff, cutoff)).fetchall()
        result = []
        for row in rows:
            if row['body']:
                result.append(json.loads(row['body']))
            else:
                lead = json.loads(row['lead'])
                # A newly fetched body cannot leak into an earlier as_of snapshot.
                for key in ('content', 'page_date', 'page_title', 'content_sha256', 'article_version'):
                    lead.pop(key, None)
                lead.update(body_status='not_read_by_as_of', verification_status='lead_only')
                result.append(lead)
        return result

    def reconcile_events(self, events, scope, now):
        """Keep the ID when an existing article gains corroboration or a correction."""
        result = []
        used = set()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for event in events:
                urls = sorted(set(event.get('source_urls', [])))
                ids = []
                for url in urls:
                    row = db.execute('SELECT event_id FROM event_evidence WHERE scope=? AND url=?', (scope, url)).fetchone()
                    if row:
                        ids.append(row['event_id'])
                event = dict(event)
                event['event_id'] = sorted(ids)[0] if ids else 'news-event-' + hashlib.sha256((scope + event['event_id']).encode()).hexdigest()[:20]
                if event['event_id'] in used:
                    event['event_id'] += '-' + hashlib.sha256(dumps(urls).encode()).hexdigest()[:8]
                    event.update(verification_status='requires_review', structural_use='context_only')
                used.add(event['event_id'])
                digest = hashlib.sha256(dumps(event).encode()).hexdigest()
                event['event_version'] = digest
                db.execute('INSERT INTO events VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET record=excluded.record,updated=excluded.updated', (event['event_id'], scope, dumps(event), now))
                db.execute('INSERT OR IGNORE INTO event_versions VALUES(?,?,?,?)', (event['event_id'], digest, dumps(event), now))
                for url in urls:
                    db.execute('INSERT INTO event_evidence VALUES(?,?,?) ON CONFLICT(scope,url) DO UPDATE SET event_id=excluded.event_id', (scope, url, event['event_id']))
                result.append(event)
        return result

    def reserve_host(self, host, now, interval=2):
        """Share host spacing across discovery/body workers and process restarts."""
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT next_allowed FROM host_limits WHERE host=?', (host,)).fetchone()
            if row and row['next_allowed'] > now:
                return row['next_allowed']
            db.execute('INSERT INTO host_limits VALUES(?,?) ON CONFLICT(host) DO UPDATE SET next_allowed=excluded.next_allowed', (host, now + interval))
            return None

    def research_requests(self):
        with self.connect() as db:
            return [json.loads(row['payload']) for row in db.execute("SELECT payload FROM jobs WHERE kind='research' AND status='pending' ORDER BY id")]

    def refresh(self, now):
        with self.connect() as db:
            # A user refresh never breaks an error backoff or a running lease.
            db.execute("UPDATE jobs SET due=MIN(due,?) WHERE kind='discover' AND status='pending' AND (last_error IS NULL OR last_error='')", (now,))

    def health(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            rows = db.execute("SELECT s.* FROM sources s JOIN jobs j ON j.kind='discover' AND j.key=s.id WHERE j.status!='disabled'").fetchall()
            jobs = {f"{r['kind']}:{r['status']}": r['n'] for r in db.execute('SELECT kind,status,COUNT(*) n FROM jobs GROUP BY kind,status')}
            records = [json.loads(r['record']) for r in db.execute('SELECT record FROM articles')]
            count = len(records)
            bodies = {}
            for record in records:
                status = record.get('body_status', 'unknown')
                bodies[status] = bodies.get(status, 0) + 1
        sources = []
        for row in rows:
            config = json.loads(row['config'])
            stale = row['last_success'] is None or now - row['last_success'] > max(1800, config.get('interval_seconds', 900) * 3)
            sources.append({'source_id': row['id'], 'last_success': iso(row['last_success']) if row['last_success'] else None,
                            'first_success': iso(row['first_success']) if row['first_success'] else None,
                            'status': 'failed' if row['failures'] else ('stale' if stale else 'ok'),
                            'failures': row['failures'], 'last_error': row['last_error']})
        return {'sources': sources, 'jobs': jobs, 'article_count': count, 'body_status_counts': bodies,
                'coverage_status': 'partial' if sources and all(s['status']=='ok' for s in sources) else 'degraded',
                'history_complete': False}
