"""Plugin registry: load, validate and run user-uploaded source plugins.

Plugins are plain Python files dropped into ``PLUGIN_DIR`` (uploaded through
``POST /api/plugins``). They are imported into the process, so uploading a
plugin is equivalent to running code on the server - the upload endpoint is
protected by the admin token for that reason.
"""
from __future__ import annotations

import ast
import concurrent.futures
import importlib.util
import re
import sys
import threading
import time
import traceback
import types
from typing import Any

from . import config, db, sdk

_lock = threading.RLock()
_modules: dict[str, types.ModuleType] = {}

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,48}$")
MAX_PLUGIN_BYTES = 512 * 1024


class PluginRejected(Exception):
    """The uploaded file is not a usable plugin."""


# --- validation & loading -------------------------------------------------

def _module_name(name: str) -> str:
    return f"newsroom_plugin_{name}"


def validate_source_code(code: str) -> dict[str, Any]:
    """Statically check an uploaded file and return its PLUGIN metadata."""
    if len(code.encode()) > MAX_PLUGIN_BYTES:
        raise PluginRejected("plugin file is larger than 512 KiB")
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise PluginRejected(f"syntax error on line {exc.lineno}: {exc.msg}") from exc

    has_fetch = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "fetch"
        for node in tree.body
    )
    if not has_fetch:
        raise PluginRejected("plugin must define a top-level `fetch(config)` function")

    meta: dict[str, Any] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PLUGIN" for t in node.targets
        ):
            try:
                meta = ast.literal_eval(node.value)
            except ValueError as exc:
                raise PluginRejected(
                    "PLUGIN must be a literal dict (no computed values)"
                ) from exc
    if not isinstance(meta, dict):
        raise PluginRejected("plugin must define a `PLUGIN = {...}` metadata dict")
    name = meta.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise PluginRejected(
            "PLUGIN['name'] must be lowercase letters, digits and underscores"
        )
    return meta


def load_module(name: str, path) -> types.ModuleType:
    """(Re)import a plugin file under a private module name."""
    module_name = _module_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginRejected(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    module.sdk = sdk  # convenience: plugins may use `sdk.http_get(...)` directly
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(module_name, None)
        raise PluginRejected(f"import failed: {type(exc).__name__}: {exc}") from exc
    if not callable(getattr(module, "fetch", None)):
        raise PluginRejected("plugin has no callable `fetch`")
    return module


def register(name: str, filename: str, meta: dict, error: str | None = None) -> None:
    db.execute(
        """INSERT INTO plugins
             (name, filename, version, description, author, config_spec,
              uploaded_at, enabled, load_error)
           VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
           ON CONFLICT(name) DO UPDATE SET
             filename    = excluded.filename,
             version     = excluded.version,
             description = excluded.description,
             author      = excluded.author,
             config_spec = excluded.config_spec,
             uploaded_at = excluded.uploaded_at,
             load_error  = excluded.load_error""",
        (
            name,
            filename,
            str(meta.get("version", "")),
            str(meta.get("description", "")),
            str(meta.get("author", "")),
            __import__("json").dumps(meta.get("config_spec", [])),
            time.time(),
            error,
        ),
    )


def install(filename: str, code: str) -> dict:
    """Validate, persist to disk, import and register an uploaded plugin."""
    meta = validate_source_code(code)
    name = meta["name"]
    path = config.PLUGIN_DIR / f"{name}.py"
    with _lock:
        previous = path.read_text() if path.exists() else None
        path.write_text(code)
        try:
            module = load_module(name, path)
        except PluginRejected:
            # Roll back to whatever worked before, so a bad upload cannot
            # break an already-configured source.
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(previous)
                try:
                    _modules[name] = load_module(name, path)
                except PluginRejected:
                    _modules.pop(name, None)
            raise
        _modules[name] = module
        register(name, filename, meta)
    return meta


def uninstall(name: str) -> None:
    with _lock:
        (config.PLUGIN_DIR / f"{name}.py").unlink(missing_ok=True)
        _modules.pop(name, None)
        sys.modules.pop(_module_name(name), None)
        db.execute("DELETE FROM plugins WHERE name = ?", (name,))


def load_all() -> None:
    """Import every plugin on disk at startup, seeding the bundled ones once."""
    with _lock:
        for bundled in sorted(config.BUNDLED_PLUGIN_DIR.glob("*.py")):
            target = config.PLUGIN_DIR / bundled.name
            if not target.exists():
                target.write_text(bundled.read_text())

        for path in sorted(config.PLUGIN_DIR.glob("*.py")):
            name = path.stem
            try:
                meta = validate_source_code(path.read_text())
                _modules[name] = load_module(meta["name"], path)
                register(meta["name"], path.name, meta)
            except PluginRejected as exc:
                _modules.pop(name, None)
                register(name, path.name, {"name": name}, error=str(exc))


def get(name: str) -> types.ModuleType | None:
    with _lock:
        return _modules.get(name)


def loaded_names() -> list[str]:
    with _lock:
        return sorted(_modules)


# --- execution ------------------------------------------------------------

_ENTITY_RE = re.compile(r"&(?:#\d+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});")


def _unescape_once(text: str | None) -> str | None:
    """Decode entities a double-encoding feed left behind in plain text.

    Some feeds escape their markup twice, so `&amp;#8220;` arrives as the
    literal `&#8220;` after one decode and would otherwise be stored, and
    displayed, as that raw escape.
    """
    if not text or not _ENTITY_RE.search(text):
        return text
    import html as _html

    return _html.unescape(text)


def normalize(items: Any) -> list[dict]:
    """Coerce a plugin's return value into validated news dicts."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        raise sdk.PluginError("fetch() must return a list of dicts")
    out: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise sdk.PluginError(f"fetch() returned a {type(raw).__name__}, expected dict")
        title = str(raw.get("title") or "").strip()
        link = str(raw.get("link") or raw.get("url") or "").strip()
        if not title or not link:
            continue
        if not link.startswith(("http://", "https://")):
            continue
        image = raw.get("image") or raw.get("image_url") or raw.get("thumbnail")
        out.append(
            {
                "title": (_unescape_once(title) or title)[:500],
                "link": link,
                "image": sdk.absolutize(str(image) if image else None, link),
                "published_at": sdk.parse_date(
                    raw.get("published_at") or raw.get("date") or raw.get("published")
                ),
                "summary": _unescape_once(
                    sdk.strip_html(raw.get("summary") or raw.get("description"))
                ),
                "author": (str(raw["author"])[:200] if raw.get("author") else None),
                "guid": str(raw.get("guid") or raw.get("id") or link)[:500],
            }
        )
    return out


def run(name: str, source_config: dict) -> list[dict]:
    """Run one plugin with a hard timeout and return normalized items."""
    module = get(name)
    if module is None:
        raise sdk.PluginError(f"plugin {name!r} is not installed or failed to load")

    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=1, thread_name_prefix=f"plugin-{name}"
    )
    try:
        future = pool.submit(module.fetch, dict(source_config))
        try:
            result = future.result(timeout=config.PLUGIN_TIMEOUT)
        except concurrent.futures.TimeoutError:
            # The worker cannot be killed; let it finish in the background and
            # tear the pool down without waiting for it.
            raise sdk.PluginError(
                f"plugin timed out after {config.PLUGIN_TIMEOUT}s"
            ) from None
        except sdk.PluginError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise sdk.PluginError(
                f"{type(exc).__name__}: {exc}\n"
                + "".join(traceback.format_exception(exc)[-3:])
            ) from exc
    finally:
        pool.shutdown(wait=False)
    return normalize(result)
