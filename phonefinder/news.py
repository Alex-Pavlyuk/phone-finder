"""
Ленты новостных сайтов про телефоны.

Нужны для вкладки «Скоро»: пока телефона нет в продаже, единственное, что про
него есть, — это анонсы, утечки и слухи. Читаем RSS, а не сами сайты: лента —
единственная часть новостного сайта со стабильной структурой, её не переделывают
вместе с вёрсткой, и она не закрыта антиботом (в отличие от, например,
PhoneArena, который на любой запрос отвечает проверкой Cloudflare).

Из статьи берётся немного: название модели в заголовке, признак того, что речь
о скором выходе, и характеристики, если их назвали. Всё остальное — работа
`upcoming.py`.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

ITEM_RE = re.compile(r"<(item|entry)[ >].*?</\1>", re.S | re.I)
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
LINK_RE = re.compile(r"<link[^>]*>(.*?)</link>|<link[^>]*href=\"(.*?)\"", re.S | re.I)
DESC_RE = re.compile(r"<(description|summary|content:encoded)[^>]*>(.*?)</\1>", re.S | re.I)
DATE_RE = re.compile(r"<(pubDate|published|updated)[^>]*>(.*?)</\1>", re.S | re.I)

# Слова, по которым видно, что статья про ещё не вышедший телефон.
UPCOMING_RE = re.compile(
    r"\b(launch|launches|launching|debut|unveil|announce[sd]?|announcement|"
    r"release date|coming soon|upcoming|leak|leaked|rumou?r|specs? sheet|"
    r"tipped|spotted|certified|premiera|zadebiutuje|debiut|zapowied[źz]|"
    r"pojawi si[ęe]|wyciek|przeciek|plotk)", re.IGNORECASE)

# Уже вышел — такие статьи для вкладки «Скоро» не нужны.
RELEASED_RE = re.compile(
    r"\b(review|hands-?on|now available|goes on sale|available now|w sprzeda[żz]y|"
    r"recenzja|test\b)", re.IGNORECASE)


@dataclass
class Article:
    source: str
    title: str
    url: str
    summary: str
    published: str | None = None

    @property
    def text(self) -> str:
        return f"{self.title} {self.summary}"


def _clean(raw: str) -> str:
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", raw or "", flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def parse_feed(xml: str, source: str, limit: int = 40) -> list[Article]:
    """RSS или Atom -> статьи. Разбор общий: различий ровно два тега."""
    out: list[Article] = []
    for match in ITEM_RE.finditer(xml):
        block = match.group(0)
        title = TITLE_RE.search(block)
        link = LINK_RE.search(block)
        desc = DESC_RE.search(block)
        date = DATE_RE.search(block)
        if not title:
            continue
        url = ""
        if link:
            url = _clean(link.group(1) or link.group(2) or "")
        out.append(Article(
            source=source,
            title=_clean(title.group(1)),
            url=url,
            summary=_clean(desc.group(2)) if desc else "",
            published=_clean(date.group(2)) if date else None,
        ))
        if len(out) >= limit:
            break
    return out


def fetch(fetcher, feeds: dict[str, str], limit: int = 40) -> list[Article]:
    """Читает все ленты. Падение одной не должно ронять остальные."""
    articles: list[Article] = []
    for source, url in feeds.items():
        try:
            xml = fetcher.get(url)
        except Exception as exc:
            log.warning("новости %s: лента недоступна — %s", source, exc)
            continue
        items = parse_feed(xml, source, limit)
        if not items:
            log.warning("новости %s: в ленте нет записей", source)
        articles.extend(items)
    log.info("новости: прочитано статей %s из %s лент", len(articles), len(feeds))
    return articles


def looks_upcoming(article: Article) -> bool:
    """Статья про то, что ещё выйдет, а не про то, что уже продаётся."""
    if RELEASED_RE.search(article.title):
        return False
    return bool(UPCOMING_RE.search(article.text))
