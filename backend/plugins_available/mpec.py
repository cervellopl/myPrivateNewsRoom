"""Minor Planet Electronic Circulars (IAU Minor Planet Center).

Reads https://www.minorplanetcenter.net/mpec/RecentMPECs.html - the 100 most
recent MPECs - and turns each circular into a news item. The page is a plain
HTML list, so the entry is parsed directly:

    <li><a href="/mpec/K26/K26Q41.html"><i>MPEC</i> 2026-Q41</a> (2026 August 19)
        <ul><li>2026 QF</ul>

giving the circular's id, its issue date and its subject (a designation such as
"2026 QF", or "DAILY ORBIT UPDATE").

MPECs carry no illustrations, so items are given the Minor Planet Center's own
logo as their premier image unless that is switched off.
"""
import re

from newsroom import sdk

PLUGIN = {
    "name": "mpec",
    "version": "1.0",
    "description": "Minor Planet Electronic Circulars (asteroid/comet discoveries and orbit updates).",
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "url", "label": "Recent MPECs page", "type": "url", "required": False,
         "default": "https://www.minorplanetcenter.net/mpec/RecentMPECs.html"},
        {"key": "limit", "label": "Max circulars per run", "type": "int",
         "required": False, "default": 30},
        {"key": "skip_daily_orbit_update", "label": "Skip DAILY ORBIT UPDATE circulars",
         "type": "bool", "required": False, "default": True},
        {"key": "include", "label": "Keep only subjects matching (regex)",
         "type": "string", "required": False, "placeholder": "COMET|2026 Q"},
        {"key": "use_logo_image", "label": "Use the MPC logo as the item image",
         "type": "bool", "required": False, "default": True},
    ],
}

LOGO = "https://www.minorplanetcenter.net/images/CfA_Logo_Vertical_CMYK.png"

# <li><a href="/mpec/K26/K26Q41.html"><i>MPEC</i> 2026-Q41</a> (2026 August 19)
_ENTRY_RE = re.compile(
    r"""<li>\s*<a\s+href=["'](?P<href>[^"']*/mpec/[^"']+)["']\s*>\s*"""
    r"""(?:<i>)?\s*MPEC\s*(?:</i>)?\s*(?P<id>[\w-]+)\s*</a>\s*"""
    r"""\((?P<date>[^)]+)\)(?P<rest>.*?)(?=<p>\s*<li>|</ul>\s*(?:<p>)?\s*<hr|\Z)""",
    re.I | re.S,
)
_SUBJECT_RE = re.compile(r"<li>(?P<subject>.*?)(?:</li>|</ul>|\Z)", re.I | re.S)


def _issue_date(text):
    """'2026 August 19' -> timestamp. The MPC writes dates in that one form."""
    import datetime

    cleaned = re.sub(r"\s+", " ", text).strip()
    for fmt in ("%Y %B %d", "%Y %b %d", "%Y %B %d.%f", "%Y %B %d %H:%M"):
        try:
            parsed = datetime.datetime.strptime(cleaned, fmt)
            return parsed.replace(tzinfo=datetime.timezone.utc).timestamp()
        except ValueError:
            continue
    return sdk.parse_date(cleaned)


def fetch(config):
    url = (config.get("url") or PLUGIN["config_spec"][0]["default"]).strip()
    limit = int(config.get("limit") or 30)
    skip_daily = config.get("skip_daily_orbit_update", True)
    include = re.compile(config["include"], re.I) if config.get("include") else None
    use_logo = config.get("use_logo_image", True)

    page = sdk.http_get(url)
    entries = list(_ENTRY_RE.finditer(page))
    if not entries:
        raise sdk.PluginError(
            "no MPEC entries found - the Minor Planet Center page layout may have changed"
        )

    items = []
    for entry in entries:
        subject_match = _SUBJECT_RE.search(entry.group("rest"))
        subject = sdk.strip_html(subject_match.group("subject"), 200) if subject_match else None
        subject = subject or "(no subject)"

        if skip_daily and subject.upper().startswith("DAILY ORBIT UPDATE"):
            continue
        if include and not include.search(subject):
            continue

        mpec_id = entry.group("id")
        items.append(
            {
                "title": f"MPEC {mpec_id}: {subject}",
                "link": sdk.absolutize(entry.group("href"), url),
                "image": LOGO if use_logo else None,
                "published_at": _issue_date(entry.group("date")),
                "summary": subject,
                "author": "IAU Minor Planet Center",
                "guid": f"mpec:{mpec_id}",
            }
        )
        if len(items) >= limit:
            break
    return items
