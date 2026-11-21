from __future__ import annotations

import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from urllib.parse import urldefrag


DEFAULTS = {
    "provider": "openai", "model": "gpt-6-luna", "interests": "",
    "summary_words": 650, "summarize_articles": True, "lookback_days": 3,
    "max_items_per_run": 5, "reading_minutes": 30,
    "audio_fallback": False, "audio_minutes_per_video": 30,
    "timezone": "Asia/Kolkata", "delivery_times": ["08:00"],
    "schedule_enabled": False, "auto_send": False,
    "email_enabled": False, "email_to": "", "kindle_enabled": False, "kindle_to": "",
    "smtp_host": "smtp.gmail.com", "smtp_port": 465, "smtp_username": "", "smtp_sender": "",
    "onboarding_done": False, "last_schedule_slot": "", "last_schedule_attempt": "",
    "reader_theme": "light", "reader_size": 20,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid.uuid4().hex


def private_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(text)
    os.replace(temporary, path)
    path.chmod(0o600)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.path = self.root / "library.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sources(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, detail TEXT NOT NULL DEFAULT '{}',
                checked_at TEXT, error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                UNIQUE(kind,url));
            CREATE TABLE IF NOT EXISTS items(
                id TEXT PRIMARY KEY, source_id TEXT, kind TEXT NOT NULL, external_id TEXT NOT NULL,
                title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
                published TEXT NOT NULL, body_html TEXT NOT NULL DEFAULT '', summary_html TEXT NOT NULL DEFAULT '',
                takeaway TEXT NOT NULL DEFAULT '', words INTEGER NOT NULL DEFAULT 0,
                is_read INTEGER NOT NULL DEFAULT 0, starred INTEGER NOT NULL DEFAULT 0,
                is_sample INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, UNIQUE(kind,external_id));
            CREATE TABLE IF NOT EXISTS editions(
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ready',
                is_sample INTEGER NOT NULL DEFAULT 0, file TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS edition_items(
                edition_id TEXT NOT NULL REFERENCES editions(id) ON DELETE CASCADE,
                item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                position INTEGER NOT NULL, PRIMARY KEY(edition_id,item_id));
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0, result TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, finished_at TEXT);
            CREATE TABLE IF NOT EXISTS deliveries(
                id TEXT PRIMARY KEY, edition_id TEXT NOT NULL, channel TEXT NOT NULL,
                target TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS delivered_once ON deliveries(edition_id,channel,target)
                WHERE status IN ('sent','sending','uncertain');
            CREATE TABLE IF NOT EXISTS seen(kind TEXT NOT NULL, external_id TEXT NOT NULL,
                PRIMARY KEY(kind,external_id));
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(items)")}
            if "connection_id" not in columns:
                db.execute("ALTER TABLE items ADD COLUMN connection_id TEXT NOT NULL DEFAULT ''")
            for column in ("content_source", "content_url", "selection_reason"):
                if column not in columns:
                    db.execute("ALTER TABLE items ADD COLUMN " + column + " TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE items SET connection_id=COALESCE((SELECT kind FROM sources WHERE id=items.source_id),'') WHERE connection_id=''")
            db.execute("UPDATE items SET connection_id='youtube' WHERE connection_id='' AND kind='video'")
            db.execute("UPDATE items SET connection_id='raindrop' WHERE connection_id='' AND is_sample=1 AND kind='article'")
            db.execute("CREATE INDEX IF NOT EXISTS items_by_connection ON items(connection_id)")
            db.execute("UPDATE jobs SET status='interrupted',message='The app closed during this task. You can try again.',finished_at=? WHERE status IN ('queued','running')", (now(),))
            db.execute("UPDATE deliveries SET status='uncertain',message='The app closed while sending. Check your mailbox before retrying.' WHERE status='sending'")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def query(self, sql, values=(), *, one=False):
        with self.lock, self.connect() as db:
            rows = db.execute(sql, values).fetchall()
        result = [dict(row) for row in rows]
        return (result[0] if result else None) if one else result

    def execute(self, sql, values=()):
        with self.lock, self.connect() as db:
            return db.execute(sql, values).rowcount

    def settings(self):
        result = copy.deepcopy(DEFAULTS)
        for row in self.query("SELECT key,value FROM settings"):
            result[row["key"]] = json.loads(row["value"])
        return result

    def save_settings(self, values):
        with self.lock, self.connect() as db:
            for key, value in values.items():
                if key in DEFAULTS:
                    db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))

    def sources(self):
        rows = self.query("SELECT s.*,(SELECT COUNT(*) FROM items i WHERE i.source_id=s.id) AS item_count FROM sources s ORDER BY created_at")
        for row in rows:
            row["detail"] = json.loads(row["detail"])
        return rows

    def add_source(self, kind, title, url, detail=None):
        source_id = uid()
        self.execute("INSERT OR IGNORE INTO sources(id,kind,title,url,detail,created_at) VALUES(?,?,?,?,?,?)",
                     (source_id, kind, title, url, json.dumps(detail or {}), now()))
        return self.query("SELECT id FROM sources WHERE kind=? AND url=?", (kind, url), one=True)["id"]

    def known(self, kind, external_id, connection_id="", url=""):
        if self.query("SELECT 1 FROM items WHERE kind=? AND external_id=? UNION ALL SELECT 1 FROM seen WHERE kind=? AND external_id=? LIMIT 1", (kind, external_id, kind, external_id), one=True):
            return True
        # Imported bookmarks may predate retained bookmark IDs. Their source URL
        # still identifies reading already in this connection's library.
        return bool(connection_id and url and self.query("SELECT 1 FROM items WHERE kind=? AND connection_id=? AND url=? LIMIT 1",
                    (kind, connection_id, urldefrag(url)[0]), one=True))

    def add_item(self, *, kind, external_id, title, url="", author="", published=None,
                 body_html="", summary_html="", takeaway="", source_id=None, is_sample=False, connection_id="",
                 content_source="", content_url="", selection_reason=""):
        from .content import plain_text
        item_id = uid()
        if not connection_id and source_id:
            source = self.query("SELECT kind FROM sources WHERE id=?", (source_id,), one=True)
            connection_id = source["kind"] if source else ""
        words = len(plain_text(summary_html or body_html).split())
        self.execute("""INSERT OR IGNORE INTO items(id,source_id,kind,external_id,title,url,author,published,
                     body_html,summary_html,takeaway,words,is_sample,created_at,connection_id,content_source,content_url,selection_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (item_id, source_id, kind, external_id, title, url, author, published or now(),
                      body_html, summary_html, takeaway, words, int(is_sample), now(), connection_id,content_source,content_url,selection_reason))
        return self.query("SELECT id FROM items WHERE kind=? AND external_id=?", (kind, external_id), one=True)["id"]

    def items(self, search="", kind="all", status="all", limit=200, offset=0):
        clauses, params = [], []
        if search:
            clauses.append("(title LIKE ? OR author LIKE ? OR takeaway LIKE ?)")
            params += ["%" + search + "%"] * 3
        if kind in ("article", "video"):
            clauses.append("kind=?")
            params.append(kind)
        if status == "unread":
            clauses.append("is_read=0")
        elif status == "saved":
            clauses.append("starred=1")
        real = self.query("SELECT 1 FROM items WHERE is_sample=0 LIMIT 1", one=True)
        if real:
            clauses.append("is_sample=0")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self.query("SELECT * FROM items" + where + " ORDER BY published DESC,created_at DESC,id LIMIT ? OFFSET ?",
                          (*params, max(1, min(int(limit), 500)), max(0, min(int(offset), 2**31 - 1))))

    def editions(self):
        return self.query("""SELECT e.*,(SELECT COUNT(*) FROM edition_items ei WHERE ei.edition_id=e.id) AS item_count,
            COALESCE((SELECT SUM(i.words) FROM edition_items ei JOIN items i ON i.id=ei.item_id WHERE ei.edition_id=e.id),0) AS words
            FROM editions e ORDER BY is_sample ASC,created_at DESC LIMIT 100""")

    def edition(self, edition_id):
        edition = self.query("SELECT * FROM editions WHERE id=?", (edition_id,), one=True)
        if not edition:
            raise ValueError("This edition could not be found")
        edition["items"] = self.query("SELECT i.* FROM edition_items ei JOIN items i ON i.id=ei.item_id WHERE edition_id=? ORDER BY position", (edition_id,))
        return edition

    def make_edition(self, title, item_ids, *, is_sample=False, status="ready", edition_id=None):
        edition_id = edition_id or uid()
        with self.lock, self.connect() as db:
            db.execute("INSERT INTO editions(id,title,created_at,is_sample,status) VALUES(?,?,?,?,?)",
                       (edition_id, title, now(), int(is_sample), status))
            db.executemany("INSERT INTO edition_items VALUES(?,?,?)", [(edition_id, item_id, i) for i, item_id in enumerate(item_ids)])
        return edition_id

    def create_job(self, kind):
        job_id = uid()
        self.execute("INSERT INTO jobs(id,kind,status,message,created_at) VALUES(?,?,?,?,?)",
                     (job_id, kind, "queued", "Getting ready…", now()))
        return job_id

    def progress(self, job_id, message, progress=0):
        self.execute("UPDATE jobs SET status='running',message=?,progress=? WHERE id=?", (message, progress, job_id))

    def finish_job(self, job_id, result=None, error=None):
        self.execute("UPDATE jobs SET status=?,message=?,progress=?,result=?,finished_at=? WHERE id=?",
                     ("failed" if error else "done", error or (result or {}).get("message", "All done"),
                      100, json.dumps(result or {}), now(), job_id))
