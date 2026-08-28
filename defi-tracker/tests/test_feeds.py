from dft.sources.feeds import parse, strip_html


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>
<item><title>Aave &amp; Curve rally</title><link>https://x.com/a?utm_source=rss</link>
<description>&lt;p&gt;Some  text&lt;/p&gt;</description><pubDate>Tue, 25 Aug 2026 10:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Protocol upgrade shipped</title><link href="https://y.com/b"/>
<summary>hello</summary><published>2026-08-25T09:00:00Z</published></entry></feed>"""


def test_parse_rss_item():
    items = parse(RSS, "X")
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "Aave & Curve rally"
    assert item["url"] == "https://x.com/a?utm_source=rss"
    assert item["summary"] == "Some text"
    assert item["published_at"] == "2026-08-25T10:00:00+00:00"


def test_parse_atom_entry():
    items = parse(ATOM, "Y")
    assert len(items) == 1
    assert items[0]["title"] == "Protocol upgrade shipped"
    assert items[0]["url"] == "https://y.com/b"


def test_parse_invalid_xml_returns_empty():
    assert parse("not xml", "Z") == []


def test_parse_skips_items_missing_title_or_link():
    xml = """<rss><channel><item><description>no title or link</description></item></channel></rss>"""
    assert parse(xml, "Z") == []


def test_strip_html():
    assert strip_html("<p>Hello  <b>World</b></p>") == "Hello World"
    assert strip_html(None) == ""
