"""Scrape a listing page for links and turn each into a news item.

Useful for sites that publish no feed: point it at a section page, give it a
regex that matches article URLs, and it collects title, date and premier image
from each article's own OpenGraph / schema.org metadata.
"""
from newsroom import sdk

PLUGIN = {
    "name": "html_scraper",
    "version": "1.4",
    "description": "Scrape a listing page for article links and read their metadata.",
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "url", "label": "Listing page URL", "type": "url", "required": True},
        {"key": "link_pattern", "label": "Article URL regex", "type": "string",
         "required": True, "placeholder": r"/artykul/\d+"},
        {"key": "limit", "label": "Max articles per run", "type": "int",
         "required": False, "default": 15},
        {"key": "title_strip", "label": "Remove this from titles (regex)",
         "type": "string", "required": False,
         "placeholder": r"^Site\.pl\s*-\s*"},
        {"key": "link_exclude", "label": "Skip links matching (regex)",
         "type": "string", "required": False, "placeholder": r"/tags/|/page/"},
        {"key": "max_age_days", "label": "Ignore articles older than (days)",
         "type": "int", "required": False, "default": 0},
    ],
}


def fetch(config):
    url = (config.get("url") or "").strip()
    pattern = (config.get("link_pattern") or "").strip()
    if not url or not pattern:
        raise sdk.PluginError("config keys 'url' and 'link_pattern' are required")

    import re
    import time

    limit = int(config.get("limit") or 15)
    # a listing page mixes real articles with nav, tag and promo links; ask for
    # more than needed so the filters below still leave a full run
    links = sdk.find_links(sdk.http_get(url), url, pattern, limit=limit * 3)

    skip = (config.get("link_exclude") or "").strip()
    if skip:
        try:
            skip_rx = re.compile(skip, re.I)
        except re.error as exc:
            raise sdk.PluginError(f"invalid link_exclude pattern: {exc}") from exc
        links = [l for l in links if not skip_rx.search(l)]

    # Evergreen promos sit alongside the news on many listing pages. An age
    # limit removes them without guessing at their URLs - and an item with no
    # date at all is not news either, so it goes too.
    max_age_days = int(config.get("max_age_days") or 0)
    oldest = time.time() - max_age_days * 86400 if max_age_days else None

    # many sites prefix every page title with their own name
    strip = config.get("title_strip")
    if strip:
        try:
            strip = re.compile(strip, re.I)
        except re.error as exc:
            raise sdk.PluginError(f"invalid title_strip pattern: {exc}") from exc

    items = []
    for link in links:
        article = sdk.read_article(link)
        if not article:  # an unreachable article must not sink the whole run
            continue
        if oldest is not None:
            published = article.get("published_at")
            if published is None or published < oldest:
                continue
        if strip:
            cleaned = strip.sub("", article["title"]).strip(" -–—|")
            article["title"] = cleaned or article["title"]
        items.append(article)
        if len(items) >= limit:
            break

    # A picture shared by several articles is the site's stand-in - a section
    # background or a default social image - not any one article's own.
    counts = {}
    for item in items:
        if item.get("image"):
            counts[item["image"]] = counts.get(item["image"], 0) + 1
    for item in items:
        if counts.get(item.get("image"), 0) >= 3:
            item["image"] = None

    return items
