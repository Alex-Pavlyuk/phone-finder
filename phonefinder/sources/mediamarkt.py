"""
Источник mediamarkt.pl.

Выдача — тот же schema.org `ItemList` (см. jsonld). Характеристики в карточке
товара лежат в состоянии GraphQL-клиента: объекты вида
`{"name":"Stopień ochrony przed wnikaniem","type":null,"unit":null,"value":"IP68"}`.
Разбирается регулярным выражением по этой форме — полностью читать состояние
Apollo смысла нет, нам нужны только пары «подпись → значение».
"""
from __future__ import annotations

import logging
import re

from ..models import Offer, is_accessory, looks_used, parse_int, parse_title
from . import jsonld

log = logging.getLogger(__name__)

BASE = "https://mediamarkt.pl"
CATEGORY = "/telefony-i-smartfony/smartfony"
SPEC_CAPABLE = True

ID_RE = re.compile(r"-(\d{5,})\.html")
FEATURE_RE = re.compile(
    r'"name":"((?:[^"\\]|\\.){2,60})","type":(?:null|"[^"]*"),'
    r'"unit":(?:null|"[^"]*"),"value":"((?:[^"\\]|\\.){1,80})"'
)


def build_url(cfg: dict, page: int = 1) -> str:
    return f"{BASE}{CATEGORY}" + (f"?page={page}" if page > 1 else "")


def _to_offer(item: dict) -> Offer | None:
    url = item.get("url") or ""
    name = item.get("name") or ""
    ident = ID_RE.search(url)
    if not name or not ident:
        return None

    parsed = parse_title(name)
    return Offer(
        source="mediamarkt",
        ext_id=ident.group(1),
        url=url,
        title=name,
        price=parse_int(item.get("price")),
        currency=item.get("currency") or "PLN",
        image=item.get("image"),
        used=looks_used(name),
        brand=parsed["brand"],
        model=parsed["model"],
        model_key=parsed["key"],
        variant=parsed["variant"],
        ram_gb=parsed["ram_gb"],
        storage_gb=parsed["storage_gb"],
    )


def fetch(fetcher, cfg: dict) -> list[Offer]:
    max_pages = cfg["sources"]["mediamarkt"].get("max_pages", 6)
    out: list[Offer] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        html = fetcher.get(build_url(cfg, page), referer=f"{BASE}/")
        items = jsonld.itemlist_products(html)
        if not items:
            log.warning("mediamarkt: ItemList пуст на стр. %s", page)
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

    log.info("mediamarkt: собрано %s предложений", len(out))
    return out


def spec_pairs(html: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for name, value in FEATURE_RE.findall(html):
        pairs.append((name.replace("\\u002F", "/"), value.replace("\\u002F", "/")))
    return pairs


def product_price(html: str) -> int | None:
    data = jsonld.product(html) or {}
    offers = data.get("offers")
    offers = offers[0] if isinstance(offers, list) and offers else offers
    return parse_int(offers.get("price")) if isinstance(offers, dict) else None
