"""
Источник komputronik.pl.

Магазин на Nuxt: и выдача, и карточка приходят отрисованными, но данные в
состоянии клиента лежат «расплющенными» (значения по индексам в общем массиве),
поэтому разбирать их не стоит. Проще взять то, что и так отрисовано:
плитки выдачи помечены `data-name="listingTile"`, цена — `data-price-type="final"`,
а таблица характеристик в карточке читается из текста (см. specs.pairs_from_text)
— подписи там ровно те же польские, что и в остальных магазинах.
"""
from __future__ import annotations

import logging
import re

from ..models import Offer, is_accessory, looks_used, parse_int, parse_title

log = logging.getLogger(__name__)

BASE = "https://www.komputronik.pl"
CATEGORY = "/category/1596/telefony.html"
SPEC_CAPABLE = True

TILE_SPLIT = 'data-name="listingTile"'
HREF_RE = re.compile(r'href="(/product/(\d+)/[^"?]+\.html)"')
TITLE_RE = re.compile(r'title="([^"]{6,140})"')
PRICE_RE = re.compile(r'data-price-type="final"[^>]*>\s*([\d\s ]{3,10})\s*z[łl]')
IMAGE_RE = re.compile(r'src="(https://static\.komputronik\.pl/product-picture/[^"]+)"')
AVAILABLE_RE = re.compile(r'data-name="statusAvailable"')


def build_url(cfg: dict, page: int = 1) -> str:
    return f"{BASE}{CATEGORY}" + (f"?p={page}" if page > 1 else "")


def _to_offer(chunk: str) -> Offer | None:
    href = HREF_RE.search(chunk)
    title = TITLE_RE.search(chunk)
    if not href or not title:
        return None

    price = PRICE_RE.search(chunk)
    image = IMAGE_RE.search(chunk)
    parsed = parse_title(title.group(1))
    return Offer(
        source="komputronik",
        ext_id=href.group(2),
        url=BASE + href.group(1),
        title=title.group(1),
        price=parse_int(price.group(1)) if price else None,
        image=image.group(1) if image else None,
        in_stock=bool(AVAILABLE_RE.search(chunk)),
        used=looks_used(title.group(1)),
        brand=parsed["brand"],
        model=parsed["model"],
        model_key=parsed["key"],
        variant=parsed["variant"],
        ram_gb=parsed["ram_gb"],
        storage_gb=parsed["storage_gb"],
    )


def fetch(fetcher, cfg: dict) -> list[Offer]:
    max_pages = cfg["sources"]["komputronik"].get("max_pages", 6)
    out: list[Offer] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        html = fetcher.get(build_url(cfg, page), referer=f"{BASE}/")
        chunks = html.split(TILE_SPLIT)[1:]
        if not chunks:
            log.warning("komputronik: плиток не найдено на стр. %s", page)
            break

        fresh = 0
        for chunk in chunks:
            offer = _to_offer(chunk)
            if offer is None or offer.key in seen or is_accessory(offer.title):
                continue
            seen.add(offer.key)
            out.append(offer)
            fresh += 1
        if fresh == 0:
            break

    log.info("komputronik: собрано %s предложений", len(out))
    return out


def spec_pairs(html: str) -> list[tuple[str, str]]:
    """Размеченных пар у Komputronik нет — характеристики читаются из текста."""
    return []
