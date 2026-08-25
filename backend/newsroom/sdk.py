"""Helper API available to news-source plugins.

A plugin is a single ``.py`` file the user uploads. It must define:

    PLUGIN = {
        "name": "rss",                  # unique id, matches the module name
        "version": "1.0",
        "description": "Generic RSS/Atom reader",
        "author": "you",
        "config_spec": [                # rendered as the source config form
            {"key": "url", "label": "Feed URL", "type": "url", "required": True},
        ],
    }

    def fetch(config: dict) -> list[dict]:
        ...

``fetch`` returns a list of dicts; each item must carry at least ``title`` and
``link``. Recognised keys:

    title        str   required
    link         str   required - the URL of the article
    image        str   the "premier" (lead) image URL
    published_at datetime | float | str (ISO-8601 or RFC-822) - the news date
    summary      str
    author       str
    guid         str   stable id used for dedup; defaults to ``link``

The ``source`` of every item is recorded automatically from the source entry
that ran the plugin, so plugins never have to set it.
"""
from __future__ import annotations

import datetime as _dt
import email.utils
import gzip
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser as _HTMLParser
from typing import Any

from . import config

__all__ = [
    "http_get",
    "http_get_json",
    "parse_feed",
    "find_lead_image",
    "find_links",
    "read_article",
    "read_listing",
    "meta_tag",
    "json_ld",
    "absolutize",
    "parse_date",
    "strip_html",
    "PluginError",
]


class PluginError(Exception):
    """Raised by plugins to report a clean, user-visible failure."""


# --- HTTP -----------------------------------------------------------------

def http_get(url: str, *, headers: dict[str, str] | None = None,
             timeout: int | None = None) -> str:
    """GET a URL and return decoded text. Raises PluginError on failure."""
    return http_get_bytes(url, headers=headers, timeout=timeout).decode(
        "utf-8", errors="replace"
    )


def http_get_bytes(url: str, *, headers: dict[str, str] | None = None,
                   timeout: int | None = None) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise PluginError(f"unsupported URL scheme: {parsed.scheme!r}")
    req = urllib.request.Request(url)
    req.add_header("User-Agent", config.USER_AGENT)
    req.add_header("Accept-Encoding", "gzip")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.PLUGIN_TIMEOUT) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return raw
    except urllib.error.HTTPError as exc:
        raise PluginError(f"HTTP {exc.code} for {url}") from exc
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        raise PluginError(f"{type(exc).__name__}: {exc} ({url})") from exc


def http_get_json(url: str, **kwargs: Any) -> Any:
    text = http_get(url, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PluginError(f"invalid JSON from {url}: {exc}") from exc


# --- dates ----------------------------------------------------------------

_ISO_CLEAN = re.compile(r"(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")


def parse_date(value: Any) -> float | None:
    """Best-effort conversion of a feed date into a UNIX timestamp."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.timestamp()
    if isinstance(value, _dt.date):
        return _dt.datetime(
            value.year, value.month, value.day, tzinfo=_dt.timezone.utc
        ).timestamp()

    text = str(value).strip()
    # Bare numbers: some sites publish epoch seconds (or milliseconds) in a
    # field that normally holds a formatted date.
    if text.isdigit():
        number = int(text)
        if len(text) == 13:
            number //= 1000
        if 10**8 < number < 10**10:   # ~1973 to ~2286, sane epoch seconds
            return float(number)
        return None
    # RFC-822 (RSS)
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.timestamp()
    except (TypeError, ValueError):
        pass
    # ISO-8601 (Atom / JSON APIs)
    try:
        iso = text.replace("Z", "+00:00")
        parsed = _dt.datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


# --- HTML helpers ---------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(html: str | None, limit: int = 400) -> str | None:
    if not html:
        return None
    import html as _html

    text = _html.unescape(_TAG_RE.sub(" ", html))
    text = _WS_RE.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rsplit(" ", 1)[0] + "…"
    return text or None


_META_RE = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR_RE = re.compile(r"""(\w[\w:-]*)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))""")
_IMG_RE = re.compile(r"<img\b[^>]*>", re.I)


def _attrs(tag: str) -> dict[str, str]:
    out = {}
    for match in _ATTR_RE.finditer(tag):
        key = match.group(1).lower()
        out[key] = match.group(3) or match.group(4) or match.group(5) or ""
    return out


def find_lead_image(html: str, base_url: str = "") -> str | None:
    """Extract the article's lead ("premier") image from an HTML page.

    Order of preference: og:image, twitter:image, link rel=image_src, then the
    first reasonably sized <img> on the page.
    """
    import html as _html

    for tag in _META_RE.findall(html):
        attrs = _attrs(tag)
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key in ("og:image", "og:image:url", "og:image:secure_url",
                   "twitter:image", "twitter:image:src"):
            url = attrs.get("content")
            # some sites point og:image at their own header or logo; that is
            # page furniture wherever it appears, so keep looking
            if url and not is_decorative_image(_html.unescape(url), strict=False):
                return absolutize(_html.unescape(url), base_url)

    link_match = re.search(
        r"""<link\b[^>]*rel\s*=\s*["']?image_src["']?[^>]*>""", html, re.I
    )
    if link_match:
        url = _attrs(link_match.group(0)).get("href")
        if url and not is_decorative_image(_html.unescape(url), strict=False):
            return absolutize(_html.unescape(url), base_url)

    for tag in _IMG_RE.findall(html):
        attrs = _attrs(tag)
        url = attrs.get("src") or attrs.get("data-src")
        if not url or url.startswith("data:"):
            continue
        if is_decorative_image(url):
            continue
        return absolutize(_html.unescape(url), base_url)
    return None


def absolutize(url: str | None, base_url: str) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith("//"):
        scheme = urllib.parse.urlparse(base_url).scheme or "https"
        return f"{scheme}:{url}"
    if base_url and not urllib.parse.urlparse(url).scheme:
        return urllib.parse.urljoin(base_url, url)
    return url


# --- feed parsing ---------------------------------------------------------

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


def _text(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    text = "".join(node.itertext()).strip()
    return text or None


_KNOWN_NS = {
    "media": "http://search.yahoo.com/mrss/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "atom": "http://www.w3.org/2005/Atom",
    "sy": "http://purl.org/rss/1.0/modules/syndication/",
    "slash": "http://purl.org/rss/1.0/modules/slash/",
    "wfw": "http://wellformedweb.org/CommentAPI/",
    "georss": "http://www.georss.org/georss",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
}
_BARE_AMP_RE = re.compile(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)")
_PREFIX_RE = re.compile(r"<(\w+):|\s(\w+):\w+\s*=")
_ROOT_RE = re.compile(r"<(rss|feed|rdf:RDF)\b([^>]*)>", re.I)


def repair_feed_xml(xml_text: str) -> str:
    """Patch the two things real-world feeds get wrong often enough to matter.

    Unescaped ampersands, and namespace prefixes (`media:`, `dc:`) used without
    ever being declared - TVP Info's feed does both, and a strict parser
    rejects the whole document over it.
    """
    text = _BARE_AMP_RE.sub("&amp;", xml_text)

    root = _ROOT_RE.search(text)
    if not root:
        return text
    attrs = root.group(2)
    declared = set(re.findall(r"xmlns:(\w+)\s*=", text))
    used = {p for pair in _PREFIX_RE.findall(text) for p in pair if p}
    missing = {p for p in used - declared - {"xmlns", "xml"} if p != root.group(1)}
    if not missing:
        return text

    additions = "".join(
        f' xmlns:{prefix}="{_KNOWN_NS.get(prefix, f"urn:x-newsroom:{prefix}")}"'
        for prefix in sorted(missing)
    )
    return text[: root.start()] + f"<{root.group(1)}{attrs}{additions}>" + text[root.end():]


def _local(tag: str) -> str:
    """Element tag without its namespace: '{ns}item' -> 'item'."""
    return tag.rsplit("}", 1)[-1]


def _by_local(entry: ET.Element) -> dict[str, list[ET.Element]]:
    """Index an entry's children by local name, namespace ignored.

    Feeds come in RSS 2.0 (no namespace), RSS 1.0/RDF (everything in the RSS
    namespace) and Atom, and all three spell the same fields differently. Going
    by local name reads all of them without a branch per format.
    """
    children: dict[str, list[ET.Element]] = {}
    for child in entry:
        children.setdefault(_local(child.tag), []).append(child)
    return children


def _first(children: dict, *names: str) -> ET.Element | None:
    for name in names:
        if children.get(name):
            return children[name][0]
    return None


def parse_feed(xml_text: str, base_url: str = "") -> list[dict]:
    """Parse an RSS 2.0, RSS 1.0/RDF or Atom document into raw news dicts.

    Images are taken from media:content / media:thumbnail / enclosure, or from
    the first <img> inside the item body. Malformed documents get one repair
    attempt before being rejected.
    """
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        try:
            root = ET.fromstring(repair_feed_xml(xml_text).strip())
        except ET.ParseError as exc:
            raise PluginError(f"could not parse feed XML: {exc}") from exc

    entries = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]

    items: list[dict] = []
    for entry in entries:
        children = _by_local(entry)

        # link: RSS puts it in the text, Atom in an href attribute, RDF may
        # only carry rdf:about on the item itself
        link = None
        for candidate in children.get("link", []):
            href = candidate.get("href")
            rel = candidate.get("rel", "alternate")
            if href and rel == "alternate":
                link = href
                break
            if _text(candidate):
                link = _text(candidate)
                break
        if not link:
            link = next(
                (v for k, v in entry.attrib.items() if _local(k) == "about"), None
            )
        if not link:
            continue

        body = _text(_first(children, "encoded", "description", "summary", "content"))

        image = None
        for child in entry:
            if _local(child.tag) in ("content", "thumbnail") and child.get("url"):
                image = child.get("url")
                break
        if not image:
            enclosure = _first(children, "enclosure")
            if enclosure is not None and (enclosure.get("type") or "").startswith("image"):
                image = enclosure.get("url")
        if not image and body:
            match = _IMG_RE.search(body)
            if match:
                image = _attrs(match.group(0)).get("src")

        published = _text(_first(children, "pubDate", "published", "date", "updated"))

        author = _text(_first(children, "creator"))
        if author is None:
            author_el = _first(children, "author")
            if author_el is not None:
                name = next((c for c in author_el if _local(c.tag) == "name"), None)
                author = _text(name) if name is not None else _text(author_el)

        items.append(
            {
                "title": _text(_first(children, "title")) or "(untitled)",
                "link": absolutize(link.strip(), base_url),
                "image": absolutize(image, base_url),
                "published_at": parse_date(published),
                "summary": strip_html(body),
                "author": author,
                "guid": _text(_first(children, "guid", "id")) or link.strip(),
            }
        )
    return items


# --- scraping helpers -----------------------------------------------------

_HREF_RE = re.compile(r"""<a\b[^>]*href\s*=\s*["']([^"']+)["']""", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_LD_RE = re.compile(
    r"""<script[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.I | re.S,
)
_ARTICLE_TYPES = {"NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting"}


def meta_tag(html: str, key: str) -> str | None:
    """Read <meta property|name|itemprop="key" content="…">, attribute order free."""
    import html as _html

    key = key.lower()
    for tag in _META_RE.findall(html):
        attrs = _attrs(tag)
        names = {
            (attrs.get(k) or "").lower() for k in ("property", "name", "itemprop")
        }
        if key in names and attrs.get("content"):
            return _html.unescape(attrs["content"]).strip()
    return None


def json_ld(html: str) -> dict:
    """Return the page's first schema.org Article block, or an empty dict.

    Many sites publish the publication date only here.
    """
    for raw in _LD_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop(0)
            if not isinstance(node, dict):
                continue
            if isinstance(node.get("@graph"), list):
                stack.extend(node["@graph"])
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if any(t in _ARTICLE_TYPES for t in types if isinstance(t, str)):
                return node
    return {}


def find_links(html: str, base_url: str, pattern: str, limit: int = 0) -> list[str]:
    """Absolute, de-duplicated links on a listing page matching `pattern`."""
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        raise PluginError(f"invalid link pattern: {exc}") from exc

    out, seen = [], set()
    for href in _HREF_RE.findall(html):
        url = absolutize(href, base_url)
        if not url or url in seen or not matcher.search(url):
            continue
        seen.add(url)
        out.append(url)
        if limit and len(out) >= limit:
            break
    return out


def read_article(url: str, *, timeout: int = 15) -> dict | None:
    """Fetch one article and read its own metadata into a news item.

    Uses OpenGraph first, then schema.org JSON-LD, then the <title> tag.
    Returns None when the page cannot be fetched.
    """
    try:
        page = http_get(url, timeout=timeout)
    except PluginError:
        return None

    ld = json_ld(page)

    author = ld.get("author")
    if isinstance(author, list):
        author = author[0] if author else None
    if isinstance(author, dict):
        author = author.get("name")

    image = ld.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get("url")

    title_match = _TITLE_RE.search(page)
    return {
        "title": meta_tag(page, "og:title")
        or (ld.get("headline") if isinstance(ld.get("headline"), str) else None)
        or (strip_html(title_match.group(1), 300) if title_match else url),
        "link": url,
        "image": find_lead_image(page, url) or absolutize(
            image if isinstance(image, str) else None, url
        ),
        "published_at": parse_date(
            meta_tag(page, "article:published_time")
            or meta_tag(page, "datePublished")
            or ld.get("datePublished")
            or ld.get("dateCreated")
        ),
        "summary": meta_tag(page, "og:description")
        or meta_tag(page, "description")
        or strip_html(ld.get("description") if isinstance(ld.get("description"), str) else None),
        "author": meta_tag(page, "article:author")
        or (author if isinstance(author, str) else None),
        "guid": url,
    }


_VOID_TAGS = {"img", "source", "br", "hr", "meta", "link", "input", "area",
              "base", "col", "embed", "param", "track", "wbr"}
_IMG_ATTRS = ("src", "data-src", "data-original", "srcset", "data-srcset")


class _Node:
    """Minimal element node: enough structure to relate a link to its picture."""

    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag="", attrs=None, parent=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []
        self.parent = parent
        self.text = ""

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def inner_text(self):
        parts = [node.text for node in self.walk() if node.text]
        return _WS_RE.sub(" ", " ".join(parts)).strip()


class _TreeBuilder(_HTMLParser):
    """Tolerant tree builder - real listing pages are full of unclosed tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root")
        self.current = self.root

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        if tag not in _VOID_TAGS:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.current.children.append(_Node(tag, dict(attrs), self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node is not self.root:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent
        # stray closing tag: ignore it rather than unwinding the whole document

    def handle_data(self, data):
        if data.strip():
            leaf = _Node("#text", parent=self.current)
            leaf.text = data
            self.current.children.append(leaf)


# Site furniture that must never be mistaken for an article's own picture when
# picking images out of a page's markup.
_CHROME_IMAGE_RE = re.compile(
    r"(logo|masthead|header|sprite|icon|favicon|avatar|placeholder|blank|pixel|"
    r"1x1|banner|button|badge|watermark)", re.I
)
# The narrower list, for images the publisher chose deliberately (og:image).
# An article about CSS may quite legitimately lead with the CSS logo, but no
# one picks their own masthead as an article's picture.
_FURNITURE_RE = re.compile(
    r"(masthead|header|sprite|favicon|placeholder|blank|pixel|1x1|watermark)", re.I
)


def is_decorative_image(url: str | None, *, strict: bool = True) -> bool:
    """True for page furniture that should not be shown as an article's image.

    A listing page's masthead sits in the markup like any other image, so
    without this check every card lacking a photo would show the site logo.
    Pass ``strict=False`` for a URL the publisher nominated themselves, where
    only unmistakable furniture should be rejected.
    """
    if not url:
        return True
    if url.startswith("data:"):
        return True
    path = url.split("?")[0]
    if path.lower().endswith((".svg", ".ico")):
        return True
    return bool((_CHROME_IMAGE_RE if strict else _FURNITURE_RE).search(path))


def _first_image(node) -> str | None:
    for element in node.walk():
        if element.tag in ("img", "source"):
            for attr in _IMG_ATTRS:
                value = element.attrs.get(attr)
                if not value:
                    continue
                # srcset holds "url 320w, url 640w" - take the first URL
                url = value.split(",")[0].strip().split(" ")[0]
                if url and not is_decorative_image(url):
                    return url
    return None


def read_listing(html_text: str, base_url: str, pattern: str, *,
                 limit: int = 20, min_title_length: int = 25) -> list[dict]:
    """Build news items from a listing page alone, without opening articles.

    For sites whose article pages carry no usable metadata - paywalled, or
    rendered in the browser - the headline is taken from the link text.

    An image is attached only when it belongs to that link: inside the link, or
    inside an ancestor that holds this link and no other article link. A picture
    borrowed from a neighbouring card would sit under the wrong headline, so an
    ambiguous one is dropped instead. Listing pages carry no publication date,
    so `published_at` stays unset and the app falls back to first-seen time.
    """
    try:
        matcher = re.compile(pattern)
    except re.error as exc:
        raise PluginError(f"invalid link pattern: {exc}") from exc

    builder = _TreeBuilder()
    try:
        builder.feed(html_text)
    except Exception as exc:  # noqa: BLE001 - malformed markup must not crash a run
        raise PluginError(f"could not parse the listing page: {exc}") from exc

    def link_url(node):
        href = node.attrs.get("href")
        if not href:
            return None
        url = absolutize(href.split("#")[0], base_url)
        return url if url and matcher.search(url) else None

    anchors = [
        (node, url)
        for node in builder.root.walk()
        if node.tag == "a" and (url := link_url(node))
    ]

    items, seen = [], set()
    for node, url in anchors:
        if url in seen:
            continue
        title = node.inner_text()
        if len(title) < min_title_length:
            continue
        seen.add(url)

        image = _first_image(node)
        ancestor = node.parent
        while image is None and ancestor is not None and ancestor.tag != "#root":
            # only borrow an image from a block that covers this link alone
            covered = sum(
                1 for other, other_url in anchors
                if other_url != url and _contains(ancestor, other)
            )
            if covered:
                break
            image = _first_image(ancestor)
            ancestor = ancestor.parent

        items.append(
            {
                "title": title,
                "link": url,
                "image": absolutize(image, url),
                "published_at": None,   # listing pages do not carry dates
                "summary": None,
                "author": None,
                "guid": url,
            }
        )
        if limit and len(items) >= limit:
            break

    # A picture repeated across a listing is the site's stand-in for articles
    # without one (Wyborcza reuses its wordmark that way), not a headline's own
    # image - and its URL gives no hint of that, so spot it by repetition.
    counts: dict[str, int] = {}
    for item in items:
        if item["image"]:
            counts[item["image"]] = counts.get(item["image"], 0) + 1
    shared = {url for url, n in counts.items() if n >= 3}
    for item in items:
        if item["image"] in shared:
            item["image"] = None
    return items


def _contains(ancestor, node) -> bool:
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False
