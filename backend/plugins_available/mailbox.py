"""Read a mailbox over IMAP or POP3 and turn its messages into news items.

Aimed at newsletters: a subscription lands in a folder, and each message becomes
an item with its subject as the title, the sender as the author, and the date the
message was sent. Since a news item needs a web address, the plugin links to the
first http(s) link found in the body - which for a newsletter is the article it
is announcing - and falls back to `fallback_link` (your webmail, say) otherwise.

Credentials: put the password in `password`, or better, leave that empty and set
`password_env` to the name of an environment variable the server reads it from,
so the secret never goes into the database. On accounts with two-factor login,
use an application-specific password.

Nothing is ever deleted, and messages are read with BODY.PEEK so they are not
marked as seen unless `mark_seen` is turned on.
"""
import email
import email.policy
import imaplib
import os
import poplib
import re
from email.header import decode_header, make_header

from newsroom import sdk

PLUGIN = {
    "name": "mailbox",
    "version": "1.0",
    "description": "Read newsletters from an IMAP or POP3 mailbox.",
    "author": "myPrivateNewsRoom",
    "config_spec": [
        {"key": "protocol", "label": "Protocol (imap or pop3)", "type": "string",
         "required": True, "default": "imap"},
        {"key": "host", "label": "Server host", "type": "string", "required": True,
         "placeholder": "imap.example.com"},
        {"key": "port", "label": "Port (blank = protocol default)", "type": "int",
         "required": False},
        {"key": "ssl", "label": "Use SSL/TLS", "type": "bool", "required": False,
         "default": True},
        {"key": "username", "label": "Username", "type": "string", "required": True},
        {"key": "password", "label": "Password (or use password_env)",
         "type": "secret", "required": False},
        {"key": "password_env", "label": "Environment variable holding the password",
         "type": "string", "required": False, "placeholder": "NEWSROOM_MAIL_PASSWORD"},
        {"key": "folder", "label": "Folder (IMAP only)", "type": "string",
         "required": False, "default": "INBOX"},
        {"key": "limit", "label": "Max messages per run", "type": "int",
         "required": False, "default": 25},
        {"key": "unread_only", "label": "Only unread messages (IMAP only)",
         "type": "bool", "required": False, "default": False},
        {"key": "from_filter", "label": "Only senders matching (regex)",
         "type": "string", "required": False, "placeholder": "newsletter@"},
        {"key": "subject_filter", "label": "Only subjects matching (regex)",
         "type": "string", "required": False},
        {"key": "fallback_link", "label": "Link to use when a message has none",
         "type": "url", "required": False, "placeholder": "https://mail.example.com/"},
        {"key": "mark_seen", "label": "Mark messages as read after reading",
         "type": "bool", "required": False, "default": False},
    ],
}

DEFAULT_PORTS = {
    ("imap", True): 993, ("imap", False): 143,
    ("pop3", True): 995, ("pop3", False): 110,
}


def _password(config):
    variable = (config.get("password_env") or "").strip()
    if variable:
        value = os.environ.get(variable)
        if not value:
            raise sdk.PluginError(f"environment variable {variable!r} is not set")
        return value
    password = config.get("password") or ""
    if not password:
        raise sdk.PluginError("set either 'password' or 'password_env'")
    return password


def _decode(value):
    """MIME-encoded headers ('=?UTF-8?B?...?=') into plain text."""
    if not value:
        return None
    try:
        return str(make_header(decode_header(value))).strip()
    except (UnicodeDecodeError, LookupError, ValueError):
        return value.strip()


def _body_parts(message):
    """The text and html bodies of a message, either one possibly missing."""
    text = html = None
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_filename():          # an attachment is not the body
                continue
            kind = part.get_content_type()
            try:
                content = part.get_content()
            except (LookupError, UnicodeDecodeError):
                continue
            if kind == "text/plain" and text is None:
                text = content
            elif kind == "text/html" and html is None:
                html = content
    else:
        try:
            content = message.get_content()
        except (LookupError, UnicodeDecodeError):
            content = ""
        if message.get_content_type() == "text/html":
            html = content
        else:
            text = content
    return text, html


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
# links a newsletter carries for its own plumbing, not for the reader
_PLUMBING_RE = re.compile(
    r"(unsubscribe|optout|opt-out|preferences|list-manage|/track|/click|"
    r"pixel|beacon|\.gif$|mailto:)", re.I
)


def first_link(text, html):
    """The first link that looks like an article rather than list plumbing."""
    candidates = []
    if html:
        candidates += re.findall(r"""<a[^>]+href\s*=\s*["'](https?://[^"']+)["']""",
                                 html, re.I)
    if text:
        candidates += _URL_RE.findall(text)
    for url in candidates:
        if not _PLUMBING_RE.search(url):
            return url.rstrip(".,;)")
    return None


def first_image(html):
    if not html:
        return None
    for url in re.findall(r"""<img[^>]+src\s*=\s*["'](https?://[^"']+)["']""", html, re.I):
        if not sdk.is_decorative_image(url) and not _PLUMBING_RE.search(url):
            return url
    return None


def message_to_item(raw, config):
    """Convert one RFC-822 message into a news item, or None to skip it."""
    message = email.message_from_bytes(raw, policy=email.policy.default)

    subject = _decode(message.get("Subject")) or "(no subject)"
    sender = _decode(message.get("From")) or ""

    from_filter = config.get("from_filter")
    if from_filter and not re.search(from_filter, sender, re.I):
        return None
    subject_filter = config.get("subject_filter")
    if subject_filter and not re.search(subject_filter, subject, re.I):
        return None

    text, html = _body_parts(message)
    link = first_link(text, html) or (config.get("fallback_link") or "").strip()
    if not link:
        return None      # a news item must point somewhere

    return {
        "title": subject,
        "link": link,
        "image": first_image(html),
        "published_at": sdk.parse_date(message.get("Date")),
        "summary": sdk.strip_html(html) if html else sdk.strip_html(text),
        "author": sender,
        # Message-ID is unique per message and stable across runs
        "guid": (message.get("Message-ID") or f"{sender}:{subject}").strip("<> "),
    }


def _fetch_imap(config, limit):
    host = (config.get("host") or "").strip()
    use_ssl = config.get("ssl", True)
    port = int(config.get("port") or DEFAULT_PORTS[("imap", bool(use_ssl))])
    folder = (config.get("folder") or "INBOX").strip()
    peek = "" if config.get("mark_seen") else ".PEEK"

    opener = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
    try:
        server = opener(host, port, timeout=30)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        raise sdk.PluginError(f"cannot reach {host}:{port} - {exc}") from exc

    try:
        try:
            server.login(config.get("username") or "", _password(config))
        except imaplib.IMAP4.error as exc:
            raise sdk.PluginError(f"login refused: {exc}") from exc

        try:
            status, _ = server.select(folder, readonly=not config.get("mark_seen"))
        except imaplib.IMAP4.readonly:
            # the server will only hand out this folder read-only; that is fine,
            # reading is all this plugin needs
            status, _ = server.select(folder, readonly=True)
            peek = ".PEEK"
        except imaplib.IMAP4.error as exc:
            raise sdk.PluginError(f"cannot open folder {folder!r}: {exc}") from exc
        if status != "OK":
            raise sdk.PluginError(f"cannot open folder {folder!r}")

        criterion = "UNSEEN" if config.get("unread_only") else "ALL"
        try:
            status, data = server.search(None, criterion)
            if status != "OK":
                raise sdk.PluginError("the server refused the search")

            ids = data[0].split()[-limit:]        # newest messages sit last
            raws = []
            for message_id in reversed(ids):
                status, parts = server.fetch(message_id, f"(BODY{peek}[])")
                if status != "OK" or not parts or not isinstance(parts[0], tuple):
                    continue
                raws.append(parts[0][1])
            return raws
        except imaplib.IMAP4.error as exc:
            # includes IMAP4.abort, raised when a server drops out mid-session
            raise sdk.PluginError(f"IMAP session failed: {exc}") from exc
    finally:
        # Some servers drop the connection after CLOSE. Waiting the full socket
        # timeout on the LOGOUT that follows would burn most of the plugin's
        # budget, so the teardown gets a short leash and the socket is shut
        # down regardless of how politely the server behaved.
        try:
            server.sock.settimeout(5)
        except Exception:  # noqa: BLE001
            pass
        for step in (server.close, server.logout, server.shutdown):
            try:
                step()
            except Exception:  # noqa: BLE001 - tearing down a dead connection
                pass


def _fetch_pop3(config, limit):
    host = (config.get("host") or "").strip()
    use_ssl = config.get("ssl", True)
    port = int(config.get("port") or DEFAULT_PORTS[("pop3", bool(use_ssl))])

    opener = poplib.POP3_SSL if use_ssl else poplib.POP3
    try:
        server = opener(host, port, timeout=30)
    except Exception as exc:  # noqa: BLE001
        raise sdk.PluginError(f"cannot reach {host}:{port} - {exc}") from exc

    try:
        try:
            server.user(config.get("username") or "")
            server.pass_(_password(config))
        except poplib.error_proto as exc:
            raise sdk.PluginError(f"login refused: {exc}") from exc

        count = len(server.list()[1])
        raws = []
        for number in range(count, max(0, count - limit), -1):   # newest first
            _, lines, _ = server.retr(number)
            raws.append(b"\r\n".join(lines))
        return raws
    finally:
        try:
            server.quit()      # POP3 deletes nothing unless asked; quit is safe
        except Exception:  # noqa: BLE001
            pass


def fetch(config):
    protocol = (config.get("protocol") or "imap").strip().lower()
    if protocol not in ("imap", "pop3"):
        raise sdk.PluginError("protocol must be 'imap' or 'pop3'")
    if not (config.get("host") or "").strip():
        raise sdk.PluginError("config key 'host' is required")
    limit = max(1, min(int(config.get("limit") or 25), 200))

    raws = _fetch_imap(config, limit) if protocol == "imap" else _fetch_pop3(config, limit)

    items = []
    linkless = 0
    for raw in raws:
        try:
            item = message_to_item(raw, config)
        except Exception:  # noqa: BLE001 - one malformed message is not fatal
            continue
        if item is None:
            # None means either a filter excluded it, which is the user asking
            # for that, or it had no link at all, which is worth reporting
            linkless += 1
            continue
        items.append(item)

    if not items and linkless and not _filters_used(config):
        raise sdk.PluginError(
            f"{linkless} message(s) read but none carried a link - set "
            "'fallback_link' if your mail has none"
        )
    return items


def _filters_used(config):
    return bool((config.get("from_filter") or "").strip()
                or (config.get("subject_filter") or "").strip())
