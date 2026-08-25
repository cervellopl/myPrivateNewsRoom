# Writing a source plugin

A plugin is **one `.py` file** that teaches myPrivateNewsRoom how to read one
kind of source. Upload it in the web UI (**Plugins → Upload**) or with
`curl -X POST localhost:8000/api/plugins -F "file=@my_source.py"`.

## Contract

```python
from newsroom import sdk          # helper library provided by the server

PLUGIN = {
    "name": "my_source",          # required, lowercase [a-z0-9_], = the plugin id
    "version": "1.0",
    "description": "What this reads",
    "author": "you",
    "config_spec": [              # renders the source-configuration form
        {"key": "url", "label": "Feed URL", "type": "url", "required": True,
         "placeholder": "https://…"},
        {"key": "limit", "label": "Max items", "type": "int", "default": 20},
        {"key": "token", "label": "API key", "type": "secret"},
    ],
}

def fetch(config: dict) -> list[dict]:
    """Called on every poll. `config` is what the user filled in the form."""
    return [
        {
            "title": "…",                       # required
            "link": "https://…",                # required — the article URL
            "image": "https://…/lead.jpg",      # premier image
            "published_at": "2026-08-19T10:00:00Z",  # ISO-8601, RFC-822, epoch or datetime
            "summary": "…",
            "author": "…",
            "guid": "stable-id",                # dedup key; defaults to the link
        },
    ]
```

`PLUGIN` must be a literal dict — it is read statically before the file is ever
imported. `config_spec` field types: `string`, `url`, `int`, `bool`, `secret`.

The **source** of every item is filled in automatically from the source entry
that ran the plugin, so plugins never set it.

## The `sdk` helpers

| helper | what it does |
|---|---|
| `sdk.http_get(url, headers=…, timeout=…)` | fetch a URL as text (gzip, UA, timeouts handled) |
| `sdk.http_get_json(url, …)` | same, parsed as JSON |
| `sdk.parse_feed(xml, base_url)` | RSS 2.0 / Atom → list of news dicts, images included; repairs undeclared namespaces and bare `&` before giving up |
| `sdk.find_lead_image(html, base_url)` | `og:image` → `twitter:image` → `image_src` → first real `<img>` |
| `sdk.find_links(html, base_url, pattern, limit)` | article links on a listing page matching a regex, de-duplicated |
| `sdk.read_article(url)` | open one article and read a full news item from its OpenGraph / JSON-LD metadata |
| `sdk.read_listing(html, base_url, pattern, limit=…)` | build items from a listing page alone (headline from the link text, image only when unambiguously its own) — for paywalled or JS-rendered sites |
| `sdk.meta_tag(html, key)` | one `<meta>` value, whatever the attribute order |
| `sdk.json_ld(html)` | the page's schema.org Article block, `@graph` included |
| `sdk.absolutize(url, base_url)` | resolve a relative URL |
| `sdk.parse_date(value)` | RFC-822 / ISO-8601 / epoch / datetime → timestamp |
| `sdk.strip_html(html, limit)` | tags out, entities decoded, truncated |
| `sdk.PluginError("…")` | raise it to report a clean failure shown next to the source |

## Rules the server enforces

* the file must parse, be UTF-8 and stay under 512 KiB;
* it must define a top-level `fetch` function and a literal `PLUGIN` dict;
* a run is abandoned after `NEWSROOM_PLUGIN_TIMEOUT` seconds (default 60);
* items without a title or an `http(s)` link are dropped;
* any exception is caught, recorded on the source and shown in the UI — a
  failing plugin never stops the other sources.

Re-uploading a file with the same `PLUGIN["name"]` upgrades it in place; if the
new version fails to import, the previous one keeps running.

## Example: a paginated JSON API

```python
from newsroom import sdk

PLUGIN = {
    "name": "acme_news",
    "version": "1.0",
    "description": "ACME newsroom API",
    "config_spec": [
        {"key": "api_key", "label": "API key", "type": "secret", "required": True},
        {"key": "section", "label": "Section", "type": "string", "default": "world"},
    ],
}

def fetch(config):
    if not config.get("api_key"):
        raise sdk.PluginError("an API key is required")

    payload = sdk.http_get_json(
        f"https://api.acme.example/v1/{config.get('section', 'world')}",
        headers={"Authorization": f"Bearer {config['api_key']}"},
    )
    return [
        {
            "title": row["headline"],
            "link": row["webUrl"],
            "image": row.get("leadImage", {}).get("url"),
            "published_at": row.get("firstPublished"),
            "summary": sdk.strip_html(row.get("standfirst")),
            "guid": row["id"],
        }
        for row in payload.get("results", [])
    ]
```

Use **Test plugin** in the Sources tab (or `POST /api/plugins/{name}/test` with
a config body) to dry-run it and see the first items it would import.
