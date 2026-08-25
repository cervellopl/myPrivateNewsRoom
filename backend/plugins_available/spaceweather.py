"""Spaceweather.com - daily solar and near-Earth space news.

The site publishes no feed. Its front page is one long document holding a few
stories a day, each opening with an ALL-CAPS headline that ends in a colon:

    <b>POTENTIALLY DANGEROUS SUNSPOT:</b> Sunspot AR4295 has a delta-class …

Each story becomes an item. Individual stories have no permalink of their own,
so items link to that day's archive page, which keeps them readable after the
front page has moved on. Older days can be collected too, by walking back
through the archive.
"""
import datetime
import re

from newsroom import sdk

PLUGIN = {
    "name": "spaceweather",
    "version": "1.0",
    "description": "Spaceweather.com daily stories: solar flares, auroras, meteors, near-Earth asteroids.",
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "days_back", "label": "Also read this many earlier days",
         "type": "int", "required": False, "default": 0},
        {"key": "include", "label": "Keep only headlines matching (regex)",
         "type": "string", "required": False, "placeholder": "AURORA|FLARE"},
        {"key": "base_url", "label": "Site URL", "type": "url", "required": False,
         "default": "https://www.spaceweather.com/"},
    ],
}

# an ALL-CAPS headline ending in a colon, inside <b> or <strong>
_HEADLINE_RE = re.compile(
    r"<(?:b|strong)>\s*(?:<font[^>]*>)?\s*([A-Z][A-Z0-9 ,'\.\-/&]{6,70}:)",
    re.S,
)
_IMG_RE = re.compile(r"""<img[^>]*src\s*=\s*["']([^"']+)["']""", re.I)
# some stories only link their picture ("see the movie") instead of inlining it
_LINKED_IMAGE_RE = re.compile(
    r"""<a[^>]*href\s*=\s*["']([^"']+\.(?:jpg|jpeg|png|gif|webp))["']""", re.I
)
# site furniture and house ads that must not be mistaken for a story's picture
_FURNITURE = re.compile(
    r"(spacer|logo|thumb|banner|bannerlet|button|nublokr|POES/|site_images/"
    r"|timemachine|arrow|advert)", re.I
)


def _archive_url(base, day):
    return (f"{base.rstrip('/')}/archive.php?view=1"
            f"&day={day.day:02d}&month={day.month:02d}&year={day.year}")


def _story_image(chunk, page_url):
    """The story's own picture: inlined if there is one, linked otherwise."""
    for pattern in (_IMG_RE, _LINKED_IMAGE_RE):
        for candidate in pattern.findall(chunk):
            if _FURNITURE.search(candidate):
                continue
            return sdk.absolutize(candidate, page_url)
    return None


def _readable(headline):
    """SHOUTED HEADLINE -> Shouted Headline, without mangling apostrophes."""
    if not headline.isupper():
        return headline
    titled = headline.title()
    return re.sub(r"(?<=\w)'(\w)", lambda m: "'" + m.group(1).lower(), titled)


def _stories(html, page_url, day, include):
    """Split one day's page into its stories."""
    matches = list(_HEADLINE_RE.finditer(html))
    items = []
    for index, match in enumerate(matches):
        headline = match.group(1).strip().rstrip(":").strip()
        if include and not include.search(headline):
            continue

        # the story runs until the next headline, capped so a trailing story
        # cannot swallow the rest of the page
        end = matches[index + 1].start() if index + 1 < len(matches) else match.end() + 4000
        chunk = html[match.end():end]

        # a headline in title case reads better in a feed than shouting
        title = _readable(headline)
        items.append(
            {
                "title": title,
                "link": _archive_url(page_url, day),
                "image": _story_image(chunk, page_url),
                "published_at": datetime.datetime(
                    day.year, day.month, day.day, tzinfo=datetime.timezone.utc
                ).timestamp(),
                "summary": sdk.strip_html(chunk, 400),
                "author": "Spaceweather.com",
                # stories have no id of their own; the day plus the headline is
                # stable enough to keep the same story from being stored twice
                "guid": f"spaceweather:{day.isoformat()}:{headline[:60]}",
            }
        )
    return items


def fetch(config):
    base = (config.get("base_url") or PLUGIN["config_spec"][2]["default"]).strip()
    days_back = max(0, min(int(config.get("days_back") or 0), 14))
    include = re.compile(config["include"], re.I) if config.get("include") else None

    today = datetime.datetime.now(datetime.timezone.utc).date()
    items = _stories(sdk.http_get(base), base, today, include)

    for offset in range(1, days_back + 1):
        day = today - datetime.timedelta(days=offset)
        try:
            page = sdk.http_get(_archive_url(base, day))
        except sdk.PluginError:
            continue          # a missing archive day must not fail the run
        items.extend(_stories(page, base, day, include))

    if not items:
        raise sdk.PluginError(
            "no stories found - the Spaceweather.com layout may have changed"
        )
    return items
