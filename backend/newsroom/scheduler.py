"""Background poller.

A single daemon thread wakes up every second, and runs any enabled source whose
own interval (or the global one, when the source does not override it) has
elapsed. Changing the interval through the API takes effect immediately - the
next due time is always recomputed from the current setting.
"""
from __future__ import annotations

import json
import logging
import threading
import time

from . import db, plugins, sdk

log = logging.getLogger("newsroom.scheduler")

_thread: threading.Thread | None = None
_stop = threading.Event()
_wake = threading.Event()
_running: set[int] = set()
_lock = threading.Lock()

_stats = {"runs": 0, "items_added": 0, "errors": 0, "started_at": None,
          "last_cycle_at": None}


def stats() -> dict:
    with _lock:
        return dict(_stats, active=sorted(_running))


def source_interval(row) -> int:
    return int(row["interval_seconds"] or db.poll_interval())


def is_due(row, now: float) -> bool:
    if not row["enabled"]:
        return False
    if row["last_run_at"] is None:
        return True
    return now - row["last_run_at"] >= source_interval(row)


def run_source(source_id: int, *, force: bool = False) -> dict:
    """Fetch one source and store its items. Safe to call from any thread."""
    row = db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if row is None:
        raise LookupError(f"source {source_id} not found")

    with _lock:
        if source_id in _running:
            return {"status": "already_running", "source_id": source_id, "added": 0}
        _running.add(source_id)

    started = time.time()
    added = 0
    status = "ok"
    error = None
    count = 0
    try:
        try:
            source_config = json.loads(row["config"] or "{}")
        except json.JSONDecodeError as exc:
            raise sdk.PluginError(f"stored config is not valid JSON: {exc}") from exc

        items = plugins.run(row["plugin"], source_config)
        count = len(items)
        for item in items:
            if db.insert_news(source_id, row["name"], item):
                added += 1
        if not items:
            status = "empty"
    except sdk.PluginError as exc:
        status, error = "error", str(exc)
        log.warning("source %s (%s) failed: %s", source_id, row["name"], exc)
    except Exception as exc:  # noqa: BLE001 - never let the poller die
        status, error = "error", f"{type(exc).__name__}: {exc}"
        log.exception("source %s (%s) crashed", source_id, row["name"])
    finally:
        db.execute(
            """UPDATE sources
                  SET last_run_at = ?, last_status = ?, last_error = ?,
                      last_item_count = ?
                WHERE id = ?""",
            (time.time(), status, error, count, source_id),
        )
        with _lock:
            _running.discard(source_id)
            _stats["runs"] += 1
            _stats["items_added"] += added
            if status == "error":
                _stats["errors"] += 1

    return {
        "source_id": source_id,
        "name": row["name"],
        "status": status,
        "error": error,
        "fetched": count,
        "added": added,
        "took_seconds": round(time.time() - started, 2),
    }


def _cycle() -> None:
    now = time.time()
    for row in db.query("SELECT * FROM sources WHERE enabled = 1"):
        if not is_due(row, now):
            continue
        with _lock:
            if row["id"] in _running:
                continue
        threading.Thread(
            target=run_source, args=(row["id"],), daemon=True,
            name=f"fetch-{row['id']}",
        ).start()
    with _lock:
        _stats["last_cycle_at"] = now


def _loop() -> None:
    log.info("poller started (global interval: %ss)", db.poll_interval())
    last_prune = 0.0
    while not _stop.is_set():
        try:
            _cycle()
            if time.time() - last_prune > 3600:
                removed = db.prune_old_news()
                last_prune = time.time()
                if removed:
                    log.info("pruned %s expired news items", removed)
        except Exception:  # noqa: BLE001
            log.exception("poller cycle failed")
        _wake.wait(1.0)
        _wake.clear()


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    with _lock:
        _stats["started_at"] = time.time()
    _thread = threading.Thread(target=_loop, daemon=True, name="newsroom-poller")
    _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()
    if _thread:
        _thread.join(timeout=5)


def kick() -> None:
    """Ask the poller to re-evaluate immediately (after a config change)."""
    _wake.set()
