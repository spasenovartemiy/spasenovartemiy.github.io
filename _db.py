import sqlite3
import os
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_posts (
    post_id    TEXT PRIMARY KEY,
    seen_at    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vacancies (
    uid        TEXT PRIMARY KEY,        -- post_id#idx
    post_id    TEXT,
    title      TEXT,
    url        TEXT,
    company    TEXT,
    salary_raw TEXT,
    salary_max INTEGER,
    score      INTEGER,
    reason     TEXT,
    status     TEXT DEFAULT 'new',      -- new | sent | selected | skipped | generated
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_vac_status ON vacancies(status);
"""


def init():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with conn() as c:
        c.executescript(SCHEMA)


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def post_seen(post_id: str) -> bool:
    with conn() as c:
        return c.execute(
            "SELECT 1 FROM seen_posts WHERE post_id=?", (post_id,)
        ).fetchone() is not None


def mark_post_seen(post_id: str):
    with conn() as c:
        c.execute("INSERT OR IGNORE INTO seen_posts(post_id) VALUES(?)", (post_id,))


def save_vacancy(v, score: int, reason: str):
    with conn() as c:
        c.execute(
            """INSERT OR IGNORE INTO vacancies
               (uid, post_id, title, url, company, salary_raw, salary_max, score, reason)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (v.uid, v.post_id, v.title, v.url, v.company,
             v.salary_raw, v.salary_max, score, reason),
        )


def set_status(uid: str, status: str):
    with conn() as c:
        c.execute("UPDATE vacancies SET status=? WHERE uid=?", (status, uid))


def get_vacancy(uid: str):
    with conn() as c:
        return c.execute("SELECT * FROM vacancies WHERE uid=?", (uid,)).fetchone()


def list_by_status(status: str, limit: int = 50):
    with conn() as c:
        return c.execute(
            "SELECT * FROM vacancies WHERE status=? ORDER BY score DESC, created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()


def stats():
    with conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) n FROM vacancies GROUP BY status"
        ).fetchall()
        total_posts = c.execute("SELECT COUNT(*) n FROM seen_posts").fetchone()["n"]
    return {r["status"]: r["n"] for r in rows}, total_posts


def channel_has_seen(channel: str) -> bool:
    """Есть ли хоть один виденный пост из канала (post_id вида 'channel/123')."""
    with conn() as c:
        row = c.execute(
            "SELECT 1 FROM seen_posts WHERE post_id LIKE ? LIMIT 1",
            (channel + "/%",),
        ).fetchone()
        return row is not None
