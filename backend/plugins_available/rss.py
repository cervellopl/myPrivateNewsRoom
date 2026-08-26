"""Generic RSS / Atom source plugin for myPrivateNewsRoom.

Reads any RSS 2.0 or Atom feed. When the feed carries no image for an entry
and `fetch_images` is enabled, the article page is opened and its og:image is
used as the premier image.
"""
from newsroom import sdk

PLUGIN = {
    "name": "rss",
    "version": "1.3",
    "description": "Generic RSS/Atom/RDF feed reader, with title and link filters and an og:image fallback.",
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "url", "label": "Feed URL", "type": "url", "required": True,
         "placeholder": "https://example.com/feed.xml"},
        {"key": "limit", "label": "Max items per run", "type": "int",
         "required": False, "default": 30},
        {"key": "fetch_images", "label": "Open articles to find missing images",
         "type": "bool", "required": False, "default": True},
        {"key": "include", "label": "Only keep titles matching (regex)",
         "type": "string", "required": False},
        {"key": "link_include", "label": "Only keep links matching (regex)",
         "type": "string", "required": False,
         "placeholder": r"/astronomy-news/"},
        {"key": "link_exclude", "label": "Drop links matching (regex)",
         "type": "string", "required": False},
        {"key": "title_strip", "label": "Remove this from titles (regex)",
         "type": "string", "required": False,
         "placeholder": r"\s+-\s+Reuters$"},
    ],
}


def fetch(config):
    url = (config.get("url") or "").strip()
    if not url:
        raise sdk.PluginError("config key 'url' is required")

    items = sdk.parse_feed(sdk.http_get(url), base_url=url)

    import re

    # Title cleanup runs first, so the filters below apply to the title as it
    # will be stored - not to a prefix the reader never sees.
    strip = (config.get("title_strip") or "").strip()
    if strip:
        try:
            strip_rx = re.compile(strip, re.I)
        except re.error as exc:
            raise sdk.PluginError(f"invalid title_strip pattern: {exc}") from exc
        for item in items:
            cleaned = strip_rx.sub("", item["title"] or "").strip(" -–—|")
            if cleaned:
                item["title"] = cleaned

    pattern = (config.get("include") or "").strip()
    if pattern:
        rx = re.compile(pattern, re.I)
        items = [i for i in items if rx.search(i["title"] or "")]

    # Filtering on the link picks one section out of a site-wide feed, which is
    # the only way in when a publisher serves no per-section feed.
    link_include = (config.get("link_include") or "").strip()
    if link_include:
        rx = re.compile(link_include, re.I)
        items = [i for i in items if rx.search(i["link"] or "")]

    link_exclude = (config.get("link_exclude") or "").strip()
    if link_exclude:
        rx = re.compile(link_exclude, re.I)
        items = [i for i in items if not rx.search(i["link"] or "")]

    limit = int(config.get("limit") or 30)
    items = items[:limit]

    if config.get("fetch_images", True):
        for item in items:
            if item.get("image"):
                continue
            try:
                page = sdk.http_get(item["link"], timeout=15)
                item["image"] = sdk.find_lead_image(page, item["link"])
            except sdk.PluginError:
                pass  # an unreachable article must not fail the whole source
    return items
