"""HTTP API for myPrivateNewsRoom."""
from __future__ import annotations

import datetime as dt
import json
import re
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import (Depends, FastAPI, File, Header, HTTPException, Query,
                     UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__, config, db, export, plugins, scheduler

log = logging.getLogger("newsroom.api")


# --- models ---------------------------------------------------------------

class NewsItem(BaseModel):
    id: int
    title: str
    date: dt.datetime | None = Field(None, description="Publication date of the news")
    image: str | None = Field(None, description="Premier (lead) image URL")
    source: str = Field(..., description="Name of the source it came from")
    source_id: int
    link: str = Field(..., description="Link to the original news article")
    summary: str | None = None
    author: str | None = None
    fetched_at: dt.datetime
    bookmark_id: int | None = Field(None, description="Set when this item is bookmarked")
    bookmark_category: str | None = None


class BookmarkCategoryIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    color: str | None = Field(None, max_length=32, description="Optional colour for the UI")


class BookmarkCategory(BaseModel):
    id: int
    name: str
    color: str | None
    created_at: dt.datetime
    bookmark_count: int


class BookmarkIn(BaseModel):
    news_id: int | None = Field(None, description="Save this stored news item")
    category_id: int | None = None
    note: str | None = Field(None, max_length=2000)
    # a bookmark can also be created from scratch, without a news item
    title: str | None = None
    link: str | None = None
    image: str | None = None
    source: str | None = None


class BookmarkPatch(BaseModel):
    category_id: int | None = None
    note: str | None = Field(None, max_length=2000)


class Bookmark(BaseModel):
    id: int
    category_id: int | None
    category: str | None
    news_id: int | None
    title: str
    date: dt.datetime | None
    image: str | None
    source: str | None
    link: str
    summary: str | None
    author: str | None
    note: str | None
    created_at: dt.datetime


class NewsPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[NewsItem]


class SourceIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    plugin: str
    config: dict[str, Any] = {}
    enabled: bool = True
    interval_seconds: int | None = Field(
        None, description="Per-source override of the global polling interval"
    )


class SourcePatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    plugin: str | None = None
    config: dict[str, Any] | None = None
    enabled: bool | None = None
    interval_seconds: int | None = None


class Source(BaseModel):
    id: int
    name: str
    plugin: str
    config: dict[str, Any]
    enabled: bool
    interval_seconds: int | None
    effective_interval_seconds: int
    last_run_at: dt.datetime | None
    next_run_at: dt.datetime | None
    last_status: str | None
    last_error: str | None
    last_item_count: int
    news_count: int


class Plugin(BaseModel):
    name: str
    version: str | None
    description: str | None
    author: str | None
    config_spec: list[dict[str, Any]]
    uploaded_at: dt.datetime
    loaded: bool
    load_error: str | None
    sources_using: int


class Settings(BaseModel):
    poll_interval_seconds: int = Field(
        ..., ge=1, description="How often sources are polled, in seconds"
    )


class SettingsPatch(BaseModel):
    poll_interval_seconds: int | None = Field(None, ge=1)


# --- helpers --------------------------------------------------------------

def _ts(value: float | None) -> dt.datetime | None:
    if value is None:
        return None
    return dt.datetime.fromtimestamp(value, dt.timezone.utc)


def require_admin(x_api_token: str | None = Header(default=None)) -> None:
    """Guard for endpoints that mutate state or execute uploaded code."""
    if not config.API_TOKEN:
        return
    if x_api_token != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Token")


def _source_out(row) -> Source:
    interval = scheduler.source_interval(row)
    count = db.query_one(
        "SELECT COUNT(*) AS c FROM news WHERE source_id = ?", (row["id"],)
    )["c"]
    next_run = None
    if row["enabled"]:
        next_run = (row["last_run_at"] + interval) if row["last_run_at"] else time.time()
    return Source(
        id=row["id"],
        name=row["name"],
        plugin=row["plugin"],
        config=json.loads(row["config"] or "{}"),
        enabled=bool(row["enabled"]),
        interval_seconds=row["interval_seconds"],
        effective_interval_seconds=interval,
        last_run_at=_ts(row["last_run_at"]),
        next_run_at=_ts(next_run),
        last_status=row["last_status"],
        last_error=row["last_error"],
        last_item_count=row["last_item_count"],
        news_count=count,
    )


def _news_out(row) -> NewsItem:
    keys = row.keys()
    return NewsItem(
        id=row["id"],
        title=row["title"],
        date=_ts(row["published_at"]),
        image=row["image"],
        source=row["source_name"],
        source_id=row["source_id"],
        link=row["link"],
        summary=row["summary"],
        author=row["author"],
        fetched_at=_ts(row["fetched_at"]),
        bookmark_id=row["bookmark_id"] if "bookmark_id" in keys else None,
        bookmark_category=row["bookmark_category"] if "bookmark_category" in keys else None,
    )


def _check_interval(value: int | None) -> int | None:
    if value is None:
        return None
    if value < config.MIN_POLL_INTERVAL:
        raise HTTPException(
            422, f"interval must be at least {config.MIN_POLL_INTERVAL} seconds"
        )
    return value


# --- app ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    db.init()
    plugins.load_all()
    log.info("plugins loaded: %s", ", ".join(plugins.loaded_names()) or "none")
    scheduler.start()
    yield
    scheduler.stop()


app = FastAPI(
    title="myPrivateNewsRoom",
    version=__version__,
    description=(
        "Private news aggregator. Sources are defined by plugins that you upload; "
        "every source is polled on a configurable interval and each stored news "
        "item carries its date, premier image, source and link."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- news -----------------------------------------------------------------

@app.get("/api/news", response_model=NewsPage, tags=["news"])
def list_news(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source_id: int | None = None,
    q: str | None = Query(None, description="Full-text filter on title and summary"),
    since: dt.datetime | None = Query(None, description="Only news published after this"),
    with_image: bool | None = Query(None, description="Only items that have an image"),
    bookmarked: bool | None = Query(None, description="Only saved (or only unsaved) items"),
    after_id: int | None = Query(
        None, description="Only items stored after this id - what a notifier polls with"
    ),
    order: Literal["date", "fetched"] = "date",
):
    """The main feed: newest news first, ready for the web and Android clients."""
    where, params = ["1=1"], []
    if source_id is not None:
        where.append("n.source_id = ?")
        params.append(source_id)
    if q:
        where.append("(n.title LIKE ? OR IFNULL(n.summary,'') LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if since is not None:
        where.append("COALESCE(n.published_at, n.fetched_at) >= ?")
        params.append(since.timestamp())
    if with_image:
        where.append("n.image IS NOT NULL AND n.image <> ''")
    if after_id is not None:
        # ids grow with insertion, so this is "everything collected since then",
        # which is what a notifier wants - not "published since then"
        where.append("n.id > ?")
        params.append(after_id)
    if bookmarked is not None:
        where.append("b.id IS NOT NULL" if bookmarked else "b.id IS NULL")

    clause = " AND ".join(where)
    sort = ("COALESCE(n.published_at, n.fetched_at) DESC" if order == "date"
            else "n.fetched_at DESC")
    # the bookmark join lets the feed show what is already saved, and lets the
    # caller ask for only saved (or only unsaved) items
    joins = """FROM news n
               LEFT JOIN bookmarks b ON b.link = n.link
               LEFT JOIN bookmark_categories c ON c.id = b.category_id"""

    total = db.query_one(f"SELECT COUNT(*) AS c {joins} WHERE {clause}", params)["c"]
    rows = db.query(
        f"""SELECT n.*, b.id AS bookmark_id, c.name AS bookmark_category
            {joins} WHERE {clause}
            ORDER BY {sort}, n.id DESC LIMIT ? OFFSET ?""",
        params + [limit, offset],
    )
    return NewsPage(
        total=total, limit=limit, offset=offset, items=[_news_out(r) for r in rows]
    )


@app.get("/api/news/{news_id}", response_model=NewsItem, tags=["news"])
def get_news(news_id: int):
    row = db.query_one("SELECT * FROM news WHERE id = ?", (news_id,))
    if row is None:
        raise HTTPException(404, "news item not found")
    return _news_out(row)


@app.delete("/api/news/{news_id}", tags=["news"],
            dependencies=[Depends(require_admin)])
def delete_news(news_id: int):
    cur = db.execute("DELETE FROM news WHERE id = ?", (news_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "news item not found")
    return {"deleted": news_id}


# --- bookmarks ------------------------------------------------------------

def _category_out(row) -> BookmarkCategory:
    count = db.query_one(
        "SELECT COUNT(*) AS c FROM bookmarks WHERE category_id = ?", (row["id"],)
    )["c"]
    return BookmarkCategory(
        id=row["id"], name=row["name"], color=row["color"],
        created_at=_ts(row["created_at"]), bookmark_count=count,
    )


def _bookmark_out(row) -> Bookmark:
    return Bookmark(
        id=row["id"],
        category_id=row["category_id"],
        category=row["category"] if "category" in row.keys() else None,
        news_id=row["news_id"],
        title=row["title"],
        date=_ts(row["published_at"]),
        image=row["image"],
        source=row["source_name"],
        link=row["link"],
        summary=row["summary"],
        author=row["author"],
        note=row["note"],
        created_at=_ts(row["created_at"]),
    )


_BOOKMARK_SELECT = """SELECT b.*, c.name AS category
                        FROM bookmarks b
                        LEFT JOIN bookmark_categories c ON c.id = b.category_id"""


@app.get("/api/bookmark-categories", response_model=list[BookmarkCategory], tags=["bookmarks"])
def list_categories():
    return [_category_out(r) for r in
            db.query("SELECT * FROM bookmark_categories ORDER BY name")]


@app.post("/api/bookmark-categories", response_model=BookmarkCategory, status_code=201,
          tags=["bookmarks"], dependencies=[Depends(require_admin)])
def create_category(payload: BookmarkCategoryIn):
    existing = db.query_one(
        "SELECT * FROM bookmark_categories WHERE name = ? COLLATE NOCASE", (payload.name,)
    )
    if existing is not None:
        raise HTTPException(409, f"a category named {payload.name!r} already exists")
    cur = db.execute(
        "INSERT INTO bookmark_categories (name, color, created_at) VALUES (?, ?, ?)",
        (payload.name.strip(), payload.color, time.time()),
    )
    return _category_out(
        db.query_one("SELECT * FROM bookmark_categories WHERE id = ?", (cur.lastrowid,))
    )


@app.patch("/api/bookmark-categories/{category_id}", response_model=BookmarkCategory,
           tags=["bookmarks"], dependencies=[Depends(require_admin)])
def update_category(category_id: int, payload: BookmarkCategoryIn):
    if db.query_one("SELECT 1 FROM bookmark_categories WHERE id = ?", (category_id,)) is None:
        raise HTTPException(404, "category not found")
    db.execute(
        "UPDATE bookmark_categories SET name = ?, color = ? WHERE id = ?",
        (payload.name.strip(), payload.color, category_id),
    )
    return _category_out(
        db.query_one("SELECT * FROM bookmark_categories WHERE id = ?", (category_id,))
    )


@app.delete("/api/bookmark-categories/{category_id}", tags=["bookmarks"],
            dependencies=[Depends(require_admin)])
def delete_category(category_id: int):
    """Remove a category. Its bookmarks are kept, just uncategorised."""
    if db.query_one("SELECT 1 FROM bookmark_categories WHERE id = ?", (category_id,)) is None:
        raise HTTPException(404, "category not found")
    freed = db.query_one(
        "SELECT COUNT(*) AS c FROM bookmarks WHERE category_id = ?", (category_id,)
    )["c"]
    db.execute("DELETE FROM bookmark_categories WHERE id = ?", (category_id,))
    return {"deleted": category_id, "bookmarks_uncategorised": freed}


@app.get("/api/bookmarks", response_model=list[Bookmark], tags=["bookmarks"])
def list_bookmarks(
    category_id: int | None = None,
    uncategorised: bool = False,
    q: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    where, params = ["1=1"], []
    if category_id is not None:
        where.append("b.category_id = ?")
        params.append(category_id)
    if uncategorised:
        where.append("b.category_id IS NULL")
    if q:
        where.append("(b.title LIKE ? OR IFNULL(b.note,'') LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    rows = db.query(
        f"{_BOOKMARK_SELECT} WHERE {' AND '.join(where)} "
        "ORDER BY b.created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    return [_bookmark_out(r) for r in rows]


@app.post("/api/bookmarks", response_model=Bookmark, status_code=201, tags=["bookmarks"],
          dependencies=[Depends(require_admin)])
def create_bookmark(payload: BookmarkIn):
    """Save an item. The bookmark keeps its own copy, so it survives the news
    being pruned by retention or removed with its source."""
    if payload.category_id is not None and db.query_one(
        "SELECT 1 FROM bookmark_categories WHERE id = ?", (payload.category_id,)
    ) is None:
        raise HTTPException(422, "category not found")

    if payload.news_id is not None:
        item = db.query_one("SELECT * FROM news WHERE id = ?", (payload.news_id,))
        if item is None:
            raise HTTPException(404, "news item not found")
        fields = dict(
            news_id=item["id"], title=item["title"], link=item["link"],
            image=item["image"], published_at=item["published_at"],
            summary=item["summary"], author=item["author"],
            source_name=item["source_name"],
        )
    else:
        if not payload.title or not payload.link:
            raise HTTPException(422, "give either news_id, or both title and link")
        fields = dict(
            news_id=None, title=payload.title, link=payload.link,
            image=payload.image, published_at=None, summary=None,
            author=None, source_name=payload.source,
        )

    existing = db.query_one("SELECT id FROM bookmarks WHERE link = ?", (fields["link"],))
    if existing is not None:
        # saving twice just moves it to the category asked for
        db.execute(
            "UPDATE bookmarks SET category_id = ?, note = COALESCE(?, note) WHERE id = ?",
            (payload.category_id, payload.note, existing["id"]),
        )
        return _bookmark_out(
            db.query_one(f"{_BOOKMARK_SELECT} WHERE b.id = ?", (existing["id"],))
        )

    cur = db.execute(
        """INSERT INTO bookmarks
             (category_id, news_id, title, link, image, published_at, summary,
              author, source_name, note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (payload.category_id, fields["news_id"], fields["title"], fields["link"],
         fields["image"], fields["published_at"], fields["summary"], fields["author"],
         fields["source_name"], payload.note, time.time()),
    )
    return _bookmark_out(db.query_one(f"{_BOOKMARK_SELECT} WHERE b.id = ?", (cur.lastrowid,)))


@app.patch("/api/bookmarks/{bookmark_id}", response_model=Bookmark, tags=["bookmarks"],
           dependencies=[Depends(require_admin)])
def update_bookmark(bookmark_id: int, payload: BookmarkPatch):
    if db.query_one("SELECT 1 FROM bookmarks WHERE id = ?", (bookmark_id,)) is None:
        raise HTTPException(404, "bookmark not found")
    fields = payload.model_dump(exclude_unset=True)
    if "category_id" in fields and fields["category_id"] is not None and db.query_one(
        "SELECT 1 FROM bookmark_categories WHERE id = ?", (fields["category_id"],)
    ) is None:
        raise HTTPException(422, "category not found")
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE bookmarks SET {assignments} WHERE id = ?",
                   list(fields.values()) + [bookmark_id])
    return _bookmark_out(db.query_one(f"{_BOOKMARK_SELECT} WHERE b.id = ?", (bookmark_id,)))


@app.delete("/api/bookmarks/{bookmark_id}", tags=["bookmarks"],
            dependencies=[Depends(require_admin)])
def delete_bookmark(bookmark_id: int):
    cur = db.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
    if cur.rowcount == 0:
        raise HTTPException(404, "bookmark not found")
    return {"deleted": bookmark_id}


# --- page export ----------------------------------------------------------

def _export(url: str, fmt: str, filename: str, refresh: bool):
    if not export.available():
        raise HTTPException(
            503, "page export needs a headless Chromium; install one or set NEWSROOM_CHROMIUM"
        )
    try:
        path = export.render(url, fmt, refresh=refresh)
    except export.ExportError as exc:
        raise HTTPException(502, str(exc)) from None
    media = "application/pdf" if fmt == "pdf" else "image/jpeg"
    return FileResponse(path, media_type=media, filename=filename)


def _safe_name(title: str, fmt: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip()[:70]
    cleaned = re.sub(r"\s+", "-", cleaned) or "article"
    return f"{cleaned}.{fmt}"


@app.get("/api/news/{news_id}/export.{fmt}", tags=["export"])
def export_news(news_id: int, fmt: str, refresh: bool = False):
    """Download the article behind a news item as a full-page PDF or JPG."""
    row = db.query_one("SELECT * FROM news WHERE id = ?", (news_id,))
    if row is None:
        raise HTTPException(404, "news item not found")
    if fmt not in export.FORMATS:
        raise HTTPException(422, "format must be pdf or jpg")
    return _export(row["link"], fmt, _safe_name(row["title"], fmt), refresh)


@app.get("/api/bookmarks/{bookmark_id}/export.{fmt}", tags=["export"])
def export_bookmark(bookmark_id: int, fmt: str, refresh: bool = False):
    """Same, for a saved item - which may outlive the news it came from."""
    row = db.query_one("SELECT * FROM bookmarks WHERE id = ?", (bookmark_id,))
    if row is None:
        raise HTTPException(404, "bookmark not found")
    if fmt not in export.FORMATS:
        raise HTTPException(422, "format must be pdf or jpg")
    return _export(row["link"], fmt, _safe_name(row["title"], fmt), refresh)


# --- sources --------------------------------------------------------------

@app.get("/api/sources", response_model=list[Source], tags=["sources"])
def list_sources():
    return [_source_out(r) for r in db.query("SELECT * FROM sources ORDER BY name")]


@app.post("/api/sources", response_model=Source, status_code=201, tags=["sources"],
          dependencies=[Depends(require_admin)])
def create_source(payload: SourceIn):
    if payload.plugin not in plugins.loaded_names():
        raise HTTPException(
            422, f"plugin {payload.plugin!r} is not installed; upload it first"
        )
    _check_interval(payload.interval_seconds)
    cur = db.execute(
        """INSERT INTO sources (name, plugin, config, enabled, interval_seconds, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            payload.name,
            payload.plugin,
            json.dumps(payload.config),
            int(payload.enabled),
            payload.interval_seconds,
            time.time(),
        ),
    )
    scheduler.kick()
    return _source_out(db.query_one("SELECT * FROM sources WHERE id = ?", (cur.lastrowid,)))


@app.get("/api/sources/{source_id}", response_model=Source, tags=["sources"])
def get_source(source_id: int):
    row = db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if row is None:
        raise HTTPException(404, "source not found")
    return _source_out(row)


@app.patch("/api/sources/{source_id}", response_model=Source, tags=["sources"],
           dependencies=[Depends(require_admin)])
def update_source(source_id: int, payload: SourcePatch):
    row = db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if row is None:
        raise HTTPException(404, "source not found")
    if payload.plugin is not None and payload.plugin not in plugins.loaded_names():
        raise HTTPException(422, f"plugin {payload.plugin!r} is not installed")
    _check_interval(payload.interval_seconds)

    fields = payload.model_dump(exclude_unset=True)
    if "config" in fields:
        fields["config"] = json.dumps(fields["config"])
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE sources SET {assignments} WHERE id = ?",
            list(fields.values()) + [source_id],
        )
    scheduler.kick()
    return _source_out(db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,)))


@app.delete("/api/sources/{source_id}", tags=["sources"],
            dependencies=[Depends(require_admin)])
def delete_source(source_id: int):
    """Remove a source; its stored news is deleted with it (ON DELETE CASCADE)."""
    row = db.query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if row is None:
        raise HTTPException(404, "source not found")
    removed = db.query_one(
        "SELECT COUNT(*) AS c FROM news WHERE source_id = ?", (source_id,)
    )["c"]
    db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return {"deleted": source_id, "news_removed": removed}


@app.post("/api/sources/{source_id}/refresh", tags=["sources"],
          dependencies=[Depends(require_admin)])
def refresh_source(source_id: int):
    """Poll one source right now, without waiting for the interval."""
    try:
        return scheduler.run_source(source_id, force=True)
    except LookupError:
        raise HTTPException(404, "source not found") from None


@app.post("/api/refresh", tags=["sources"], dependencies=[Depends(require_admin)])
def refresh_all():
    results = []
    for row in db.query("SELECT id FROM sources WHERE enabled = 1"):
        results.append(scheduler.run_source(row["id"], force=True))
    return {"sources": len(results), "results": results}


# --- plugins --------------------------------------------------------------

@app.get("/api/plugins", response_model=list[Plugin], tags=["plugins"])
def list_plugins():
    loaded = set(plugins.loaded_names())
    out = []
    for row in db.query("SELECT * FROM plugins ORDER BY name"):
        used = db.query_one(
            "SELECT COUNT(*) AS c FROM sources WHERE plugin = ?", (row["name"],)
        )["c"]
        out.append(
            Plugin(
                name=row["name"],
                version=row["version"],
                description=row["description"],
                author=row["author"],
                config_spec=json.loads(row["config_spec"] or "[]"),
                uploaded_at=_ts(row["uploaded_at"]),
                loaded=row["name"] in loaded,
                load_error=row["load_error"],
                sources_using=used,
            )
        )
    return out


@app.post("/api/plugins", response_model=Plugin, status_code=201, tags=["plugins"],
          dependencies=[Depends(require_admin)])
async def upload_plugin(file: UploadFile = File(...)):
    """Upload a `.py` source plugin.

    The file is validated, saved and imported immediately; an upload that fails
    to import leaves the previously installed version in place.
    """
    if not (file.filename or "").endswith(".py"):
        raise HTTPException(422, "plugin must be a .py file")
    raw = await file.read()
    if len(raw) > plugins.MAX_PLUGIN_BYTES:
        raise HTTPException(413, "plugin file too large (max 512 KiB)")
    try:
        code = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "plugin file must be UTF-8 text") from None
    try:
        meta = plugins.install(file.filename, code)
    except plugins.PluginRejected as exc:
        raise HTTPException(422, str(exc)) from None
    log.info("plugin %s installed from %s", meta["name"], file.filename)
    return next(p for p in list_plugins() if p.name == meta["name"])


@app.delete("/api/plugins/{name}", tags=["plugins"],
            dependencies=[Depends(require_admin)])
def delete_plugin(name: str, force: bool = False):
    row = db.query_one("SELECT * FROM plugins WHERE name = ?", (name,))
    if row is None:
        raise HTTPException(404, "plugin not installed")
    used = db.query_one(
        "SELECT COUNT(*) AS c FROM sources WHERE plugin = ?", (name,)
    )["c"]
    if used and not force:
        raise HTTPException(
            409, f"{used} source(s) still use this plugin; pass force=true to remove anyway"
        )
    plugins.uninstall(name)
    return {"deleted": name, "sources_orphaned": used}


@app.post("/api/plugins/{name}/test", tags=["plugins"],
          dependencies=[Depends(require_admin)])
def test_plugin(name: str, source_config: dict[str, Any]):
    """Dry-run a plugin with a config and return what it would import."""
    from . import sdk

    try:
        items = plugins.run(name, source_config)
    except sdk.PluginError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"count": len(items), "items": items[:10]}


# --- settings & status ----------------------------------------------------

@app.get("/api/settings", response_model=Settings, tags=["settings"])
def get_settings():
    return Settings(poll_interval_seconds=db.poll_interval())


@app.put("/api/settings", response_model=Settings, tags=["settings"],
         dependencies=[Depends(require_admin)])
def update_settings(payload: SettingsPatch):
    """Change the global polling interval; it applies to the very next cycle."""
    if payload.poll_interval_seconds is not None:
        _check_interval(payload.poll_interval_seconds)
        db.set_setting("poll_interval_seconds", payload.poll_interval_seconds)
        scheduler.kick()
        log.info("poll interval set to %ss", payload.poll_interval_seconds)
    return Settings(poll_interval_seconds=db.poll_interval())


@app.get("/api/status", tags=["settings"])
def status():
    return {
        "app": "myPrivateNewsRoom",
        "version": __version__,
        "poll_interval_seconds": db.poll_interval(),
        "min_poll_interval_seconds": config.MIN_POLL_INTERVAL,
        "retention_days": config.RETENTION_DAYS,
        "auth_required": bool(config.API_TOKEN),
        "plugins_loaded": plugins.loaded_names(),
        "export_available": export.available(),
        "bookmarks": db.query_one("SELECT COUNT(*) AS c FROM bookmarks")["c"],
        "bookmark_categories": db.query_one(
            "SELECT COUNT(*) AS c FROM bookmark_categories"
        )["c"],
        "sources": db.query_one("SELECT COUNT(*) AS c FROM sources")["c"],
        "sources_enabled": db.query_one(
            "SELECT COUNT(*) AS c FROM sources WHERE enabled = 1"
        )["c"],
        "news": db.query_one("SELECT COUNT(*) AS c FROM news")["c"],
        "scheduler": scheduler.stats(),
    }


@app.get("/api/health", tags=["settings"])
def health():
    return {"status": "ok", "time": dt.datetime.now(dt.timezone.utc)}


# --- web UI ---------------------------------------------------------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(config.WEB_DIR / "index.html")


@app.get("/app.js", include_in_schema=False)
def app_js():
    return FileResponse(config.WEB_DIR / "app.js", media_type="text/javascript")


@app.get("/style.css", include_in_schema=False)
def app_css():
    return FileResponse(config.WEB_DIR / "style.css", media_type="text/css")
