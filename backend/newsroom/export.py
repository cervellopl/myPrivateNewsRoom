"""Export an article as a full-page PDF or JPG.

A headless Chromium prints the article to PDF; the JPG is rendered from that
same PDF and stitched into one tall image, which is what makes it a *full page*
capture rather than a screenshot of the visible window.

Renders are cached on disk by URL and format, so asking twice is cheap, and the
cache is swept oldest-first once it grows past its limit.
"""
from __future__ import annotations

import glob
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("newsroom.export")

FORMATS = ("pdf", "jpg")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class ExportError(Exception):
    """Rendering failed, with a message worth showing the user."""


def chromium_path() -> str | None:
    """The browser used for rendering, or None when none is available."""
    if config.CHROMIUM == "":
        for candidate in ("chromium", "chromium-browser", "google-chrome",
                          "google-chrome-stable", "chrome"):
            found = shutil.which(candidate)
            if found:
                return found
        return None
    return config.CHROMIUM if os.path.exists(config.CHROMIUM) else None


def available() -> bool:
    return chromium_path() is not None


def _cache_path(url: str, fmt: str) -> Path:
    digest = hashlib.sha256(url.encode()).hexdigest()[:20]
    return config.EXPORT_DIR / f"{digest}.{fmt}"


def _lock_for(key: str) -> threading.Lock:
    """One lock per file, so two requests for the same page render once."""
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _run(command: list[str]) -> None:
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=config.EXPORT_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        raise ExportError(
            f"rendering timed out after {config.EXPORT_TIMEOUT}s"
        ) from None
    if result.returncode != 0:
        tail = (result.stderr or b"").decode(errors="replace").strip().splitlines()
        # Chromium is noisy about GPU and dbus on a headless box; those lines are
        # not the failure, so only the last one is worth reporting.
        raise ExportError(f"{Path(command[0]).name} failed: {tail[-1] if tail else 'unknown error'}")


def _render_pdf(url: str, target: Path) -> None:
    browser = chromium_path()
    if browser is None:
        raise ExportError("no Chromium found; set NEWSROOM_CHROMIUM to its path")

    with tempfile.TemporaryDirectory(dir=config.EXPORT_DIR) as work:
        # Chromium writes to its own profile dir, which must be writable
        temporary = Path(work) / "out.pdf"
        _run([
            browser, "--headless", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--no-pdf-header-footer",
            "--disable-dev-shm-usage",
            f"--user-data-dir={work}/profile",
            "--virtual-time-budget=15000",
            f"--print-to-pdf={temporary}", url,
        ])
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise ExportError("the browser produced an empty PDF")
        shutil.move(str(temporary), target)


def _render_jpg(url: str, target: Path) -> None:
    """Render the PDF, then stitch its pages into a single tall image."""
    from PIL import Image

    pdf = _cache_path(url, "pdf")
    if not pdf.exists():
        _render_pdf(url, pdf)

    with tempfile.TemporaryDirectory(dir=config.EXPORT_DIR) as work:
        prefix = Path(work) / "pg"
        _run(["pdftoppm", "-jpeg", "-r", "100", "-jpegopt", "quality=82",
              str(pdf), str(prefix)])
        pages = sorted(glob.glob(f"{prefix}-*.jpg"))
        if not pages:
            raise ExportError("could not convert the PDF into images (is poppler-utils installed?)")

        images = [Image.open(p) for p in pages]
        width = max(i.width for i in images)
        height = sum(i.height for i in images)
        canvas = Image.new("RGB", (width, height), "white")
        offset = 0
        for image in images:
            canvas.paste(image, (0, offset))
            offset += image.height
        temporary = Path(work) / "full.jpg"
        canvas.save(temporary, "JPEG", quality=82, optimize=True)
        shutil.move(str(temporary), target)


def render(url: str, fmt: str, *, refresh: bool = False) -> Path:
    """Return a cached render of `url`, producing it if needed."""
    if fmt not in FORMATS:
        raise ExportError(f"unsupported format {fmt!r}; use pdf or jpg")
    if not url.startswith(("http://", "https://")):
        raise ExportError("only http(s) pages can be exported")

    target = _cache_path(url, fmt)
    with _lock_for(str(target)):
        if target.exists() and not refresh:
            os.utime(target, None)          # keep freshly used files in the cache
            return target
        log.info("rendering %s as %s", url, fmt)
        started = time.time()
        if fmt == "pdf":
            _render_pdf(url, target)
        else:
            _render_jpg(url, target)
        log.info("rendered %s in %.1fs (%.1f kB)", fmt, time.time() - started,
                 target.stat().st_size / 1024)
    sweep_cache()
    return target


def sweep_cache() -> int:
    """Drop the least recently used renders once the cache outgrows its limit."""
    files = sorted(
        (f for f in config.EXPORT_DIR.glob("*.*") if f.suffix[1:] in FORMATS),
        key=lambda f: f.stat().st_mtime,
    )
    total = sum(f.stat().st_size for f in files)
    limit = config.EXPORT_CACHE_MB * 1024 * 1024
    removed = 0
    while total > limit and files:
        oldest = files.pop(0)
        total -= oldest.stat().st_size
        oldest.unlink(missing_ok=True)
        removed += 1
    if removed:
        log.info("swept %s cached render(s)", removed)
    return removed
