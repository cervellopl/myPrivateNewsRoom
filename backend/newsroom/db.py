"""SQLite storage layer (stdlib only, no ORM)."""
import json
import sqlite3
import threading
import time
from typing import Any, Iterable

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plugins (
    name        TEXT PRIMARY KEY,   -- module name, e.g. "rss"
    filename    TEXT NOT NULL,
    version     TEXT,
    description TEXT,
    author      TEXT,
    config_spec TEXT,               -- JSON list describing accepted config keys
    uploaded_at REAL NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    load_error  TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    plugin       TEXT NOT NULL,
    config       TEXT NOT NULL DEFAULT '{}',   -- JSON passed to the plugin
    enabled      INTEGER NOT NULL DEFAULT 1,
    interval_seconds INTEGER,                  -- NULL -> use global interval
    last_run_at  REAL,
    last_status  TEXT,
    last_error   TEXT,
    last_item_count INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS news (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    source_name  TEXT NOT NULL,
    title        TEXT NOT NULL,
    link         TEXT NOT NULL,
    image        TEXT,
    published_at REAL,
    summary      TEXT,
    author       TEXT,
    guid         TEXT NOT NULL,
    fetched_at   REAL NOT NULL,
    UNIQUE (source_id, guid)
);

CREATE TABLE IF NOT EXISTS bookmark_categories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    color      TEXT,                       -- optional hue override for the UI
    created_at REAL NOT NULL
);

-- A bookmark keeps its own copy of the item. News is pruned by retention and
-- deleted with its source, but a saved article must outlive both.
CREATE TABLE IF NOT EXISTS bookmarks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id  INTEGER REFERENCES bookmark_categories(id) ON DELETE SET NULL,
    news_id      INTEGER,                  -- may point at pruned news
    title        TEXT NOT NULL,
    link         TEXT NOT NULL UNIQUE,
    image        TEXT,
    published_at REAL,
    summary      TEXT,
    author       TEXT,
    source_name  TEXT,
    note         TEXT,
    created_at   REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bookmarks_category ON bookmarks (category_id);
CREATE INDEX IF NOT EXISTS idx_news_published ON news (published_at DESC);
CREATE INDEX IF NOT EXISTS idx_news_source ON news (source_id);
"""


def connect() -> sqlite3.Connection:
    """One connection per thread; WAL so the poller and API can share the file."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def init() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    set_setting_default("poll_interval_seconds", config.DEFAULT_POLL_INTERVAL)


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    return connect().execute(sql, tuple(params))


# --- settings -------------------------------------------------------------

def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def set_setting_default(key: str, value: Any) -> None:
    execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
        (key, json.dumps(value)),
    )


def poll_interval() -> int:
    return int(get_setting("poll_interval_seconds", config.DEFAULT_POLL_INTERVAL))


# --- news -----------------------------------------------------------------

def insert_news(source_id: int, source_name: str, item: dict) -> bool:
    """Insert one item. Returns True when it was new (deduplicated on guid)."""
    cur = execute(
        """INSERT OR IGNORE INTO news
           (source_id, source_name, title, link, image, published_at,
            summary, author, guid, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_id,
            source_name,
            item["title"],
            item["link"],
            item.get("image"),
            item.get("published_at"),
            item.get("summary"),
            item.get("author"),
            item["guid"],
            time.time(),
        ),
    )
    return cur.rowcount > 0


def prune_old_news() -> int:
    if config.RETENTION_DAYS <= 0:
        return 0
    cutoff = time.time() - config.RETENTION_DAYS * 86400
    cur = execute(
        "DELETE FROM news WHERE COALESCE(published_at, fetched_at) < ?", (cutoff,)
    )
    return cur.rowcount
