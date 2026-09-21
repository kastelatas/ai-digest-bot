import unittest

from digest_bot.config import Source
from digest_bot.fetch import parse_feed, strip_html

RSS2_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Новая модель ИИ бьёт рекорды</title>
      <link>https://example.com/news/1</link>
      <guid>https://example.com/news/1</guid>
      <description><![CDATA[<p>Компания выпустила <b>новую модель</b>.</p>]]></description>
      <pubDate>Mon, 21 Sep 2026 10:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Без ссылки — должна быть пропущена</title>
      <description>Нет link, нет guid</description>
    </item>
  </channel>
</rss>
"""

ATOM_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom Feed</title>
  <entry>
    <title>Atom-новость про стартап</title>
    <link rel="alternate" href="https://example.com/atom/1"/>
    <id>urn:uuid:1234</id>
    <summary>Краткое описание atom-записи.</summary>
    <published>2026-09-21T10:00:00Z</published>
  </entry>
</feed>
"""

BROKEN_XML = "<rss><channel><item><title>Незакрытый тег"


class ParseFeedTests(unittest.TestCase):
    def test_rss2_parses_valid_items_and_skips_incomplete(self):
        source = Source(name="Test", url="https://example.com/rss", lang="ru", category="ai_news")
        items = parse_feed(RSS2_SAMPLE, source)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.title, "Новая модель ИИ бьёт рекорды")
        self.assertEqual(item.link, "https://example.com/news/1")
        self.assertIn("новую модель", item.summary)
        self.assertNotIn("<b>", item.summary)
        self.assertIsNotNone(item.published_at)
        self.assertEqual(item.source_name, "Test")

    def test_atom_parses_link_and_date(self):
        source = Source(name="AtomSrc", url="https://example.com/atom", lang="ru", category="ai_news")
        items = parse_feed(ATOM_SAMPLE, source)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item.link, "https://example.com/atom/1")
        self.assertEqual(item.guid, "urn:uuid:1234")
        self.assertIsNotNone(item.published_at)

    def test_broken_xml_returns_empty_list_not_raises(self):
        source = Source(name="Broken", url="https://example.com/broken", lang="ru", category="x")
        items = parse_feed(BROKEN_XML, source)
        self.assertEqual(items, [])

    def test_strip_html(self):
        self.assertEqual(strip_html("<p>Привет&nbsp;мир</p>"), "Привет мир")
        self.assertEqual(strip_html(None), "")
        self.assertEqual(strip_html("  много   пробелов  "), "много пробелов")


if __name__ == "__main__":
    unittest.main()
