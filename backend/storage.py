from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import time

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS templates(id TEXT PRIMARY KEY,name TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS template_versions(template_id TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL,PRIMARY KEY(template_id,version));
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,name TEXT NOT NULL,extension TEXT NOT NULL,hash TEXT NOT NULL,pages TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'imported',archived INTEGER NOT NULL DEFAULT 0,template_id TEXT,template_version INTEGER,template_snapshot TEXT,raw_json TEXT,result_json TEXT,revision INTEGER NOT NULL DEFAULT 0,duplicate_of TEXT,duplicate_decision TEXT,approval_note TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,template_id TEXT NOT NULL,template_snapshot TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,error TEXT,attempts INTEGER NOT NULL DEFAULT 0,cancel_requested INTEGER NOT NULL DEFAULT 0,created_at REAL NOT NULL,updated_at REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(document_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS watches(id TEXT PRIMARY KEY,path TEXT NOT NULL UNIQUE,template_id TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,error TEXT,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS watch_seen(watch_id TEXT NOT NULL,path TEXT NOT NULL,fingerprint TEXT NOT NULL,document_id TEXT,PRIMARY KEY(watch_id,path,fingerprint));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,document_id TEXT,action TEXT NOT NULL,payload TEXT NOT NULL,created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS audit_document ON audit(document_id,id);
'''

class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in ['uploads', 'rendered', 'backups']:
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / 'fieldwork.sqlite'
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self.ensure_columns(conn)
            conn.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
            # A crash cannot leave a job permanently running or publish half an extraction.
            conn.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END,progress=0,updated_at=? WHERE status='running'", (time.time(),))
        os.chmod(self.path, 0o600)

    @staticmethod
    def ensure_columns(conn):
        columns={r[1] for r in conn.execute('PRAGMA table_info(documents)')}
        for name in ['approval_fingerprint','duplicate_fingerprint']:
            if name not in columns:conn.execute(f'ALTER TABLE documents ADD COLUMN {name} TEXT')

    @contextmanager
    def connect(self, immediate=False):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            if immediate:
                conn.execute('BEGIN IMMEDIATE')
            with conn:
                yield conn
        finally:
            conn.close()

    def setting(self, key, default=None):
        with self.connect() as conn:
            row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.connect() as conn:
            conn.execute('INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(value)))

    @staticmethod
    def audit(conn, id, action, payload):
        conn.execute('INSERT INTO audit(document_id,action,payload,created_at) VALUES(?,?,?,?)', (id, action, json.dumps(payload), time.time()))
