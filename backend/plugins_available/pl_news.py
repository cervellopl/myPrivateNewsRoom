"""Polish news sites for myPrivateNewsRoom.

A catalogue of Polish outlets with their working feed addresses, so a source
can be set up by picking names ("tvn24, onet, rmf24") instead of hunting for
feed URLs. Handles what the Polish feeds do differently: tracking parameters
glued onto article links, Polish month names in dates, and entries that carry
no image until the article page itself is opened.

Config keys
    outlets       comma-separated ids from the catalogue, a group name
                  (pap, biznes, tech, ogolne), or "all"
    limit_per_outlet   how many newest items to take from each outlet
    fetch_images  open articles that have no image and read their og:image
    include       keep only titles matching this regex (e.g. "Polska|sejm")
    exclude       drop titles matching this regex (e.g. "sport|celebryci")
    custom_feeds  extra feeds as "Nazwa|https://…", separated by ; or newlines

Every item keeps its outlet in the `author` field when the feed itself names
no author, so items stay attributable when one source aggregates several sites.
"""
import re

from newsroom import sdk

# id -> outlet definition. "feed" outlets are read as RSS/Atom; "scrape"
# outlets have no feed at all, so their listing page is read and each matching
# article is opened for its own metadata. All addresses verified to work.
def _feed(name, url):
    return {"name": name, "mode": "feed", "url": url}


def _scrape(name, url, pattern):
    return {"name": name, "mode": "scrape", "url": url, "pattern": pattern}


def _listing(name, url, pattern):
    """For sites whose article pages carry no metadata: read the listing only."""
    return {"name": name, "mode": "listing", "url": url, "pattern": pattern}


# Wyborcza article addresses: /<dział>/7,<id>,<id>,<slug>.html
_WYBORCZA_PATTERN = (
    r"wyborcza\.(pl|biz)/[a-zA-Z0-9_/-]*7,\d+,\d+,[^\"']*\.html$"
)


OUTLETS = {
    # general news
    "tvn24":           _feed("TVN24", "https://tvn24.pl/najnowsze.xml"),
    "onet":            _feed("Onet Wiadomości", "https://wiadomosci.onet.pl/.feed"),
    "interia":         _feed("Interia Wydarzenia", "https://wydarzenia.interia.pl/feed"),
    "polsatnews":      _feed("Polsat News", "https://www.polsatnews.pl/rss/wszystkie.xml"),
    "rmf24":           _feed("RMF24 Fakty", "https://www.rmf24.pl/fakty/feed"),
    "gazeta":          _feed("Gazeta.pl Wiadomości", "https://wiadomosci.gazeta.pl/pub/rss/wiadomosci.xml"),
    "rp":              _feed("Rzeczpospolita", "https://www.rp.pl/rss_main"),
    "newsweek":        _feed("Newsweek Polska", "https://www.newsweek.pl/rss.xml"),
    "wprost":          _feed("Wprost", "https://www.wprost.pl/rss"),
    "dorzeczy":        _feed("Do Rzeczy", "https://dorzeczy.pl/feed"),
    "wp":              _feed("Wirtualna Polska", "https://wiadomosci.wp.pl/rss.xml"),
    # TVP Info's feed is served from an odd path and is slightly malformed
    # (undeclared media: prefix, bare ampersands); the SDK repairs it.
    "tvpinfo":         _feed("TVP Info", "https://www.tvp.info/tvp.info/rss+xml.php"),
    # RMF FM (the radio site, distinct from rmf24 above) has no feed either
    "rmffm":           _scrape(
        "RMF FM",
        "https://www.rmf.fm/",
        r"rmf\.fm/[a-z0-9-]+/news,n\d+,[a-z0-9-]+\.html$",
    ),
    # Radio ZET publishes no feed - its listing page is scraped instead
    "radiozet":        _scrape(
        "Radio ZET Wiadomości",
        "https://wiadomosci.radiozet.pl/",
        r"wiadomosci\.radiozet\.pl/(polska|swiat|polityka|gospodarka|nauka"
        r"|technologia|zdrowie|spoleczenstwo)/[a-z0-9-]{15,}$",
    ),
    # Gazeta Wyborcza (Agora) - distinct from Gazeta.pl above. It publishes no
    # feed and its article pages are an empty shell behind the paywall, so only
    # the listing page can be read: headline, link and often the lead image.
    "wyborcza":        _listing(
        "Gazeta Wyborcza", "https://wyborcza.pl/0,0.html", _WYBORCZA_PATTERN,
    ),
    "wyborcza_biz":    _listing(
        "Wyborcza Biznes", "https://wyborcza.biz/biznes/0,0.html", _WYBORCZA_PATTERN,
    ),
    # PAP: the main wire (www.pap.pl) sits behind bot protection and serves no
    # content to non-browser clients, so these are its open sibling services.
    "pap_samorzad":    _feed("PAP Samorząd", "https://samorzad.pap.pl/rss.xml"),
    "pap_zdrowie":     _feed("PAP Zdrowie", "https://zdrowie.pap.pl/rss.xml"),
    "pap_nauka":       _feed("PAP Nauka w Polsce", "https://naukawpolsce.pl/rss.xml"),
    # business
    "money":           _feed("Money.pl", "https://www.money.pl/rss/rss.xml"),
    "bankier":         _feed("Bankier.pl", "https://www.bankier.pl/rss/wiadomosci.xml"),
    "businessinsider": _feed("Business Insider Polska", "https://businessinsider.com.pl/.feed"),
    # the feed is advertised only in the page head, at an unusual path
    "gazetaprawna":    _feed("Dziennik Gazeta Prawna", "https://www.gazetaprawna.pl/.feed"),
    # technology
    "spidersweb":      _feed("Spider's Web", "https://spidersweb.pl/api/post/feed/feed-gn"),
    "antyweb":         _feed("Antyweb", "https://antyweb.pl/feed"),
    "niebezpiecznik":  _feed("Niebezpiecznik", "http://feeds.feedburner.com/niebezpiecznik/"),
}

# Groups usable in place of outlet ids, e.g. outlets = "pap" or "tech".
GROUPS = {
    "pap":      ["pap_samorzad", "pap_zdrowie", "pap_nauka"],
    "wyborcza": ["wyborcza", "wyborcza_biz"],
    "biznes":   ["money", "bankier", "businessinsider", "wyborcza_biz",
                 "gazetaprawna"],
    "tech":     ["spidersweb", "antyweb", "niebezpiecznik"],
    "ogolne":   ["tvn24", "onet", "interia", "polsatnews", "rmf24", "rmffm",
                 "radiozet", "tvpinfo", "gazeta", "wp", "rp", "newsweek",
                 "wprost", "dorzeczy", "wyborcza"],
    "radio":    ["rmf24", "rmffm", "radiozet"],
}

PLUGIN = {
    "name": "pl_news",
    "version": "1.5",
    "description": (
        "Polish news sites by name - TVN24, TVP Info, Onet, Interia, Polsat "
        "News, RMF24, RMF FM, Radio ZET, Gazeta.pl, WP, Rzeczpospolita, "
        "Newsweek, Wprost, Dziennik Gazeta Prawna, "
        "Do Rzeczy, Gazeta Wyborcza + Wyborcza Biznes (headlines only), PAP "
        "(Samorząd/Zdrowie/Nauka), Money.pl, Bankier, Business Insider PL, "
        "Spider's Web, Antyweb, Niebezpiecznik. Groups: pap, wyborcza, biznes, "
        "tech, radio, ogolne."
    ),
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "outlets", "label": "Outlets (comma-separated, or 'all')",
         "type": "string", "required": True, "default": "tvn24,onet,rmf24",
         "placeholder": "tvn24,onet,rmf24"},
        {"key": "limit_per_outlet", "label": "Items per outlet", "type": "int",
         "required": False, "default": 10},
        {"key": "fetch_images", "label": "Open articles to find missing images",
         "type": "bool", "required": False, "default": True},
        {"key": "include", "label": "Keep only titles matching (regex)",
         "type": "string", "required": False},
        {"key": "exclude", "label": "Drop titles matching (regex)",
         "type": "string", "required": False, "placeholder": "sport|plotki"},
        {"key": "custom_feeds", "label": "Extra feeds ('Nazwa|https://…', ; separated)",
         "type": "string", "required": False},
    ],
}

# Polish month names, for the few feeds that localise their dates.
_MONTHS = {
    "stycznia": 1, "styczeń": 1, "lutego": 2, "luty": 2, "marca": 3, "marzec": 3,
    "kwietnia": 4, "kwiecień": 4, "maja": 5, "maj": 5, "czerwca": 6, "czerwiec": 6,
    "lipca": 7, "lipiec": 7, "sierpnia": 8, "sierpień": 8, "września": 9,
    "wrzesień": 9, "października": 10, "październik": 10, "listopada": 11,
    "listopad": 11, "grudnia": 12, "grudzień": 12,
}
_PL_DATE_RE = re.compile(
    r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})(?:[,\s]+(\d{1,2}):(\d{2}))?", re.I
)

# Tracking noise Polish portals append to article links; stripping it keeps the
# deduplication stable when the same article is linked twice.
_TRACKING = re.compile(
    r"^(utm_[a-z_]+|srcc?|src|source|xtor|ref|fbclid|gclid|_ga|s_id|iso)$", re.I
)


def _parse_pl_date(text):
    """Fall back to Polish date wording when the standard parsers fail."""
    match = _PL_DATE_RE.search(text or "")
    if not match:
        return None
    day, month_name, year, hour, minute = match.groups()
    month = _MONTHS.get(month_name.lower())
    if not month:
        return None
    import datetime

    return datetime.datetime(
        int(year), month, int(day), int(hour or 0), int(minute or 0),
        tzinfo=datetime.timezone.utc,
    ).timestamp()


def clean_link(url):
    """Drop tracking query parameters, keeping the real article address."""
    import urllib.parse

    if not url:
        return url
    parts = urllib.parse.urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(key)
    ]
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), "")
    )


def _clean_author(raw, outlet):
    """Keep a real byline, fall back to the outlet name otherwise.

    Some feeds (PAP's services especially) put the CMS login of whoever
    published the item - "tryton", "admin", "mrozalska" - into the author
    field, and others repeat the site's own address. Neither tells the reader
    anything, so the outlet name is used instead.
    """
    name = (raw or "").strip()
    if not name or len(name) > 40:
        return outlet
    if "http" in name.lower() or re.search(r"\.(pl|com|eu|net|org)\b", name, re.I):
        return outlet
    # a byline has at least two capitalised words: "Anna Kowalska"
    words = name.split()
    if len(words) < 2 or not all(w[:1].isupper() for w in words if w):
        return outlet
    return name


def _selected_feeds(config):
    """Resolve the `outlets` and `custom_feeds` config into (name, url) pairs."""
    raw = (config.get("outlets") or "").strip()
    selected, unknown = [], []

    if raw.lower() in ("all", "*", "wszystkie"):
        selected.extend(OUTLETS.values())
    elif raw:
        for key in re.split(r"[,\s]+", raw):
            key = key.lower()
            if not key:
                continue
            if key in GROUPS:
                selected.extend(OUTLETS[k] for k in GROUPS[key])
            elif key in OUTLETS:
                selected.append(OUTLETS[key])
            else:
                unknown.append(key)
        if unknown:
            raise sdk.PluginError(
                f"unknown outlet(s): {', '.join(unknown)}. "
                f"Outlets: {', '.join(sorted(OUTLETS))}. "
                f"Groups: {', '.join(sorted(GROUPS))}"
            )

    for chunk in re.split(r"[;\n]+", config.get("custom_feeds") or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        name, _, url = chunk.partition("|")
        if not url.strip():
            raise sdk.PluginError(f"custom feed {chunk!r} must look like 'Nazwa|https://…'")
        selected.append(_feed(name.strip() or url.strip(), url.strip()))

    if not selected:
        raise sdk.PluginError(
            "pick at least one outlet, e.g. 'tvn24,onet', a group "
            f"({', '.join(sorted(GROUPS))}), or 'all'"
        )

    seen, unique = set(), []
    for outlet in selected:
        if outlet["url"] in seen:
            continue
        seen.add(outlet["url"])
        unique.append(outlet)
    return unique


def fetch(config):
    feeds = _selected_feeds(config)
    limit = int(config.get("limit_per_outlet") or 10)
    include = re.compile(config["include"], re.I) if config.get("include") else None
    exclude = re.compile(config["exclude"], re.I) if config.get("exclude") else None

    items, failures, seen_guids = [], [], set()
    for outlet_def in feeds:
        outlet, url = outlet_def["name"], outlet_def["url"]
        try:
            if outlet_def["mode"] == "listing":
                # the article pages hold no metadata, so the listing is all
                # there is: headline, link, and the image belonging to it
                entries = sdk.read_listing(
                    sdk.http_get(url), url, outlet_def["pattern"], limit=limit
                )
                for entry in entries:
                    # their article pages are empty shells whose og:image is the
                    # site wordmark, so there is nothing to gain by opening them
                    entry["_no_image_lookup"] = True
                if not entries:
                    raise sdk.PluginError(
                        "listing page matched no headlines - the site layout "
                        "may have changed"
                    )
            elif outlet_def["mode"] == "scrape":
                # no feed on this site: read the listing page, then each article
                links = sdk.find_links(
                    sdk.http_get(url), url, outlet_def["pattern"], limit=limit * 2
                )
                if not links:
                    raise sdk.PluginError(
                        "listing page matched no article links - the site layout "
                        "may have changed"
                    )
                entries = [a for a in (sdk.read_article(l) for l in links) if a]
            else:
                entries = sdk.parse_feed(sdk.http_get(url), base_url=url)
        except sdk.PluginError as exc:
            failures.append(f"{outlet}: {exc}")
            continue

        kept = 0
        for entry in entries:
            title = entry.get("title") or ""
            if include and not include.search(title):
                continue
            if exclude and exclude.search(title):
                continue

            entry["link"] = clean_link(entry["link"])
            entry["guid"] = clean_link(entry.get("guid") or entry["link"])
            # sites in one source overlap (Wyborcza's .pl and .biz listings
            # share their top stories); keep the first copy only
            if entry["guid"] in seen_guids:
                continue
            seen_guids.add(entry["guid"])
            if entry.get("published_at") is None:
                entry["published_at"] = _parse_pl_date(entry.get("summary"))
            # keep the outlet visible when one source aggregates several sites
            entry["author"] = _clean_author(entry.get("author"), outlet)

            items.append(entry)
            kept += 1
            if kept >= limit:
                break

    if not items and failures:
        raise sdk.PluginError("; ".join(failures))

    # a picture reused across many items is a stand-in, not a headline's own
    counts = {}
    for item in items:
        if item.get("image"):
            counts[item["image"]] = counts.get(item["image"], 0) + 1
    for item in items:
        if counts.get(item.get("image"), 0) >= 3:
            item["image"] = None

    if config.get("fetch_images", True):
        for item in items:
            if item.get("image") or item.pop("_no_image_lookup", False):
                continue  # feeds with media tags, and every scraped article
            try:
                page = sdk.http_get(item["link"], timeout=12)
                item["image"] = sdk.find_lead_image(page, item["link"])
            except sdk.PluginError:
                pass  # one unreachable article must not sink the whole run

    items.sort(key=lambda i: i.get("published_at") or 0, reverse=True)
    return items
