"""Core tests: run with `python3 -m unittest discover -s tests` from backend/.

They exercise storage, the plugin engine and one poll cycle against a feed
served from a temporary local HTTP server - no network access needed.
"""
import http.server
import json
import socket
import os
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="newsroom-test-")
os.environ["NEWSROOM_DATA_DIR"] = TMP
os.environ["NEWSROOM_PLUGIN_DIR"] = str(Path(TMP) / "plugins")
os.environ["NEWSROOM_DB"] = str(Path(TMP) / "test.db")

from newsroom import config, db, plugins, scheduler, sdk  # noqa: E402

FEED = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
<channel><title>T</title>
<item><title>One</title><link>http://%(host)s/a1.html</link><guid>g1</guid>
  <pubDate>Tue, 18 Aug 2026 09:30:00 GMT</pubDate>
  <description>&lt;p&gt;Body &lt;b&gt;one&lt;/b&gt;&lt;/p&gt;</description>
  <media:content url="http://%(host)s/one.jpg"/></item>
<item><title>Two</title><link>http://%(host)s/a2.html</link><guid>g2</guid>
  <pubDate>Tue, 18 Aug 2026 11:00:00 GMT</pubDate></item>
</channel></rss>"""

ARTICLE = b'<html><head><meta property="og:image" content="/lead.jpg"></head></html>'


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        host = f"{self.server.server_address[0]}:{self.server.server_address[1]}"
        body = (FEED % {"host": host}).encode() if self.path.endswith(".xml") else ARTICLE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class NewsroomTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        db.init()
        plugins.load_all()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_bundled_plugins_load(self):
        self.assertIn("rss", plugins.loaded_names())
        self.assertIn("json_api", plugins.loaded_names())

    def test_rss_plugin_extracts_all_required_fields(self):
        items = plugins.run("rss", {"url": f"{self.base}/feed.xml", "fetch_images": True})
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["title"], "One")
        self.assertEqual(first["link"], f"{self.base}/a1.html")     # link
        self.assertEqual(first["image"], f"{self.base}/one.jpg")    # premier image
        self.assertIsNotNone(first["published_at"])                 # date
        self.assertEqual(first["summary"], "Body one")
        # the second item has no feed image, so it falls back to the article's og:image
        self.assertEqual(items[1]["image"], f"{self.base}/lead.jpg")

    def test_poll_stores_and_deduplicates(self):
        import time

        cur = db.execute(
            "INSERT INTO sources (name, plugin, config, enabled, created_at) VALUES (?,?,?,1,?)",
            ("Wire", "rss", json.dumps({"url": f"{self.base}/feed.xml"}), time.time()),
        )
        source_id = cur.lastrowid

        first = scheduler.run_source(source_id)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["added"], 2)

        second = scheduler.run_source(source_id)
        self.assertEqual(second["added"], 0, "re-polling must not duplicate items")

        rows = db.query("SELECT * FROM news WHERE source_id = ?", (source_id,))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["source_name"], "Wire")            # source

    def test_broken_plugin_is_recorded_not_raised(self):
        import time

        code = 'PLUGIN = {"name": "boom"}\ndef fetch(config):\n    raise RuntimeError("nope")\n'
        plugins.install("boom.py", code)
        cur = db.execute(
            "INSERT INTO sources (name, plugin, config, enabled, created_at) VALUES (?,?,?,1,?)",
            ("Boom", "boom", "{}", time.time()),
        )
        result = scheduler.run_source(cur.lastrowid)
        self.assertEqual(result["status"], "error")
        self.assertIn("nope", result["error"])

    def test_upload_validation(self):
        with self.assertRaises(plugins.PluginRejected):
            plugins.install("x.py", "def fetch(c):\n    return []\n")       # no PLUGIN
        with self.assertRaises(plugins.PluginRejected):
            plugins.install("x.py", 'PLUGIN = {"name": "x"}\n')             # no fetch
        with self.assertRaises(plugins.PluginRejected):
            plugins.install("x.py", "def fetch(c) return\n")                # syntax error

    def test_bad_plugin_does_not_replace_working_one(self):
        good = ('PLUGIN = {"name": "keeper", "version": "1"}\n'
                'def fetch(config):\n    return [{"title": "a", "link": "https://e.com/a"}]\n')
        plugins.install("keeper.py", good)
        with self.assertRaises(plugins.PluginRejected):
            plugins.install("keeper.py", 'PLUGIN = {"name": "keeper"}\nx = (\n')
        self.assertEqual(len(plugins.run("keeper", {})), 1)

    def test_items_are_normalized_and_filtered(self):
        raw = [
            {"title": "ok", "link": "https://e.com/1"},
            {"title": "", "link": "https://e.com/2"},        # no title -> dropped
            {"title": "no link"},                            # no link   -> dropped
            {"title": "bad scheme", "link": "ftp://e.com"},  # -> dropped
        ]
        self.assertEqual(len(plugins.normalize(raw)), 1)

    def test_double_encoded_entities_are_decoded(self):
        """Niebezpiecznik's feed escapes twice: &amp;#8220; arrives as &#8220;."""
        items = plugins.normalize([
            {"title": "Odszedł Paweł &#8220;kravietz&#8221; Krawczyk",
             "link": "https://niebezpiecznik.pl/post/x",
             "summary": "Tekst z &amp; i &#8222;cytatem&#8221;"},
        ])
        self.assertEqual(items[0]["title"], "Odszedł Paweł \u201ckravietz\u201d Krawczyk")
        self.assertIn("„cytatem”", items[0]["summary"])

    def test_plain_titles_are_left_alone(self):
        items = plugins.normalize([
            {"title": "Cena spadła o 50% & zysk rośnie", "link": "https://e.pl/a"},
        ])
        self.assertEqual(items[0]["title"], "Cena spadła o 50% & zysk rośnie")

    def test_date_parsing_formats(self):
        self.assertIsNotNone(sdk.parse_date("Tue, 18 Aug 2026 09:30:00 GMT"))
        self.assertIsNotNone(sdk.parse_date("2026-08-18T09:30:00Z"))
        self.assertIsNotNone(sdk.parse_date(1787000000))
        self.assertIsNone(sdk.parse_date("not a date"))

    def test_interval_override(self):
        import time

        db.set_setting("poll_interval_seconds", 600)
        cur = db.execute(
            "INSERT INTO sources (name, plugin, config, enabled, created_at) VALUES (?,?,?,1,?)",
            ("Interval", "rss", "{}", time.time()),
        )
        row = db.query_one("SELECT * FROM sources WHERE id = ?", (cur.lastrowid,))
        self.assertEqual(scheduler.source_interval(row), 600)
        db.execute("UPDATE sources SET interval_seconds = 120 WHERE id = ?", (row["id"],))
        row = db.query_one("SELECT * FROM sources WHERE id = ?", (row["id"],))
        self.assertEqual(scheduler.source_interval(row), 120)


class ScrapingHelperTest(unittest.TestCase):
    """The scraping helpers the html_scraper and pl_news plugins share."""

    def test_find_links_filters_and_deduplicates(self):
        html = (
            '<a href="/polska/dlugi-slug-artykulu-tutaj">a</a>'
            '<a href="/polska/dlugi-slug-artykulu-tutaj">duplicate</a>'
            '<a href="/tag/kategoria/pis">tag</a>'
        )
        links = sdk.find_links(
            html, "https://wiadomosci.radiozet.pl/", r"/(polska|swiat)/[a-z0-9-]{15,}$"
        )
        self.assertEqual(links, ["https://wiadomosci.radiozet.pl/polska/dlugi-slug-artykulu-tutaj"])

    def test_find_links_rejects_a_bad_pattern(self):
        with self.assertRaises(sdk.PluginError):
            sdk.find_links("<a href='/x'>x</a>", "https://e.pl", "(unclosed")

    def test_rss1_rdf_feed_is_read(self):
        """The Astronomer's Telegram publishes RSS 1.0, where every element
        sits in the RSS namespace and items carry rdf:about instead of a guid."""
        rdf = """<?xml version="1.0" encoding="UTF-8"?>
        <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                 xmlns="http://purl.org/rss/1.0/"
                 xmlns:dc="http://purl.org/dc/elements/1.1/">
          <channel rdf:about="https://www.astronomerstelegram.org/?rss">
            <title>Top ATels</title>
          </channel>
          <item rdf:about="https://www.astronomerstelegram.org/?read=17997">
            <title>ATel 17997: SVOM/ECLAIRs detection</title>
            <link>https://www.astronomerstelegram.org/?read=17997</link>
            <description>Observation of a symbiotic system.</description>
            <dc:date>2026-08-21T09:12:00+00:00</dc:date>
            <dc:creator>H. Yang, F. Cangemi</dc:creator>
          </item>
        </rdf:RDF>"""
        items = sdk.parse_feed(rdf, "https://astronomerstelegram.org/")
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["title"], "ATel 17997: SVOM/ECLAIRs detection")
        self.assertEqual(item["link"], "https://www.astronomerstelegram.org/?read=17997")
        self.assertEqual(item["author"], "H. Yang, F. Cangemi")
        self.assertIsNotNone(item["published_at"])
        self.assertEqual(item["summary"], "Observation of a symbiotic system.")

    def test_rss1_item_without_a_link_falls_back_to_rdf_about(self):
        rdf = """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                           xmlns="http://purl.org/rss/1.0/">
          <item rdf:about="https://e.pl/telegram/1"><title>Bez linku</title></item>
        </rdf:RDF>"""
        items = sdk.parse_feed(rdf)
        self.assertEqual(items[0]["link"], "https://e.pl/telegram/1")
        self.assertEqual(items[0]["guid"], "https://e.pl/telegram/1")

    def test_atom_entry_link_and_author_are_read(self):
        atom = """<feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Tytuł</title>
            <link rel="alternate" href="https://e.pl/a"/>
            <link rel="edit" href="https://e.pl/edit"/>
            <published>2026-08-19T10:00:00Z</published>
            <author><name>Anna Kowalska</name></author>
            <summary>Streszczenie</summary>
          </entry>
        </feed>"""
        items = sdk.parse_feed(atom)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["link"], "https://e.pl/a", "the alternate link wins")
        self.assertEqual(items[0]["author"], "Anna Kowalska")
        self.assertIsNotNone(items[0]["published_at"])

    def test_rss2_media_and_enclosure_images_still_work(self):
        rss = """<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
          <channel><item>
            <title>Z obrazkiem</title><link>https://e.pl/1</link>
            <media:content url="https://e.pl/lead.jpg"/>
            <pubDate>Tue, 18 Aug 2026 09:30:00 GMT</pubDate>
          </item>
          <item>
            <title>Enclosure</title><link>https://e.pl/2</link>
            <enclosure url="https://e.pl/enc.jpg" type="image/jpeg"/>
          </item></channel>
        </rss>"""
        items = sdk.parse_feed(rss)
        self.assertEqual([i["image"] for i in items],
                         ["https://e.pl/lead.jpg", "https://e.pl/enc.jpg"])

    def test_broken_feed_xml_is_repaired(self):
        """TVP Info uses media: without declaring it, and leaves & unescaped."""
        broken = (
            '<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>'
            "<item><title>Sejm &amp Senat o budżecie</title>"
            "<link>https://www.tvp.info/1,artykul.html</link>"
            '<media:content url="https://s.tvp.pl/lead.jpg"/>'
            "<pubDate>Tue, 18 Aug 2026 09:30:00 GMT</pubDate>"
            "</item></channel></rss>"
        )
        with self.assertRaises(ET.ParseError):
            ET.fromstring(broken)          # a strict parser refuses it
        items = sdk.parse_feed(broken)     # the SDK repairs and reads it
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["image"], "https://s.tvp.pl/lead.jpg")
        self.assertIn("&", items[0]["title"])
        self.assertIsNotNone(items[0]["published_at"])

    def test_repair_leaves_valid_feeds_untouched(self):
        good = ('<?xml version="1.0"?><rss version="2.0"><channel>'
                "<item><title>A</title><link>https://e.pl/a</link></item></channel></rss>")
        self.assertEqual(sdk.repair_feed_xml(good), good)

    def test_repair_keeps_existing_entities(self):
        repaired = sdk.repair_feed_xml("<rss><t>A &amp; B &#243; C &raw; D</t></rss>")
        self.assertIn("&amp; B", repaired)
        self.assertIn("&#243;", repaired)
        self.assertIn("&amp;raw;", repaired, "a bare entity-like token gets escaped")

    def test_read_listing_takes_headlines_from_link_text(self):
        html = (
            '<div><figure><img src="/lead.jpg"></figure>'
            '<h2><a href="/7,1,2,pierwszy-artykul-o-czyms.html">'
            'Pierwszy artykuł o czymś ważnym</a></h2></div>'
            '<a href="/7,1,3,drugi.html">za krótki</a>'
        )
        items = sdk.read_listing(html, "https://wyborcza.pl/", r"7,\d+,\d+,")
        self.assertEqual(len(items), 1, "short link text is not a headline")
        self.assertEqual(items[0]["title"], "Pierwszy artykuł o czymś ważnym")
        self.assertEqual(items[0]["image"], "https://wyborcza.pl/lead.jpg")
        self.assertIsNone(items[0]["published_at"], "listings carry no dates")

    def test_site_logo_is_never_used_as_an_article_image(self):
        """Wyborcza's masthead sat in the markup and landed on every card."""
        html = (
            '<div><img src="/images/wyborcza-logo.svg">'
            '<a href="/7,1,2,artykul.html">Tytuł artykułu o czymś ważnym</a></div>'
        )
        items = sdk.read_listing(html, "https://wyborcza.pl/", r"7,\d+,\d+,")
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["image"])

        for url in ("https://e.pl/logo.png", "https://e.pl/a/sprite.png",
                    "https://e.pl/icon.svg", "https://e.pl/x.ico", "data:image/gif;base64,x",
                    # a site header served as an article's og:image
                    "https://astronomynow.com/wp-content/uploads/2016/12/AN-Header-1080.jpg"):
            self.assertTrue(sdk.is_decorative_image(url), url)
        for url in ("https://bi.im-g.pl/im/68/72/1f/z32975720R.jpg",
                    "https://e.pl/photos/2026/08/story.webp"):
            self.assertFalse(sdk.is_decorative_image(url), url)

    def test_og_image_pointing_at_site_furniture_is_skipped(self):
        """Astronomy Now serves its header banner as some articles' og:image."""
        html = (
            '<meta property="og:image" content="/uploads/AN-Header-1080.jpg">'
            '<article><img src="/uploads/2026/08/mars-rover-site.jpg"></article>'
        )
        self.assertEqual(
            sdk.find_lead_image(html, "https://astronomynow.com/a/"),
            "https://astronomynow.com/uploads/2026/08/mars-rover-site.jpg",
        )

    def test_a_logo_chosen_as_og_image_is_trusted(self):
        """An article about CSS may lead with the CSS logo - the publisher's call."""
        html = '<meta property="og:image" content="/uploads/2026/03/CSS-Logo.webp">'
        self.assertEqual(
            sdk.find_lead_image(html, "https://shkspr.mobi/blog/"),
            "https://shkspr.mobi/uploads/2026/03/CSS-Logo.webp",
        )
        # but a logo found loose in the markup is still furniture
        self.assertTrue(sdk.is_decorative_image("https://e.pl/CSS-Logo.webp"))
        self.assertFalse(sdk.is_decorative_image("https://e.pl/CSS-Logo.webp", strict=False))

    def test_a_real_og_image_still_wins(self):
        html = ('<meta property="og:image" content="/uploads/2026/08/lead.jpg">'
                '<article><img src="/uploads/other.jpg"></article>')
        self.assertEqual(
            sdk.find_lead_image(html, "https://e.pl/a/"),
            "https://e.pl/uploads/2026/08/lead.jpg",
        )

    def test_read_listing_refuses_an_ambiguous_image(self):
        """An image shared by two article links must not be pinned on one."""
        html = (
            '<section><img src="/shared.jpg">'
            '<a href="/7,1,2,pierwszy-artykul.html">Pierwszy artykuł o czymś ważnym</a>'
            '<a href="/7,1,3,drugi-artykul.html">Drugi artykuł o czymś zupełnie innym</a>'
            "</section>"
        )
        items = sdk.read_listing(html, "https://wyborcza.pl/", r"7,\d+,\d+,")
        self.assertEqual(len(items), 2)
        self.assertTrue(all(i["image"] is None for i in items))

    def test_repeated_image_is_treated_as_a_placeholder(self):
        """Wyborcza gives photo-less articles its wordmark; 12 cards shared it."""
        html = "".join(
            f'<div><img src="https://bi.gazeta.pl/im/3/17117/m17117193.png">'
            f'<a href="/7,1,{n},artykul-{n}.html">Artykuł numer {n} o czymś ważnym</a></div>'
            for n in range(4)
        ) + ('<div><img src="https://bi.im-g.pl/im/own-photo.jpg">'
             '<a href="/7,1,99,artykul-99.html">Artykuł z własnym zdjęciem tutaj</a></div>')
        items = sdk.read_listing(html, "https://wyborcza.pl/", r"7,\d+,\d+,", limit=10)
        self.assertEqual(len(items), 5)
        shared = [i for i in items if i["link"].endswith(tuple(f"{n}.html" for n in range(4)))]
        self.assertTrue(all(i["image"] is None for i in shared), "placeholder dropped")
        own = next(i for i in items if i["link"].endswith("99.html"))
        self.assertEqual(own["image"], "https://bi.im-g.pl/im/own-photo.jpg")

    def test_read_listing_survives_unclosed_tags(self):
        html = ('<div><p><a href="/7,1,2,jakis-dlugi-tytul-tutaj.html">'
                'Jakiś dłuższy tytuł artykułu</a>')
        items = sdk.read_listing(html, "https://wyborcza.pl/", r"7,\d+,\d+,")
        self.assertEqual(len(items), 1)

    def test_read_listing_respects_the_limit(self):
        html = "".join(
            f'<a href="/7,1,{n},artykul-{n}.html">Artykuł numer {n} o czymś ważnym</a>'
            for n in range(10)
        )
        self.assertEqual(len(sdk.read_listing(html, "https://wyborcza.pl/", r"7,\d+,\d+,", limit=3)), 3)

    def test_meta_tag_reads_any_attribute_order(self):
        self.assertEqual(
            sdk.meta_tag('<meta content="1787162971" property="article:published_time">',
                         "article:published_time"),
            "1787162971",
        )
        self.assertIsNone(sdk.meta_tag("<meta name='other' content='x'>", "og:title"))

    def test_json_ld_finds_article_inside_graph(self):
        html = ('<script type="application/ld+json">'
                '{"@graph":[{"@type":"WebPage"},{"@type":"NewsArticle",'
                '"headline":"Tytuł","datePublished":"2026-08-19T12:00:00+02:00"}]}'
                "</script>")
        node = sdk.json_ld(html)
        self.assertEqual(node["headline"], "Tytuł")
        self.assertIsNotNone(sdk.parse_date(node["datePublished"]))

    def test_json_ld_survives_broken_json(self):
        self.assertEqual(sdk.json_ld('<script type="application/ld+json">{oops</script>'), {})

    def test_epoch_dates_are_understood(self):
        """Radio ZET publishes article:published_time as a UNIX timestamp."""
        self.assertEqual(sdk.parse_date("1787162971"), 1787162971.0)
        self.assertEqual(sdk.parse_date("1787162971000"), 1787162971.0)
        self.assertIsNone(sdk.parse_date("12345"), "too small to be an epoch")


class PolishPluginTest(unittest.TestCase):
    """The pl_news catalogue plugin, exercised without touching the network."""

    @classmethod
    def setUpClass(cls):
        db.init()
        plugins.load_all()
        import sys

        cls.mod = sys.modules["newsroom_plugin_pl_news"]

    def test_catalogue_entries_are_wellformed(self):
        for key, outlet in self.mod.OUTLETS.items():
            self.assertRegex(key, r"^[a-z0-9_]+$")
            self.assertTrue(outlet["name"])
            self.assertIn(outlet["mode"], ("feed", "scrape", "listing"))
            self.assertTrue(outlet["url"].startswith("http"), f"{key} has a bad URL")
            if outlet["mode"] in ("scrape", "listing"):
                import re

                re.compile(outlet["pattern"])  # must be a valid regex

    def test_groups_reference_real_outlets(self):
        for group, members in self.mod.GROUPS.items():
            for key in members:
                self.assertIn(key, self.mod.OUTLETS, f"group {group} points at {key}")

    def test_group_expands_to_its_members(self):
        feeds = self.mod._selected_feeds({"outlets": "pap"})
        self.assertEqual(len(feeds), len(self.mod.GROUPS["pap"]))
        self.assertTrue(all("pap" in f["url"] or "naukawpolsce" in f["url"] for f in feeds))

    def test_duplicate_selection_is_collapsed(self):
        feeds = self.mod._selected_feeds({"outlets": "tvn24,tvn24,ogolne"})
        urls = [f["url"] for f in feeds]
        self.assertEqual(len(urls), len(set(urls)))

    def test_radiozet_is_a_scrape_outlet(self):
        self.assertEqual(self.mod.OUTLETS["radiozet"]["mode"], "scrape")

    def test_unknown_outlet_is_reported(self):
        with self.assertRaises(sdk.PluginError) as ctx:
            self.mod.fetch({"outlets": "tvn24,nie_ma_takiego"})
        self.assertIn("nie_ma_takiego", str(ctx.exception))

    def test_no_outlet_selected_is_reported(self):
        with self.assertRaises(sdk.PluginError):
            self.mod.fetch({"outlets": ""})

    def test_all_selects_the_whole_catalogue(self):
        feeds = self.mod._selected_feeds({"outlets": "all"})
        self.assertEqual(len(feeds), len(self.mod.OUTLETS))

    def test_custom_feeds_are_added(self):
        feeds = self.mod._selected_feeds(
            {"outlets": "tvn24", "custom_feeds": "Moje|https://e.pl/rss; Twoje|https://f.pl/rss"}
        )
        self.assertEqual(len(feeds), 3)
        self.assertIn(
            {"name": "Moje", "mode": "feed", "url": "https://e.pl/rss"}, feeds
        )

    def test_malformed_custom_feed_is_rejected(self):
        with self.assertRaises(sdk.PluginError):
            self.mod._selected_feeds({"outlets": "tvn24", "custom_feeds": "brak-urla"})

    def test_tracking_parameters_are_stripped(self):
        self.assertEqual(
            self.mod.clean_link("https://tvn24.pl/a-123.html?utm_source=rss&utm_medium=feed"),
            "https://tvn24.pl/a-123.html",
        )
        self.assertEqual(
            self.mod.clean_link("https://onet.pl/x?id=7&srcc=ucs"),
            "https://onet.pl/x?id=7",
            "real query parameters must survive",
        )

    def test_wp_and_gazetaprawna_are_in_the_catalogue(self):
        self.assertEqual(self.mod.OUTLETS["wp"]["name"], "Wirtualna Polska")
        self.assertIn("gazetaprawna", self.mod.GROUPS["biznes"])
        self.assertTrue(self.mod.OUTLETS["gazetaprawna"]["url"].endswith("/.feed"))

    def test_radio_group_and_new_outlets(self):
        self.assertEqual(self.mod.OUTLETS["tvpinfo"]["mode"], "feed")
        self.assertEqual(self.mod.OUTLETS["rmffm"]["mode"], "scrape")
        self.assertEqual(len(self.mod._selected_feeds({"outlets": "radio"})), 3)
        # rmf24 (the news portal) and rmffm (the radio site) are separate
        self.assertNotEqual(
            self.mod.OUTLETS["rmf24"]["url"], self.mod.OUTLETS["rmffm"]["url"]
        )

    def test_wyborcza_outlets_are_listing_mode(self):
        for key in ("wyborcza", "wyborcza_biz"):
            self.assertEqual(self.mod.OUTLETS[key]["mode"], "listing")
        self.assertEqual(len(self.mod._selected_feeds({"outlets": "wyborcza"})), 2,
                         "the 'wyborcza' group covers both sites")

    def test_cms_usernames_are_replaced_by_the_outlet(self):
        clean = self.mod._clean_author
        self.assertEqual(clean("tryton", "PAP Nauka"), "PAP Nauka")
        self.assertEqual(clean("admin", "PAP Samorząd"), "PAP Samorząd")
        self.assertEqual(clean("", "TVN24"), "TVN24")
        self.assertEqual(clean(None, "TVN24"), "TVN24")
        self.assertEqual(
            clean("zdrowie.pap.pl - Serwis Zdrowie", "PAP Zdrowie"), "PAP Zdrowie"
        )
        self.assertEqual(clean("Paweł Marcinkiewicz", "RMF24"), "Paweł Marcinkiewicz",
                         "a real byline must survive")

    def test_polish_month_names_parse(self):
        stamp = self.mod._parse_pl_date("Opublikowano 19 sierpnia 2026, 14:30")
        self.assertIsNotNone(stamp)
        import datetime

        parsed = datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc)
        self.assertEqual((parsed.year, parsed.month, parsed.day), (2026, 8, 19))
        self.assertIsNone(self.mod._parse_pl_date("bez daty"))


class HtmlScraperTest(unittest.TestCase):
    """Title cleanup in the generic scraper."""

    @classmethod
    def setUpClass(cls):
        db.init()
        plugins.load_all()
        import sys

        cls.mod = sys.modules["newsroom_plugin_html_scraper"]

    def _run(self, title, **config):
        mod = self.mod
        # sdk is shared by every plugin, so each patch must be undone
        saved = {name: getattr(mod.sdk, name)
                 for name in ("http_get", "find_links", "read_article")}
        mod.sdk.http_get = lambda *a, **k: "<html></html>"
        mod.sdk.find_links = lambda *a, **k: ["https://wiocha.pl/1-a"]
        mod.sdk.read_article = lambda url, **k: {
            "title": title, "link": url, "image": None, "published_at": None,
            "summary": None, "author": None, "guid": url,
        }
        try:
            return mod.fetch({"url": "https://wiocha.pl/", "link_pattern": "x", **config})
        finally:
            for name, value in saved.items():
                setattr(mod.sdk, name, value)

    def test_site_prefix_is_removed(self):
        items = self._run("Wiocha.pl - Taniej już było", title_strip=r"^Wiocha\.pl")
        self.assertEqual(items[0]["title"], "Taniej już było")

    def test_title_is_kept_when_the_pattern_would_empty_it(self):
        items = self._run("Wiocha.pl", title_strip=r"^Wiocha\.pl")
        self.assertEqual(items[0]["title"], "Wiocha.pl")

    def _run_many(self, articles, **config):
        """Drive fetch() over a fixed set of (link, article) pairs."""
        mod = self.mod
        saved = {name: getattr(mod.sdk, name)
                 for name in ("http_get", "find_links", "read_article")}
        mod.sdk.http_get = lambda *a, **k: "<html></html>"
        mod.sdk.find_links = lambda *a, **k: list(articles.keys())
        mod.sdk.read_article = lambda url, **k: articles.get(url)
        try:
            return mod.fetch({"url": "https://e.pl/", "link_pattern": "x", **config})
        finally:
            for name, value in saved.items():
                setattr(mod.sdk, name, value)

    def test_link_exclude_skips_tag_and_promo_pages(self):
        import time

        articles = {
            "https://e.pl/science/real-article-here": {
                "title": "Real", "link": "https://e.pl/science/real-article-here",
                "image": None, "published_at": time.time(), "summary": None,
                "author": None, "guid": "1",
            },
            "https://e.pl/tags/this-week": {
                "title": "Tag page", "link": "https://e.pl/tags/this-week",
                "image": None, "published_at": time.time(), "summary": None,
                "author": None, "guid": "2",
            },
        }
        items = self._run_many(articles, link_exclude="/tags/")
        self.assertEqual([i["title"] for i in items], ["Real"])

    def test_max_age_drops_evergreen_promos_and_dateless_pages(self):
        import time

        now = time.time()
        articles = {
            "https://e.pl/a/fresh-news-story-today": {
                "title": "Fresh", "link": "https://e.pl/a/fresh-news-story-today",
                "image": None, "published_at": now - 86400, "summary": None,
                "author": None, "guid": "1",
            },
            "https://e.pl/a/last-years-product-roundup": {
                "title": "Old promo", "link": "https://e.pl/a/last-years-product-roundup",
                "image": None, "published_at": now - 300 * 86400, "summary": None,
                "author": None, "guid": "2",
            },
            "https://e.pl/a/undated-hub-page-here": {
                "title": "No date", "link": "https://e.pl/a/undated-hub-page-here",
                "image": None, "published_at": None, "summary": None,
                "author": None, "guid": "3",
            },
        }
        items = self._run_many(articles, max_age_days=45)
        self.assertEqual([i["title"] for i in items], ["Fresh"])

        # without the limit, everything the pattern matched is kept
        self.assertEqual(len(self._run_many(articles)), 3)

    def test_a_shared_background_is_not_kept_as_an_article_image(self):
        """Cognity serves the same blog background as every post's og:image."""
        import time

        now = time.time()
        shared = "https://www.cognity.pl/img/blog_top_bg.png"
        articles = {
            f"https://e.pl/a/post-number-{n}": {
                "title": f"Post {n}", "link": f"https://e.pl/a/post-number-{n}",
                "image": shared, "published_at": now, "summary": None,
                "author": None, "guid": str(n),
            }
            for n in range(3)
        }
        articles["https://e.pl/a/its-own-picture"] = {
            "title": "Own", "link": "https://e.pl/a/its-own-picture",
            "image": "https://e.pl/uploads/own.jpg", "published_at": now,
            "summary": None, "author": None, "guid": "own",
        }
        by_title = {i["title"]: i for i in self._run_many(articles)}
        self.assertTrue(all(by_title[f"Post {n}"]["image"] is None for n in range(3)))
        self.assertEqual(by_title["Own"]["image"], "https://e.pl/uploads/own.jpg")

    def test_two_articles_may_share_an_image(self):
        """Only a picture used three or more times counts as a stand-in."""
        import time

        now = time.time()
        shared = "https://e.pl/uploads/series-header.jpg"
        articles = {
            f"https://e.pl/a/part-number-{n}": {
                "title": f"Part {n}", "link": f"https://e.pl/a/part-number-{n}",
                "image": shared, "published_at": now, "summary": None,
                "author": None, "guid": str(n),
            }
            for n in range(2)
        }
        self.assertTrue(all(i["image"] == shared for i in self._run_many(articles)))

    def test_invalid_link_exclude_is_reported(self):
        with self.assertRaises(sdk.PluginError):
            self._run_many({}, link_exclude="(unclosed")

    def test_bad_pattern_is_reported(self):
        with self.assertRaises(sdk.PluginError):
            self._run("x", title_strip="(unclosed")


class RssPluginFilterTest(unittest.TestCase):
    """Link filters, which pick one section out of a site-wide feed."""

    FEED = """<rss version="2.0"><channel>
      <item><title>Split Comet</title>
        <link>https://skyandtelescope.org/astronomy-news/spying-on-a-split-comet/</link>
        <pubDate>Tue, 18 Aug 2026 09:30:00 GMT</pubDate></item>
      <item><title>Press release</title>
        <link>https://skyandtelescope.org/astronomy-press-releases/some-release/</link></item>
      <item><title>Shop item</title>
        <link>https://shopatsky.com/products/telescope</link></item>
    </channel></rss>"""

    @classmethod
    def setUpClass(cls):
        db.init()
        plugins.load_all()
        import sys

        cls.mod = sys.modules["newsroom_plugin_rss"]

    def _fetch(self, **config):
        original = self.mod.sdk.http_get
        self.mod.sdk.http_get = lambda *a, **k: self.FEED
        try:
            return self.mod.fetch({"url": "https://e.pl/feed", "fetch_images": False, **config})
        finally:
            self.mod.sdk.http_get = original

    def test_link_include_keeps_one_section(self):
        items = self._fetch(link_include="/astronomy-news/")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Split Comet")

    def test_link_exclude_drops_a_section(self):
        items = self._fetch(link_exclude="shopatsky|press-releases")
        self.assertEqual(len(items), 1)

    def test_filters_combine_with_the_title_filter(self):
        self.assertEqual(len(self._fetch(include="comet", link_include="/astronomy-news/")), 1)
        self.assertEqual(len(self._fetch(include="nothing", link_include="/astronomy-news/")), 0)

    def test_no_filter_keeps_everything(self):
        self.assertEqual(len(self._fetch()), 3)


class MpecPluginTest(unittest.TestCase):
    """The Minor Planet Center circular reader, against a captured page."""

    PAGE = """
    <h2>Recent MPECs</h2>
    <ul>
    <p><li><a href="/mpec/K26/K26Q41.html"><i>MPEC</i> 2026-Q41</a> (2026 August 19)
       <ul>
       <li>2026 QF
       </ul>
    <p><li><a href="/mpec/K26/K26Q40.html"><i>MPEC</i> 2026-Q40</a> (2026 August 19)
       <ul>
       <li>DAILY ORBIT UPDATE (2026 August 19)
       </ul>
    <p><li><a href="/mpec/K26/K26Q39.html"><i>MPEC</i> 2026-Q39</a> (2026 August 18)
       <ul>
       <li>COMET C/2026 P1 (PANSTARRS)
       </ul>
    </ul>
    <p><hr>
    """

    @classmethod
    def setUpClass(cls):
        db.init()
        plugins.load_all()
        import sys

        cls.mod = sys.modules["newsroom_plugin_mpec"]

    def _fetch(self, page=None, **config):
        captured = page if page is not None else self.PAGE
        original = self.mod.sdk.http_get
        self.mod.sdk.http_get = lambda *a, **k: captured
        try:
            return self.mod.fetch(config)
        finally:
            self.mod.sdk.http_get = original

    def test_reads_id_subject_and_issue_date(self):
        items = self._fetch(skip_daily_orbit_update=False)
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first["title"], "MPEC 2026-Q41: 2026 QF")
        self.assertEqual(first["link"], "https://www.minorplanetcenter.net/mpec/K26/K26Q41.html")
        self.assertEqual(first["guid"], "mpec:2026-Q41")
        self.assertEqual(first["author"], "IAU Minor Planet Center")
        import datetime

        issued = datetime.datetime.fromtimestamp(first["published_at"], datetime.timezone.utc)
        self.assertEqual((issued.year, issued.month, issued.day), (2026, 8, 19))

    def test_daily_orbit_updates_are_skipped_by_default(self):
        subjects = [i["summary"] for i in self._fetch()]
        self.assertNotIn("DAILY ORBIT UPDATE (2026 August 19)", subjects)
        self.assertEqual(len(subjects), 2)

    def test_include_filter_selects_comets(self):
        items = self._fetch(include="COMET")
        self.assertEqual(len(items), 1)
        self.assertIn("PANSTARRS", items[0]["title"])

    def test_logo_image_can_be_turned_off(self):
        self.assertTrue(all(i["image"] for i in self._fetch()))
        self.assertTrue(all(i["image"] is None for i in self._fetch(use_logo_image=False)))

    def test_limit_is_respected(self):
        self.assertEqual(len(self._fetch(limit=1, skip_daily_orbit_update=False)), 1)

    def test_layout_change_is_reported(self):
        with self.assertRaises(sdk.PluginError) as ctx:
            self._fetch(page="<html><body>nothing here</body></html>")
        self.assertIn("layout may have changed", str(ctx.exception))


class CbetPluginTest(unittest.TestCase):
    """The Central Bureau telegram reader, against a captured page."""

    PAGE = """
    <ul>
    <li> CBET   5722 : 20260807 : <a href="/iau/cbet/005700/CBET005722.txt">COMET 220P/McNAUGHT</a>
    <li> CBET   5719 : 20260728 : <a href="/iau/cbet/005700/CBET005719.txt">(39249) 2000 YR_88</a>
    <li> CBET   5718 : 20260718 : <a href="/iau/cbet/005700/CBET005718.txt">COMET 161P/HARTLEY-IRAS</a>
    </ul>
    """

    @classmethod
    def setUpClass(cls):
        db.init()
        plugins.load_all()
        import sys

        cls.mod = sys.modules["newsroom_plugin_cbet"]

    def _fetch(self, page=None, **config):
        captured = page if page is not None else self.PAGE
        original = self.mod.sdk.http_get
        self.mod.sdk.http_get = lambda *a, **k: captured
        try:
            return self.mod.fetch(config)
        finally:
            self.mod.sdk.http_get = original

    def test_reads_number_subject_and_date(self):
        items = self._fetch()
        self.assertEqual(len(items), 3)
        first = items[0]
        self.assertEqual(first["title"], "CBET 5722: COMET 220P/McNAUGHT")
        self.assertEqual(first["guid"], "cbet:5722")
        self.assertTrue(first["link"].endswith("/iau/cbet/005700/CBET005722.txt"))
        import datetime

        issued = datetime.datetime.fromtimestamp(first["published_at"], datetime.timezone.utc)
        self.assertEqual((issued.year, issued.month, issued.day), (2026, 8, 7))

    def test_include_filter(self):
        items = self._fetch(include="COMET")
        self.assertEqual(len(items), 2)
        self.assertTrue(all("COMET" in i["title"] for i in items))

    def test_limit_and_logo_toggle(self):
        self.assertEqual(len(self._fetch(limit=1)), 1)
        self.assertTrue(all(i["image"] is None for i in self._fetch(use_logo_image=False)))

    def test_layout_change_is_reported(self):
        with self.assertRaises(sdk.PluginError) as ctx:
            self._fetch(page="<html>nothing</html>")
        self.assertIn("layout may have changed", str(ctx.exception))

    def test_default_url_is_http_because_https_does_not_answer(self):
        default = self.mod.PLUGIN["config_spec"][0]["default"]
        self.assertTrue(default.startswith("http://www.cbat.eps.harvard.edu"))


class AfterIdFilterTest(unittest.TestCase):
    """The `after_id` filter a notifier polls with."""

    def test_filter_selects_only_newer_rows(self):
        import time

        db.init()
        cur = db.execute(
            "INSERT INTO sources (name, plugin, config, enabled, created_at) VALUES (?,?,?,1,?)",
            (f"notify-{time.time()}", "rss", "{}", time.time()),
        )
        source_id = cur.lastrowid
        ids = []
        for n in range(3):
            link = f"https://e.pl/notify-{time.time()}-{n}"
            db.insert_news(source_id, "S", {"title": f"T{n}", "link": link, "guid": link})
            ids.append(db.query_one("SELECT id FROM news WHERE link = ?", (link,))["id"])

        newer = db.query("SELECT id FROM news WHERE source_id = ? AND id > ?",
                         (source_id, ids[0]))
        self.assertEqual([r["id"] for r in newer], ids[1:],
                         "only rows stored after the marker are returned")


class BookmarkTest(unittest.TestCase):
    """Bookmarks and their categories, straight against the storage layer."""

    def setUp(self):
        db.init()
        db.execute("DELETE FROM bookmarks")
        db.execute("DELETE FROM bookmark_categories")

    def _news(self, title="Artykuł", link="https://e.pl/1"):
        import time

        cur = db.execute(
            "INSERT INTO sources (name, plugin, config, enabled, created_at) VALUES (?,?,?,1,?)",
            (f"S{time.time()}", "rss", "{}", time.time()),
        )
        source_id = cur.lastrowid
        db.insert_news(source_id, "Testowe źródło", {
            "title": title, "link": link, "guid": link,
            "image": "https://e.pl/i.jpg", "published_at": time.time(),
            "summary": "opis", "author": "Autor",
        })
        return source_id, db.query_one("SELECT * FROM news WHERE link = ?", (link,))

    def test_bookmark_keeps_its_own_copy_of_the_item(self):
        """News is pruned and sources are deleted; a saved item must survive."""
        import time

        source_id, item = self._news(link="https://e.pl/keepme")
        db.execute(
            """INSERT INTO bookmarks (category_id, news_id, title, link, image,
                   published_at, summary, author, source_name, note, created_at)
               VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (item["id"], item["title"], item["link"], item["image"],
             item["published_at"], item["summary"], item["author"],
             item["source_name"], time.time()),
        )
        db.execute("DELETE FROM sources WHERE id = ?", (source_id,))  # cascades to news
        self.assertIsNone(db.query_one("SELECT 1 FROM news WHERE link = ?", ("https://e.pl/keepme",)))

        saved = db.query_one("SELECT * FROM bookmarks WHERE link = ?", ("https://e.pl/keepme",))
        self.assertIsNotNone(saved, "the bookmark outlives the news it came from")
        self.assertEqual(saved["title"], "Artykuł")
        self.assertEqual(saved["source_name"], "Testowe źródło")

    def test_deleting_a_category_keeps_its_bookmarks(self):
        import time

        cur = db.execute(
            "INSERT INTO bookmark_categories (name, color, created_at) VALUES (?,?,?)",
            ("Do przeczytania", None, time.time()),
        )
        category_id = cur.lastrowid
        db.execute(
            """INSERT INTO bookmarks (category_id, title, link, created_at)
               VALUES (?, ?, ?, ?)""",
            (category_id, "T", "https://e.pl/2", time.time()),
        )
        db.execute("DELETE FROM bookmark_categories WHERE id = ?", (category_id,))
        row = db.query_one("SELECT * FROM bookmarks WHERE link = ?", ("https://e.pl/2",))
        self.assertIsNotNone(row, "bookmarks survive their category")
        self.assertIsNone(row["category_id"], "and become uncategorised")

    def test_one_bookmark_per_link(self):
        import sqlite3
        import time

        for _ in range(1):
            db.execute("INSERT INTO bookmarks (title, link, created_at) VALUES (?,?,?)",
                       ("T", "https://e.pl/3", time.time()))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO bookmarks (title, link, created_at) VALUES (?,?,?)",
                       ("T again", "https://e.pl/3", time.time()))


class ExportTest(unittest.TestCase):
    """Page export: caching, validation and the sweep."""

    def setUp(self):
        from newsroom import export

        self.export = export
        for f in config.EXPORT_DIR.glob("*.*"):
            f.unlink()

    def test_only_http_urls_and_known_formats(self):
        with self.assertRaises(self.export.ExportError):
            self.export.render("file:///etc/passwd", "pdf")
        with self.assertRaises(self.export.ExportError):
            self.export.render("https://e.pl/a", "png")

    def test_cache_path_is_stable_and_format_specific(self):
        pdf = self.export._cache_path("https://e.pl/a", "pdf")
        jpg = self.export._cache_path("https://e.pl/a", "jpg")
        self.assertEqual(pdf, self.export._cache_path("https://e.pl/a", "pdf"))
        self.assertNotEqual(pdf, jpg)
        self.assertNotEqual(pdf, self.export._cache_path("https://e.pl/b", "pdf"))

    def test_sweep_drops_the_oldest_first(self):
        import os
        import time

        now = time.time()
        for index, name in enumerate(("old.pdf", "mid.pdf", "new.pdf")):
            path = config.EXPORT_DIR / name
            path.write_bytes(b"x" * 400_000)
            os.utime(path, (now + index, now + index))

        original = config.EXPORT_CACHE_MB
        config.EXPORT_CACHE_MB = 1          # 1 MB cap against 1.2 MB of files
        try:
            self.assertEqual(self.export.sweep_cache(), 1)
        finally:
            config.EXPORT_CACHE_MB = original
        self.assertFalse((config.EXPORT_DIR / "old.pdf").exists())
        self.assertTrue((config.EXPORT_DIR / "new.pdf").exists())


class SpaceweatherPluginTest(unittest.TestCase):
    """Spaceweather.com: stories split out of one long daily page."""

    PAGE = """
    <p><a href="https://ads.example/x"><img src="nublokr/bannerlet3.jpg"></a>
    <p><font color="#FF0000"><strong>POTENTIALLY DANGEROUS SUNSPOT:</strong></font>
       Sunspot 4513 has a delta-class field. See the
       <a href="images2026/20aug26/suvi304_movie.gif">M8-class flare</a>.
    <p><strong>IT'S RAINING PLASMA:</strong>
       Amateur astronomers are watching a rainstorm.
       <img src="images2026/22aug26/plasma.jpg">
    <p><strong>SHORT:</strong> too short a headline is still a headline.
    """

    @classmethod
    def setUpClass(cls):
        db.init()
        plugins.load_all()
        import sys

        cls.mod = sys.modules["newsroom_plugin_spaceweather"]

    def _fetch(self, page=None, **config):
        captured = page if page is not None else self.PAGE
        original = self.mod.sdk.http_get
        self.mod.sdk.http_get = lambda *a, **k: captured
        try:
            return self.mod.fetch(config)
        finally:
            self.mod.sdk.http_get = original

    def test_each_headline_becomes_a_story(self):
        items = self._fetch()
        titles = [i["title"] for i in items]
        self.assertIn("Potentially Dangerous Sunspot", titles)
        self.assertIn("It's Raining Plasma", titles, "apostrophes must not be mangled")

    def test_story_picture_is_found_inline_or_linked(self):
        by_title = {i["title"]: i for i in self._fetch()}
        # this story only links its image
        self.assertTrue(
            by_title["Potentially Dangerous Sunspot"]["image"].endswith("suvi304_movie.gif")
        )
        # this one inlines it
        self.assertTrue(by_title["It's Raining Plasma"]["image"].endswith("plasma.jpg"))

    def test_house_ads_are_not_used_as_story_images(self):
        for item in self._fetch():
            self.assertNotIn("nublokr", item["image"] or "")
            self.assertNotIn("bannerlet", item["image"] or "")

    def test_items_link_to_the_dated_archive_page(self):
        item = self._fetch()[0]
        self.assertIn("archive.php?view=1", item["link"])
        self.assertIn("year=", item["link"])

    def test_guid_is_stable_for_the_same_story_on_the_same_day(self):
        first = {i["guid"] for i in self._fetch()}
        second = {i["guid"] for i in self._fetch()}
        self.assertEqual(first, second, "re-reading must not create new items")

    def test_include_filter(self):
        items = self._fetch(include="PLASMA")
        self.assertEqual(len(items), 1)

    def test_layout_change_is_reported(self):
        with self.assertRaises(sdk.PluginError) as ctx:
            self._fetch(page="<html><body>nothing to see</body></html>")
        self.assertIn("layout may have changed", str(ctx.exception))


class _ImapStub(threading.Thread):
    """Enough of IMAP4 to exercise the mailbox plugin for real."""

    daemon = True
    MESSAGES = [
        b"From: Astronomy Weekly <news@astro.example>\r\n"
        b"Subject: =?UTF-8?B?VHlnb2RuaW93eSBwcnplZ2zEhWQ=?=\r\n"
        b"Date: Fri, 21 Aug 2026 09:30:00 +0200\r\n"
        b"Message-ID: <abc123@astro.example>\r\n"
        b"Content-Type: text/html; charset=utf-8\r\n\r\n"
        b'<html><body><a href="https://astro.example/unsubscribe?u=1">Unsubscribe</a>'
        b'<img src="https://astro.example/logo.png">'
        b'<a href="https://astro.example/2026/08/eclipse-report/">Read it</a>'
        b'<img src="https://astro.example/img/eclipse.jpg"></body></html>',
        b"From: Plain Sender <plain@example.org>\r\n"
        b"Subject: No links in here\r\n"
        b"Date: Thu, 20 Aug 2026 11:00:00 +0000\r\n"
        b"Message-ID: <plain-1@example.org>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Just a note with no links at all.",
    ]

    def __init__(self):
        super().__init__()
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.fetch_commands = []
        self.selected_readonly = None

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        stream = conn.makefile("rwb")
        stream.write(b"* OK stub ready\r\n")
        stream.flush()
        while True:
            line = stream.readline()
            if not line:
                return
            parts = line.decode(errors="replace").strip().split(" ")
            tag, command = parts[0], (parts[1].upper() if len(parts) > 1 else "")
            if command == "CAPABILITY":
                stream.write(b"* CAPABILITY IMAP4rev1\r\n")
                stream.write(f"{tag} OK done\r\n".encode())
            elif command == "LOGIN":
                ok = len(parts) > 3 and parts[3].strip('"') == "correct-horse"
                stream.write(f"{tag} {'OK logged in' if ok else 'NO invalid credentials'}\r\n".encode())
            elif command in ("SELECT", "EXAMINE"):
                self.selected_readonly = command == "EXAMINE"
                access = "READ-ONLY" if self.selected_readonly else "READ-WRITE"
                stream.write(f"* {len(self.MESSAGES)} EXISTS\r\n".encode())
                stream.write(f"{tag} OK [{access}] done\r\n".encode())
            elif command == "SEARCH":
                ids = " ".join(str(i + 1) for i in range(len(self.MESSAGES)))
                stream.write(f"* SEARCH {ids}\r\n".encode())
                stream.write(f"{tag} OK done\r\n".encode())
            elif command == "FETCH":
                self.fetch_commands.append(line.decode(errors="replace").strip())
                body = self.MESSAGES[int(parts[2]) - 1]
                stream.write(f"* {parts[2]} FETCH (BODY[] {{{len(body)}}}\r\n".encode())
                stream.write(body)
                stream.write(b")\r\n")
                stream.write(f"{tag} OK done\r\n".encode())
            elif command == "CLOSE":
                stream.write(f"{tag} OK closed\r\n".encode())
            elif command == "LOGOUT":
                stream.write(b"* BYE\r\n")
                stream.write(f"{tag} OK bye\r\n".encode())
                stream.flush()
                return
            else:
                stream.write(f"{tag} OK\r\n".encode())
            stream.flush()


class _Pop3Stub(_ImapStub):
    """The same messages, over POP3."""

    def _serve(self, conn):
        stream = conn.makefile("rwb")
        stream.write(b"+OK stub POP3 ready\r\n")
        stream.flush()
        while True:
            line = stream.readline()
            if not line:
                return
            parts = line.decode(errors="replace").strip().split(" ")
            command = parts[0].upper()
            if command == "USER":
                stream.write(b"+OK\r\n")
            elif command == "PASS":
                ok = len(parts) > 1 and parts[1] == "correct-horse"
                stream.write(b"+OK\r\n" if ok else b"-ERR invalid credentials\r\n")
            elif command == "LIST":
                stream.write(f"+OK {len(self.MESSAGES)} messages\r\n".encode())
                for index, body in enumerate(self.MESSAGES, start=1):
                    stream.write(f"{index} {len(body)}\r\n".encode())
                stream.write(b".\r\n")
            elif command == "RETR":
                body = self.MESSAGES[int(parts[1]) - 1]
                stream.write(f"+OK {len(body)} octets\r\n".encode())
                stream.write(body.replace(b"\n", b"\r\n").replace(b"\r\r", b"\r"))
                stream.write(b"\r\n.\r\n")
            elif command == "QUIT":
                stream.write(b"+OK bye\r\n")
                stream.flush()
                return
            else:
                stream.write(b"+OK\r\n")
            stream.flush()


class MailboxPluginTest(unittest.TestCase):
    """The IMAP/POP3 source, against stub servers speaking the real protocols."""

    @classmethod
    def setUpClass(cls):
        db.init()
        plugins.load_all()
        import sys

        cls.mod = sys.modules["newsroom_plugin_mailbox"]

    def _config(self, stub, **overrides):
        return dict(
            {"protocol": "imap", "host": "127.0.0.1", "port": stub.port, "ssl": False,
             "username": "me@example.org", "password": "correct-horse",
             "folder": "INBOX", "limit": 10},
            **overrides,
        )

    def test_imap_message_becomes_an_item(self):
        stub = _ImapStub()
        stub.start()
        items = self.mod.fetch(self._config(stub))
        self.assertEqual(len(items), 1, "the message with no link is skipped")
        item = items[0]
        self.assertEqual(item["title"], "Tygodniowy przegląd", "MIME header decoded")
        self.assertEqual(item["link"], "https://astro.example/2026/08/eclipse-report/",
                         "the unsubscribe link is not the article")
        self.assertEqual(item["image"], "https://astro.example/img/eclipse.jpg",
                         "the sender's logo is not the article image")
        self.assertEqual(item["guid"], "abc123@astro.example")
        self.assertIsNotNone(item["published_at"])

    def test_pop3_reads_the_same_mailbox(self):
        stub = _Pop3Stub()
        stub.start()
        items = self.mod.fetch(self._config(stub, protocol="pop3"))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Tygodniowy przegląd")

    def test_fallback_link_keeps_messages_without_links(self):
        stub = _ImapStub()
        stub.start()
        items = self.mod.fetch(
            self._config(stub, fallback_link="https://mail.example.org/")
        )
        self.assertEqual(len(items), 2)

    def test_messages_are_not_marked_as_read_by_default(self):
        stub = _ImapStub()
        stub.start()
        self.mod.fetch(self._config(stub))
        self.assertTrue(all("BODY.PEEK" in c for c in stub.fetch_commands),
                        "reading must not change message flags")
        self.assertTrue(stub.selected_readonly, "the folder is opened read-only")

        stub2 = _ImapStub()
        stub2.start()
        self.mod.fetch(self._config(stub2, mark_seen=True))
        self.assertTrue(all("BODY[" in c and "PEEK" not in c for c in stub2.fetch_commands))

    def test_a_rude_server_does_not_stall_the_teardown(self):
        """Some servers drop the connection after CLOSE; logout must not hang."""
        import time

        class RudeStub(_ImapStub):
            def _serve(self, conn):
                stream = conn.makefile("rwb")
                stream.write(b"* OK stub ready\r\n")
                stream.flush()
                while True:
                    line = stream.readline()
                    if not line:
                        return
                    parts = line.decode(errors="replace").strip().split(" ")
                    tag, command = parts[0], (parts[1].upper() if len(parts) > 1 else "")
                    if command == "CAPABILITY":
                        stream.write(b"* CAPABILITY IMAP4rev1\r\n")
                        stream.write(f"{tag} OK done\r\n".encode())
                    elif command == "LOGIN":
                        stream.write(f"{tag} OK logged in\r\n".encode())
                    elif command in ("SELECT", "EXAMINE"):
                        stream.write(b"* 0 EXISTS\r\n")
                        stream.write(f"{tag} OK [READ-ONLY] done\r\n".encode())
                    elif command == "SEARCH":
                        stream.write(b"* SEARCH\r\n")
                        stream.write(f"{tag} OK done\r\n".encode())
                    elif command == "CLOSE":
                        return          # hang up without answering
                    else:
                        stream.write(f"{tag} OK\r\n".encode())
                    stream.flush()

        stub = RudeStub()
        stub.start()
        started = time.time()
        self.mod.fetch(self._config(stub, fallback_link="https://mail.example.org/"))
        self.assertLess(time.time() - started, 15,
                        "teardown must not wait out the full socket timeout")

    def test_bad_login_is_reported_clearly(self):
        stub = _ImapStub()
        stub.start()
        with self.assertRaises(sdk.PluginError) as ctx:
            self.mod.fetch(self._config(stub, password="wrong"))
        self.assertIn("login refused", str(ctx.exception))

    def test_unreachable_server_is_reported(self):
        with self.assertRaises(sdk.PluginError) as ctx:
            self.mod.fetch(self._config(_ImapStub(), host="127.0.0.1", port=9))
        self.assertIn("cannot reach", str(ctx.exception))

    def test_password_can_come_from_the_environment(self):
        import os

        stub = _ImapStub()
        stub.start()
        os.environ["NEWSROOM_TEST_MAIL_PASSWORD"] = "correct-horse"
        try:
            config = self._config(stub, password="",
                                  password_env="NEWSROOM_TEST_MAIL_PASSWORD")
            self.assertEqual(len(self.mod.fetch(config)), 1)
        finally:
            del os.environ["NEWSROOM_TEST_MAIL_PASSWORD"]

    def test_missing_password_is_reported(self):
        stub = _ImapStub()
        stub.start()
        with self.assertRaises(sdk.PluginError) as ctx:
            self.mod.fetch(self._config(stub, password=""))
        self.assertIn("password", str(ctx.exception))

    def test_sender_and_subject_filters(self):
        stub = _ImapStub()
        stub.start()
        config = self._config(stub, fallback_link="https://mail.example.org/")
        self.assertEqual(len(self.mod.fetch(dict(config, from_filter="astro.example"))), 1)
        self.assertEqual(len(self.mod.fetch(dict(config, subject_filter="No links"))), 1)
        self.assertEqual(len(self.mod.fetch(dict(config, from_filter="nobody@"))), 0)

    def test_protocol_must_be_known(self):
        with self.assertRaises(sdk.PluginError):
            self.mod.fetch({"protocol": "smtp", "host": "x", "username": "u",
                            "password": "p"})


if __name__ == "__main__":
    unittest.main()
