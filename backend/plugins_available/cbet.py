"""Central Bureau Electronic Telegrams (IAU Central Bureau for Astronomical Telegrams).

Reads http://www.cbat.eps.harvard.edu/cbet/RecentCBETs.html - the 50 most
recent CBETs, the telegrams announcing comets, novae, supernovae and other
transient discoveries. Each line of the page reads

    <li> CBET 5722 : 20260807 : <a href="/iau/cbet/005700/CBET005722.txt">COMET 220P/McNAUGHT</a>

giving the telegram number, its issue date and its subject.

The circulars themselves are plain-text files, so items carry no image unless
`use_logo_image` is left on, which uses the Central Bureau's own emblem.

Note the site serves HTTP only - its HTTPS port does not answer - so the
default URL is deliberately http://.
"""
import datetime
import re

from newsroom import sdk

PLUGIN = {
    "name": "cbet",
    "version": "1.0",
    "description": "Central Bureau Electronic Telegrams (comets, novae, supernovae and other transients).",
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "url", "label": "Recent CBETs page", "type": "url", "required": False,
         "default": "http://www.cbat.eps.harvard.edu/cbet/RecentCBETs.html"},
        {"key": "limit", "label": "Max telegrams per run", "type": "int",
         "required": False, "default": 30},
        {"key": "include", "label": "Keep only subjects matching (regex)",
         "type": "string", "required": False, "placeholder": "COMET|SUPERNOVA"},
        {"key": "use_logo_image", "label": "Use the CBAT emblem as the item image",
         "type": "bool", "required": False, "default": True},
    ],
}

LOGO = "http://www.cbat.eps.harvard.edu/figs/cbat-banner.jpg"

# <li> CBET   5722 : 20260807 : <a href="/iau/cbet/005700/CBET005722.txt">SUBJECT</a>
_ENTRY_RE = re.compile(
    r"""<li>\s*CBET\s+(?P<number>\d+)\s*:\s*(?P<date>\d{8})\s*:\s*"""
    r"""<a\s+href=["'](?P<href>[^"']+)["']\s*>(?P<subject>.*?)</a>""",
    re.I | re.S,
)


def _issue_date(digits):
    """CBAT writes issue dates as YYYYMMDD."""
    try:
        parsed = datetime.datetime.strptime(digits, "%Y%m%d")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()


def fetch(config):
    url = (config.get("url") or PLUGIN["config_spec"][0]["default"]).strip()
    limit = int(config.get("limit") or 30)
    include = re.compile(config["include"], re.I) if config.get("include") else None
    use_logo = config.get("use_logo_image", True)

    entries = list(_ENTRY_RE.finditer(sdk.http_get(url)))
    if not entries:
        raise sdk.PluginError(
            "no CBET entries found - the Central Bureau page layout may have changed"
        )

    items = []
    for entry in entries:
        subject = sdk.strip_html(entry.group("subject"), 200) or "(no subject)"
        if include and not include.search(subject):
            continue

        number = entry.group("number")
        items.append(
            {
                "title": f"CBET {number}: {subject}",
                "link": sdk.absolutize(entry.group("href"), url),
                "image": LOGO if use_logo else None,
                "published_at": _issue_date(entry.group("date")),
                "summary": subject,
                "author": "IAU Central Bureau for Astronomical Telegrams",
                "guid": f"cbet:{number}",
            }
        )
        if len(items) >= limit:
            break
    return items
