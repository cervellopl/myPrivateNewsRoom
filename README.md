# myPrivateNewsRoom

A private news room: a small self-hosted server that periodically pulls news
from **sources**, where every source is driven by a **plugin the user uploads**,
plus a web UI and an **Android client**.

Every stored news item carries exactly what you asked for:

| field    | meaning                                    |
|----------|--------------------------------------------|
| `date`   | publication date of the news               |
| `image`  | the premier (lead) image of the article    |
| `source` | name of the source it came from            |
| `link`   | link to the original article               |

(plus `title`, `summary`, `author` and `fetched_at` for convenience)

```
myyNewsRoom/
├── backend/            FastAPI server, poller, plugin engine, web UI
│   ├── newsroom/       application package
│   ├── plugins_available/  plugins shipped as examples (auto-installed once)
│   └── plugins/        plugins uploaded at runtime (created on first start)
└── android/            Kotlin / Jetpack Compose client
```

## 1. Run the server

```bash
cd backend
./run.sh                       # creates a venv, installs deps, serves :8000
```

Open <http://localhost:8000> for the web UI and <http://localhost:8000/docs>
for the interactive API reference.

Docker instead:

```bash
docker build -t newsroom backend && docker run -p 8000:8000 -v newsroom-data:/data newsroom
```

### Configuration

All settings are environment variables (see `backend/newsroom/config.py`):

| variable | default | meaning |
|---|---|---|
| `NEWSROOM_POLL_INTERVAL` | `900` | seconds between polls (initial value) |
| `NEWSROOM_MIN_POLL_INTERVAL` | `60` | smallest interval the API will accept |
| `NEWSROOM_API_TOKEN` | *(empty)* | when set, all writing endpoints require `X-API-Token` |
| `NEWSROOM_RETENTION_DAYS` | `90` | news older than this is pruned (`0` = keep forever) |
| `NEWSROOM_PLUGIN_TIMEOUT` | `60` | seconds a single plugin run may take |
| `NEWSROOM_CHROMIUM` | *(auto)* | browser used for page export; empty = search PATH |
| `NEWSROOM_EXPORT_TIMEOUT` | `120` | seconds one page render may take |
| `NEWSROOM_EXPORT_CACHE_MB` | `512` | disk budget for cached renders |
| `NEWSROOM_DATA_DIR` / `NEWSROOM_PLUGIN_DIR` | `backend/data`, `backend/plugins` | storage locations |

**The polling interval is changeable at runtime** — it is stored in the
database, editable in the web UI (Settings), in the Android app, or over the
API, and the next poll cycle picks it up immediately:

```bash
curl -X PUT localhost:8000/api/settings -H 'Content-Type: application/json' \
     -d '{"poll_interval_seconds": 300}'
```

Each source may override it with its own `interval_seconds`.

## 2. Add a source

A source = a plugin + its configuration. In the web UI go to **Sources**, pick a
plugin, fill the generated form, optionally hit **Test plugin** to preview what
it would import, then **Add source**. Over the API:

```bash
curl -X POST localhost:8000/api/sources -H 'Content-Type: application/json' -d '{
  "name": "Reuters World",
  "plugin": "rss",
  "config": {"url": "https://example.com/feed.xml", "limit": 30, "fetch_images": true},
  "interval_seconds": 600
}'
```

Three plugins ship with the server as working examples:

* **`rss`** — any **RSS 2.0, RSS 1.0/RDF or Atom** feed; when an entry carries
  no image it opens the article and takes its `og:image` as the premier image.
  `include` filters on the title, `link_include` / `link_exclude` on the URL —
  which is how one section is picked out of a site-wide feed when a publisher
  offers no per-section one (Sky & Telescope's `/astronomy-news/`, Windows
  Central's `/software-apps/`). `title_strip` removes the publisher suffix that
  aggregator feeds append to every headline ("… - Reuters").

  **Publishers that block direct access** (Reuters answers 401, Lifewire 403 to
  anything that is not a browser) can still be followed through Google News,
  which publishes a real feed for any query:

  ```
  https://news.google.com/rss/search?q=site:reuters.com/technology&hl=en-US&gl=US&ceid=US:en
  ```

  Items then link through a `news.google.com` redirect and carry no images, but
  headlines and dates are correct.

  **Mailing-list archives** work too, without a plugin of their own. Mailman 3 /
  HyperKitty publishes an Atom feed per list, which is not linked from the
  archive page but is always at `…/list/<list-address>/feed` — that is how the
  VSNET-alert variable-star alerts are followed:

  ```
  http://ooruri.kusastro.kyoto-u.ac.jp/mailman3/hyperkitty/list/vsnet-alert@ooruri.kusastro.kyoto-u.ac.jp/feed
  ```

  Mind the subject prefix when filtering such a list: every message carries
  `[vsnet-outburst NNNNN]`, so `include: "Outburst"` would match all of them
  through the prefix. Either anchor the pattern past it
  (`\].*(Outburst|Activity)`) or strip the prefix with `title_strip`, which
  runs before the filters for exactly this reason.
  Malformed feeds get a repair pass (undeclared namespace prefixes, bare `&`)
  before being rejected. Used for BBC, Hacker News and The Astronomer's
  Telegram (`https://astronomerstelegram.org/?rss`, which is RSS 1.0).
* **`html_scraper`** — for sites with no feed: scrape a listing page for article
  links matching a regex and read each article's own metadata. `title_strip`
  removes a site's own name from every title, `link_exclude` skips tag and
  pagination links, and `max_age_days` drops the evergreen promos that sit
  alongside the news on many listing pages. An image shared by three or more
  articles is dropped as a section background rather than shown as any one
  article's picture. Used for Wiocha.pl, Cognity's blog, and for
  Astronomy.com, whose `/news/` page is a hub linking articles that live under
  other sections:

  ```bash
  curl -X POST localhost:8000/api/sources -H 'Content-Type: application/json' -d '{
    "name": "Astronomy.com", "plugin": "html_scraper",
    "config": {"url": "https://www.astronomy.com/news/",
               "link_pattern": "astronomy\\.com/[a-z-]+/([a-z0-9]+-){3,}[a-z0-9]+/?$",
               "link_exclude": "/tags/|/page/", "max_age_days": 45},
    "interval_seconds": 3600
  }'
  ```


  ```bash
  curl -X POST localhost:8000/api/sources -H 'Content-Type: application/json' -d '{
    "name": "Wiocha", "plugin": "html_scraper",
    "config": {"url": "https://wiocha.pl/",
               "link_pattern": "wiocha\\.pl/\\d+-[a-z0-9-]+$",
               "limit": 15, "title_strip": "^Wiocha\\.pl"},
    "interval_seconds": 1800
  }'
  ```
* **`json_api`** — map an arbitrary JSON news API onto news items by field names.
* **`mailbox`** — read newsletters from your own **IMAP or POP3** mailbox. Each
  message becomes an item: subject as the title, sender as the author, the
  message date as the date. Because an item needs a web address, it links to the
  first real link in the body — skipping unsubscribe, tracking and click-through
  URLs — and to `fallback_link` (your webmail) when a message carries none.

  ```bash
  NEWSROOM_MAIL_PASSWORD='app-specific-password' ./run.sh    # keep it out of the DB
  curl -X POST localhost:8000/api/sources -H 'Content-Type: application/json' -d '{
    "name": "Newsletters", "plugin": "mailbox",
    "config": {"protocol": "imap", "host": "imap.example.com", "ssl": true,
               "username": "me@example.com",
               "password_env": "NEWSROOM_MAIL_PASSWORD",
               "folder": "Newsletters", "limit": 25,
               "from_filter": "newsletter@|noreply@"},
    "interval_seconds": 900
  }'
  ```

  **Credentials.** Put the password in `password_env` — the name of an
  environment variable the server reads — and it never touches the database.
  `password` works too, but is stored in the source config in plain text like
  any other setting. Use an application-specific password where your provider
  offers one; the plugin does no interactive or OAuth login.

  **Your mail is not modified.** Folders are opened read-only and messages read
  with `BODY.PEEK`, so nothing is marked as read unless you set `mark_seen`, and
  nothing is ever deleted (POP3 included).
* **`spaceweather`** — [Spaceweather.com](https://www.spaceweather.com/) daily
  stories: solar flares, auroras, meteors, near-Earth asteroids. The site has no
  feed and no per-story permalinks — its front page is one long document — so
  the plugin splits it on the ALL-CAPS headlines that open each story and links
  items to that day's archive page. `days_back` also walks back through the
  archive to collect earlier days on first run.
* **`cbet`** — Central Bureau Electronic Telegrams from the IAU Central Bureau
  for Astronomical Telegrams: comets, novae, supernovae and other transients.
  The site answers on HTTP only, so the default URL is deliberately `http://`.

  (The Astronomer's Telegram needs no plugin of its own — it publishes a feed,
  so the `rss` plugin covers it. Set `fetch_images: false` there: ATel entries
  are plain text and opening each one would just cost requests.)
* **`mpec`** — Minor Planet Electronic Circulars from the IAU Minor Planet
  Center: new asteroid and comet discoveries, orbit updates and observations.
  Each circular becomes an item with its id, subject and issue date. The noisy
  `DAILY ORBIT UPDATE` circulars are skipped by default, and `include` filters
  the rest (e.g. `COMET`).
* **`pl_news`** — 26 Polish news sites by name, no feed hunting: TVN24,
  TVP Info, Onet, **WP (Wirtualna Polska)**, Interia, Polsat News, RMF24,
  RMF FM, Radio ZET, Gazeta.pl, Rzeczpospolita, Newsweek, Wprost, Do Rzeczy,
  Gazeta Wyborcza + Wyborcza Biznes, PAP (Samorząd / Zdrowie / Nauka w Polsce),
  **Dziennik Gazeta Prawna**, Money.pl, Bankier, Business Insider PL,
  Spider's Web, Antyweb, Niebezpiecznik.
  Group names work too: `pap`, `wyborcza`, `radio`, `biznes`, `tech`, `ogolne`.

  Note `rmf24` (the news portal) and `rmffm` (the radio station's own site) are
  separate outlets, as are `gazeta` (Gazeta.pl) and `wyborcza` (Gazeta Wyborcza).

  Outlets are read as feeds where a feed exists. **Radio ZET and RMF FM publish
  none**, so they are scrape outlets: the listing page is read and each matching
  article opened for its own metadata — same result, same fields. TVP Info's
  feed lives at an unadvertised path and is malformed (it uses `media:` without
  declaring the namespace and leaves ampersands unescaped); `sdk.parse_feed`
  repairs both faults rather than rejecting the document. PAP's main
  wire (`www.pap.pl`) serves no content to non-browser clients, so the
  catalogue uses its three open services instead.

  **Wyborcza is headlines only.** It publishes no feed, and its article pages
  are an empty shell behind Agora's paywall — no title, date or image reaches a
  logged-out client. Both Wyborcza outlets therefore run in *listing* mode:
  headline, link and (about 60% of the time) the lead image are read straight
  off the section page, and items carry **no publication date**, so the app
  orders them by when it first saw them. An image is attached only when it
  provably belongs to that headline — an ambiguous one is dropped rather than
  risk showing the wrong picture.

  ```bash
  curl -X POST localhost:8000/api/sources -H 'Content-Type: application/json' -d '{
    "name": "Polskie media",
    "plugin": "pl_news",
    "config": {"outlets": "tvn24,onet,rmf24", "limit_per_outlet": 10,
               "exclude": "sport|plotki"},
    "interval_seconds": 600
  }'
  ```

  `outlets` takes a comma-separated list, a group name, or `all`; `custom_feeds`
  (`"Nazwa|https://…"`, `;`-separated) adds any site outside the catalogue.
  It strips `utm_*`-style tracking parameters from links so the same article is
  not stored twice, understands Polish month names in dates, and records each
  item's outlet in `author` so items stay attributable when one source pulls
  from several sites — replacing CMS logins (`tryton`, `admin`) that some feeds
  put there, while keeping real bylines.

## 3. Upload your own plugin

**Plugins → choose file → Upload**, or:

```bash
curl -X POST localhost:8000/api/plugins -F "file=@my_source.py"
```

The file is validated (must parse, must define `PLUGIN` and `fetch`), stored,
imported and immediately usable. A rejected upload leaves the previously
installed version untouched. See [PLUGINS.md](PLUGINS.md) for the contract.

> Uploaded plugins are Python code that runs inside the server process. Set
> `NEWSROOM_API_TOKEN` on anything that is not a private machine, and only
> upload code you trust.

## 4. Bookmarks and page export

**Bookmarks** save an item into a category of your own making ("Do
przeczytania", "Astronomia", …). Use **Save** on any card, or the API:

```bash
curl -X POST localhost:8000/api/bookmark-categories -H 'Content-Type: application/json' \
     -d '{"name": "Do przeczytania"}'
curl -X POST localhost:8000/api/bookmarks -H 'Content-Type: application/json' \
     -d '{"news_id": 42, "category_id": 1, "note": "na później"}'
```

A bookmark **keeps its own copy** of the title, link, image, date and source.
News is pruned by retention and deleted along with its source, so a saved item
that only pointed at a news row would quietly vanish; this way it survives both.
Saving an already-saved link moves it to the new category instead of
duplicating it, and deleting a category leaves its bookmarks in place,
uncategorised.

**Page export** downloads the whole article page — not just the summary — as a
PDF or as one tall JPG:

```
GET /api/news/{id}/export.pdf      GET /api/news/{id}/export.jpg
GET /api/bookmarks/{id}/export.pdf GET /api/bookmarks/{id}/export.jpg
```

A headless Chromium prints the page to PDF; the JPG is rendered from that PDF
and stitched into a single image, which is what makes it a full-page capture
rather than a screenshot of the visible window. Renders are cached on disk by
URL and format (first render of a heavy page takes tens of seconds, afterwards
it is instant), and the cache is swept oldest-first past
`NEWSROOM_EXPORT_CACHE_MB`. Needs `chromium` on PATH (or `NEWSROOM_CHROMIUM`)
and `poppler-utils` for the JPG; without them the endpoints answer 503 and the
rest of the app is unaffected.

## 5. API

| method | path | purpose |
|---|---|---|
| `GET` | `/api/news` | the feed — `limit`, `offset`, `q`, `source_id`, `since`, `with_image`, `bookmarked`, `order` |
| `GET`/`POST` | `/api/bookmarks` | list / save bookmarks (`category_id`, `uncategorised`, `q`) |
| `PATCH`/`DELETE` | `/api/bookmarks/{id}` | recategorise or annotate / remove |
| `GET`/`POST` | `/api/bookmark-categories` | list / create categories |
| `PATCH`/`DELETE` | `/api/bookmark-categories/{id}` | rename / remove (bookmarks kept) |
| `GET` | `/api/news/{id}/export.{pdf\|jpg}` | full-page capture of the article |
| `GET` | `/api/bookmarks/{id}/export.{pdf\|jpg}` | same, for a saved item |
| `GET` | `/api/news/{id}` | one item |
| `GET`/`POST` | `/api/sources` | list / create sources |
| `PATCH`/`DELETE` | `/api/sources/{id}` | edit / remove a source |
| `POST` | `/api/sources/{id}/refresh` | poll one source now |
| `POST` | `/api/refresh` | poll every enabled source now |
| `GET`/`POST` | `/api/plugins` | list / upload plugins |
| `POST` | `/api/plugins/{name}/test` | dry-run a plugin against a config |
| `DELETE` | `/api/plugins/{name}` | remove a plugin (`?force=true` if in use) |
| `GET`/`PUT` | `/api/settings` | read / change the polling interval |
| `GET` | `/api/status`, `/api/health` | diagnostics |

Writing endpoints require `X-API-Token` when `NEWSROOM_API_TOKEN` is set.

## 6. Android client

`android/` is a complete Gradle project (Kotlin, Jetpack Compose, Material 3,
Retrofit, Coil, WorkManager). Open it in Android Studio, or:

```bash
cd android && ./gradlew assembleDebug
```

The APK lands in `android/app/build/outputs/apk/debug/`.

### Release build

`assembleRelease` produces a signed, R8-shrunk APK (~1.8 MB against the debug
build's 18 MB). Signing credentials are read from `android/keystore.properties`,
which is gitignored along with the keystore itself:

```properties
storeFile=keystore/release.jks
storePassword=…
keyAlias=newsroom
keyPassword=…
```

Create your own keystore with:

```bash
keytool -genkeypair -v -keystore keystore/release.jks -alias newsroom         -keyalg RSA -keysize 4096 -validity 10950
```

then `./gradlew assembleRelease`. Without `keystore.properties` the release
build still runs but comes out unsigned, rather than failing.

> **Back up the keystore and its password.** Android identifies an app by its
> signing key: lose it and you can never ship an update that upgrades an
> installed copy — only a fresh install under a new package identity.

> **Building on an ARM host** (Raspberry Pi and friends): Google ships `aapt2`
> and `zipalign` as x86-64 binaries only, so the build fails with
> `aapt2: Syntax error: "(" unexpected` until they can be emulated. To build
> here anyway:
>
> ```bash
> sudo apt-get install -y qemu-user-static binfmt-support
> # a minimal amd64 sysroot for the emulated binaries
> mkdir -p ~/amd64-sysroot && cd ~/amd64-sysroot
> # extract libc6, libstdc++6, libgcc-s1 and zlib1g (amd64 .deb files) here, then:
> ln -sfn usr/lib lib && ln -sfn usr/lib64 lib64
> QEMU_LD_PREFIX=~/amd64-sysroot ./gradlew assembleDebug
> ```
>
> With that in place the debug APK builds on ARM in about 7 minutes. On x86-64
> or in Android Studio no such setup is needed.

The app mirrors the web UI's saving and export features: the bookmark button on
each article picks a category (or creates one), a **Bookmarks** screen filters by
category and lets items be moved, annotated or removed, and **PDF / JPG** on any
card downloads the full-page render and opens it in whatever app handles that
type. Exports are written to the app's own external files directory and shared
through a `FileProvider`, so no storage permission is needed.

On first launch open **Settings** and point it at your server
(`http://10.0.2.2:8000` from the emulator, `http://<lan-ip>:8000` from a real
phone), plus the API token if you set one. The app shows the card feed with
premier image, source badge and date, opens articles in the browser, filters by
source and by search text, triggers an immediate server-side fetch, changes the
**server's** polling interval, and syncs in the background through WorkManager
on its own configurable cadence (Android's floor is 15 minutes).

## Updating without reloading

The board is never rebuilt behind the reader. Every action touches only what
changed:

* **New items** arrive by polling `/api/news` every 60 seconds (and whenever the
  tab regains focus). Only items the page has not seen are prepended, with a
  brief highlight and a sticky **"N new"** pill; existing cards keep their DOM
  nodes, so the scroll position and any open menu survive.
* **Bookmarking** replaces just that one card, using the bookmark returned by
  the API - no feed reload.
* **Fetch now** polls the sources server-side, then pulls in only the new items.
* **Per-source Fetch** on the Sources tab redraws that single table row.

Polling pauses while the tab is hidden, and a failed poll is swallowed rather
than interrupting reading with an error.

## Desktop notifications

Two ways to be told about new items, because they suit different setups.

**From the browser** - Settings, *Desktop notifications*. While the tab is open,
each new item the poller finds raises a native toast (Windows, macOS or Linux),
at most three per check plus a summary; clicking one opens the article. Browsers
only expose the Notification API on a **secure origin**, so this works on
`http://localhost:8080` or behind https, and the toggle says so plainly when
opened on a LAN address instead of silently doing nothing.

**From Windows, without a browser** - `tools/windows-notifier.ps1` polls the
server and raises Windows toasts. No HTTPS and no open tab needed:

```powershell
.\windows-notifier.ps1 -Server http://192.168.1.50:8080
.\windows-notifier.ps1 -Server http://192.168.1.50:8080 -Match 'AI|Copilot' -IntervalSeconds 120
```

Install BurntToast (`Install-Module -Name BurntToast -Scope CurrentUser`) for
clickable toasts; without it the script falls back to tray balloons, which need
nothing installed. `-SourceId` limits it to one source, `-Match` to titles
matching a regex, and `-Token` passes `X-API-Token`. To run it at logon, point a
Task Scheduler "At log on" task at
`powershell.exe -WindowStyle Hidden -ExecutionPolicy Bypass -File ...`.

The script remembers the last item id it saw in
`%LOCALAPPDATA%\myPrivateNewsRoom\last-id.txt`, so a restart does not replay
old news, and its first run only marks the current position. Both routes use the
`after_id` query parameter, which returns what was *collected* after a given id
rather than what was *published* after a date.

## Web UI layout

**Category colours.** Every source is given a stable hue, derived from its name,
and shown on the card's top edge and its badge. A colour key sits above the feed
— click an entry to filter to that source, click again to clear. The hues are
generated, so a new source is colour-coded the moment it is added, with nothing
to configure.

**Full-width board** (on by default) stretches the grid across the whole window;
turn it off in the toolbar or under Settings → Layout for a centred column.


The board is **masonry**: each card is placed in whichever column is currently
shortest, so cards of different heights pack tightly instead of leaving ragged
gaps. Placement follows the feed order, which keeps the newest items running
left-to-right across the top — unlike a CSS multi-column layout, which would
fill the first column top-to-bottom before starting the second.

The **Columns** control (in the toolbar, and as a slider under Settings →
Layout) sets how many columns, from 1 to 6; the board re-packs when it changes,
when the full-width toggle flips, and when the window crosses a breakpoint
(at most 3 columns under 1100 px, 2 under 820 px, 1 under 560 px). `?cols=5`
in the URL overrides the stored choice, and each tab has its own address
(`#feed`, `#sources`, `#plugins`, `#settings`), so a view can be bookmarked. The choice is
remembered in the browser. Narrow windows step down automatically — at most 3
columns under 1100 px, 2 under 820 px and a single column under 560 px — and the
denser settings shrink the images and type so cards stay balanced.

## Notes

* Storage is a single SQLite file (`backend/data/newsroom.db`, WAL mode).
* Items are deduplicated per source on the feed's `guid`, falling back to the link.
* The poller runs each due source in its own thread; a slow or broken source
  cannot stall the others, and its error is shown next to the source.
