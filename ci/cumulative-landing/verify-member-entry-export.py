"""Verify generated public routes from the isolated fixture build."""

from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from xml.etree import ElementTree


BASE = "https://ilovedrrealty.com"
SPANISH_LANDING = "/es/bienes-raices-republica-dominicana/"
LISTING = "/listing/10000000-0000-0000-0000-000000000001/"
VISIBLE = {"can-americans-buy", "closing-costs-dr", "punta-cana-airbnb-roi"}
STAGED = {
    "best-places-to-retire", "can-canadians-buy", "confotur-explained", "dr-real-estate-safe",
    "dr-vs-miami", "las-terrenas", "living-cabarete-sosua", "living-punta-cana", "property-taxes-ipi",
}
SITEMAP_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}


class RouteMetadata(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.meta = {}
        self.schemas = []
        self.schema = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "link":
            self.links.append(values)
        if tag == "meta" and "property" in values:
            self.meta[values["property"]] = values.get("content")
        if tag == "script" and values.get("type") == "application/ld+json":
            self.schema = []

    def handle_data(self, data):
        if self.schema is not None:
            self.schema.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.schema is not None:
            self.schemas.append(json.loads("".join(self.schema)))
            self.schema = None

    def canonical(self, url):
        assert [link.get("href") for link in self.links if link.get("rel") == "canonical"] == [BASE + url]

    def alternate(self, lang, url):
        assert any(link.get("rel") == "alternate" and link.get("hreflang") == lang
                   and link.get("href") == BASE + url for link in self.links)


def article_inventory(output, prefix):
    return {page.parent.name for page in (output / prefix).glob("*/index.html")}


def inspect(output):
    def read(route):
        path = output / route.lstrip("/") / "index.html"
        assert path.is_file() and not path.is_symlink()
        return path.read_text(encoding="utf-8")

    expected = VISIBLE | STAGED
    assert article_inventory(output, "article") == expected
    assert article_inventory(output, "es/article") == expected
    assert "Property Services Directory" in read("/professionals/")
    assert "<title>Log In" in read("/login/")
    for route in ("/", "/search/", "/blog/", "/signup/", "/verify/", LISTING):
        read(route)

    spanish = RouteMetadata(read(SPANISH_LANDING))
    spanish.canonical(SPANISH_LANDING)
    spanish.alternate("es-DO", SPANISH_LANDING)
    spanish.alternate("en", "/buying/")
    assert spanish.meta.get("og:locale") == "es_DO"
    for slug in sorted(expected):
        en_page, es_page = f"/article/{slug}/", f"/es/article/{slug}/"
        for locale, canonical in (("en", en_page), ("es", es_page)):
            page = RouteMetadata(read(canonical))
            page.canonical(canonical)
            page.alternate("en", en_page)
            page.alternate("es", es_page)
            assert any(item.get("@type") == "Article" and item.get("inLanguage") == locale and item.get("headline")
                       for item in page.schemas)

    sitemap = ElementTree.parse(output / "sitemap.xml")
    entries = {}
    for item in sitemap.findall("s:url", SITEMAP_NS):
        url = item.findtext("s:loc", namespaces=SITEMAP_NS)
        assert url and url.startswith(BASE + "/") and url not in entries
        entries[url] = {(link.get("hreflang"), link.get("href")) for link in item.findall("x:link", SITEMAP_NS)}
    for route in ("/", "/search/", "/blog/", "/professionals/", SPANISH_LANDING, LISTING):
        assert BASE + route in entries
    en_urls = {BASE + f"/article/{slug}/" for slug in VISIBLE}
    es_urls = {BASE + f"/es/article/{slug}/" for slug in VISIBLE}
    assert {url for url in entries if url.startswith(BASE + "/article/")} == en_urls
    assert {url for url in entries if url.startswith(BASE + "/es/article/")} == es_urls
    for slug in VISIBLE:
        en_url, es_url = BASE + f"/article/{slug}/", BASE + f"/es/article/{slug}/"
        assert {("en", en_url), ("es", es_url)} <= entries[en_url]
        assert {("en", en_url), ("es", es_url)} <= entries[es_url]
    print("MEMBER_LANDING_ROUTES professionals=pass login=pass spanish_landing=pass english_articles=12 spanish_articles=12 "
          "sitemap_indexed_pairs=3 sitemap_staged_excluded_pairs=9")


if __name__ == "__main__":
    inspect(Path(sys.argv[1]))
