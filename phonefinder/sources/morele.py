"""
Источник morele.net.

Выдача — schema.org `ItemList` (см. jsonld). Карточка товара отдаёт
характеристики как `PropertyValue`, и это единственный магазин, который прямо
пишет «Funkcje AI: Galaxy AI» — то есть подтверждает критерий, о котором
остальные молчат.

Ссылка на вторую страницу у Morele выглядит как
`/kategoria/smartfony-280/,,,,,,,,0,,,,/2/`: запятые — это пустые слоты
фильтров, и без них номер страницы не воспринимается.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import quote

from ..models import Offer, is_accessory, looks_used, parse_int, parse_title
from . import jsonld

log = logging.getLogger(__name__)

BASE = "https://www.morele.net"
CATEGORY = "/kategoria/smartfony-280"
SPEC_CAPABLE = True

ID_RE = re.compile(r"-(\d{5,})/?$")
PROPERTY_RE = re.compile(
    r'\{"@type":"PropertyValue","name":"((?:[^"\\]|\\.)*)","value":"((?:[^"\\]|\\.)*)"\}'
)


def build_url(cfg: dict, page: int = 1) -> str:
    if page <= 1:
        return f"{BASE}{CATEGORY}/"
    return f"{BASE}{CATEGORY}/,,,,,,,,0,,,,/{page}/"


def _to_offer(item: dict) -> Offer | None:
    url = item.get("url") or ""
    name = item.get("name") or ""
    ident = ID_RE.search(url.rstrip("/"))
    if not name or not ident:
        return None

    parsed = parse_title(name)
    available = (item.get("availability") or "").lower()
    return Offer(
        source="morele",
        ext_id=ident.group(1),
        url=url,
        title=name,
        price=parse_int(item.get("price")),
        currency=item.get("currency") or "PLN",
        image=item.get("image"),
        in_stock=None if not available else "instock" in available.replace("/", ""),
        used=looks_used(name),
        brand=parsed["brand"],
        model=parsed["model"],
        model_key=parsed["key"],
        variant=parsed["variant"],
        ram_gb=parsed["ram_gb"],
        storage_gb=parsed["storage_gb"],
    )


def fetch(fetcher, cfg: dict) -> list[Offer]:
    max_pages = cfg["sources"]["morele"].get("max_pages", 6)
    out: list[Offer] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        html = fetcher.get(build_url(cfg, page), referer=f"{BASE}/")
        items = jsonld.itemlist_products(html)
        if not items:
            log.warning("morele: ItemList пуст на стр. %s", page)
            break

        fresh = 0
        for item in items:
            offer = _to_offer(item)
            if offer is None or offer.key in seen or is_accessory(offer.title):
                continue
            seen.add(offer.key)
            out.append(offer)
            fresh += 1
        if fresh == 0:
            break

    log.info("morele: собрано %s предложений", len(out))
    return out


SEARCH_RESULT_RE = re.compile(
    r'data-link-href-param="(/[a-z0-9\-]+-(\d{5,})/)"\s*title="([^"]{6,140})"'
)


def search(fetcher, query: str, limit: int = 4) -> list[Offer]:
    """
    Поиск товара по названию.

    Нужен для того же, что и поиск x-kom, но покрывает другие марки: Oukitel,
    Doogee, AGM и прочие «кирпичи» с большой батареей x-kom не возит, а Morele
    возит. Выдача поиска — не та же разметка, что у категории (schema.org там
    нет), поэтому берётся пара «ссылка + title» из плитки.
    """
    html = fetcher.get(f"{BASE}/wyszukiwarka/?q={quote(query)}", referer=f"{BASE}/")
    out: list[Offer] = []
    seen: set[str] = set()
    for path, ident, title in SEARCH_RESULT_RE.findall(html):
        if ident in seen or is_accessory(title):
            continue
        seen.add(ident)
        offer = _to_offer({"url": BASE + path, "name": title, "price": None})
        if offer:
            out.append(offer)
        if len(out) >= limit:
            break
    return out


def spec_pairs(html: str) -> list[tuple[str, str]]:
    return [(name, value) for name, value in PROPERTY_RE.findall(html)]


def product_price(html: str) -> int | None:
    data = jsonld.product(html) or {}
    offers = data.get("offers")
    offers = offers[0] if isinstance(offers, list) and offers else offers
    return parse_int(offers.get("price")) if isinstance(offers, dict) else None
