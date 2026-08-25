"""Runtime configuration.

Everything here can be overridden with environment variables; the polling
interval can additionally be changed at runtime through the API (it is stored
in the ``settings`` table and the value in the DB wins).
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("NEWSROOM_DATA_DIR", BASE_DIR / "data"))
PLUGIN_DIR = Path(os.environ.get("NEWSROOM_PLUGIN_DIR", BASE_DIR / "plugins"))
BUNDLED_PLUGIN_DIR = BASE_DIR / "plugins_available"
DB_PATH = Path(os.environ.get("NEWSROOM_DB", DATA_DIR / "newsroom.db"))
WEB_DIR = Path(__file__).resolve().parent / "web"

# Admin endpoints (plugin upload, source/settings mutations) require this token
# in the `X-API-Token` header. Empty string disables the check (dev only).
API_TOKEN = os.environ.get("NEWSROOM_API_TOKEN", "")

# Default polling interval in seconds, used until the user changes it via the API.
DEFAULT_POLL_INTERVAL = int(os.environ.get("NEWSROOM_POLL_INTERVAL", "900"))

# Minimum interval accepted from the API, so a typo cannot hammer a source.
MIN_POLL_INTERVAL = int(os.environ.get("NEWSROOM_MIN_POLL_INTERVAL", "60"))

# How long a single plugin run may take before it is abandoned.
PLUGIN_TIMEOUT = int(os.environ.get("NEWSROOM_PLUGIN_TIMEOUT", "60"))

# Retention: news older than this many days are pruned. 0 = keep forever.
RETENTION_DAYS = int(os.environ.get("NEWSROOM_RETENTION_DAYS", "90"))

# Page capture: an article can be exported as a full-page PDF or JPG. Rendering
# is done by a headless Chromium; set NEWSROOM_CHROMIUM if it is not on PATH,
# or to an empty string to switch the feature off.
CHROMIUM = os.environ.get("NEWSROOM_CHROMIUM", "")
EXPORT_DIR = Path(os.environ.get("NEWSROOM_EXPORT_DIR", DATA_DIR / "exports"))
EXPORT_TIMEOUT = int(os.environ.get("NEWSROOM_EXPORT_TIMEOUT", "120"))
# Rendered files are cached; older ones are swept once the cache exceeds this.
EXPORT_CACHE_MB = int(os.environ.get("NEWSROOM_EXPORT_CACHE_MB", "512"))

USER_AGENT = os.environ.get(
    "NEWSROOM_USER_AGENT",
    "myPrivateNewsRoom/1.0 (+https://localhost; personal news aggregator)",
)

for _d in (DATA_DIR, PLUGIN_DIR, EXPORT_DIR):
    _d.mkdir(parents=True, exist_ok=True)
