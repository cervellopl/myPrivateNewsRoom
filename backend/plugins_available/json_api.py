"""Read news from any JSON endpoint by mapping its fields onto news items.

Example config for an API returning {"articles": [{"headline": ..., ...}]}:
    url        = https://api.example.com/v1/news
    items_path = articles
    title_key  = headline
    link_key   = url
    image_key  = urlToImage
    date_key   = publishedAt
"""
from newsroom import sdk

PLUGIN = {
    "name": "json_api",
    "version": "1.0",
    "description": "Map an arbitrary JSON news API onto news items.",
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "url", "label": "API URL", "type": "url", "required": True},
        {"key": "items_path", "label": "Path to the array (dot-separated)",
         "type": "string", "required": False, "placeholder": "data.articles"},
        {"key": "title_key", "label": "Title field", "type": "string",
         "required": False, "default": "title"},
        {"key": "link_key", "label": "Link field", "type": "string",
         "required": False, "default": "url"},
        {"key": "image_key", "label": "Image field", "type": "string",
         "required": False, "default": "image"},
        {"key": "date_key", "label": "Date field", "type": "string",
         "required": False, "default": "publishedAt"},
        {"key": "summary_key", "label": "Summary field", "type": "string",
         "required": False, "default": "description"},
        {"key": "auth_header", "label": "Authorization header value",
         "type": "secret", "required": False},
    ],
}


def _dig(data, path):
    for part in [p for p in (path or "").split(".") if p]:
        if isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data


def _pick(row, key):
    value = _dig(row, key) if "." in (key or "") else row.get(key)
    return value if isinstance(value, (str, int, float)) else None


def fetch(config):
    url = (config.get("url") or "").strip()
    if not url:
        raise sdk.PluginError("config key 'url' is required")

    headers = {"Accept": "application/json"}
    if config.get("auth_header"):
        headers["Authorization"] = config["auth_header"]

    payload = sdk.http_get_json(url, headers=headers)
    rows = _dig(payload, config.get("items_path")) if config.get("items_path") else payload
    if isinstance(rows, dict):
        rows = next((v for v in rows.values() if isinstance(v, list)), None)
    if not isinstance(rows, list):
        raise sdk.PluginError(
            "could not find an array of items - set 'items_path' to point at it"
        )

    title_key = config.get("title_key") or "title"
    link_key = config.get("link_key") or "url"
    image_key = config.get("image_key") or "image"
    date_key = config.get("date_key") or "publishedAt"
    summary_key = config.get("summary_key") or "description"

    items = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        link = _pick(row, link_key)
        if not link:
            continue
        items.append(
            {
                "title": _pick(row, title_key) or str(link),
                "link": str(link),
                "image": _pick(row, image_key),
                "published_at": _pick(row, date_key),
                "summary": _pick(row, summary_key),
                "author": _pick(row, "author"),
                "guid": _pick(row, "id") or str(link),
            }
        )
    return items
