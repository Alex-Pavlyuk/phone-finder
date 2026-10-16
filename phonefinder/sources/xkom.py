"""
Источник x-kom.pl.

Главный поставщик характеристик: в карточке товара лежит полная таблица
спецификации в виде JSON — «Pojemność baterii», «Zoom - kamera tylna»,
«Odporności», «Dodatkowe cechy aparatu», «Data premiery». Ни один другой
магазин не даёт столько полей сразу, поэтому x-kom опрашивается первым.

Выдача категории — обычный HTML: карточки помечены `data-name="productCard"`,
внутри — ссылка, название и цена в `aria-label` (её пишут для читалок экрана,
и это единственное место, где цена лежит одним числом, а не разбита на злотые
и гроши по разным элементам).
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from ..models import Offer, is_accessory, looks_used, parse_int, parse_title

log = logging.getLogger(__name__)

BASE = "https://www.x-kom.pl"
CATEGORY = "/g-6/c/1590-smartfony-i-telefony.html"
SPEC_CAPABLE = True

CARD_SPLIT = 'data-name="productCard"'
HREF_RE = re.compile(r'href="(/p/(\d+)-[^"?]+\.html)')
TITLE_RE = re.compile(r'title="([^"]{6,140})"')
PRICE_RE = re.compile(r'aria-label="Cena: ([^"]+)"')
UNAVAILABLE_RE = re.compile(r"(Powiadom o dostępności|Produkt niedostępny|Zapowiedź)")

# Таблица характеристик: {"key":"Pojemność baterii","id":"…","values":[{"name":"4000 mAh"}]}
SPEC_RE = re.compile(
    r'\{"key":"((?:[^"\\]|\\.)*)","id":"[0-9]+"'
    r'(?:,"description":"(?:[^"\\]|\\.)*")?,'
    r'"values":\[((?:\{"name":"(?:[^"\\]|\\.)*"\},?)+)\]\}'
)
SPEC_VALUE_RE = re.compile(r'\{"name":"((?:[^"\\]|\\.)*)"\}')
LD_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)


def build_url(cfg: dict, page: int = 1) -> str:
    """
    Ссылка на страницу выдачи.

    Фильтры магазина сознательно не используются: у x-kom они закодированы
    внутренними идентификаторами параметров, которые меняются вместе с
    вёрсткой. Отбор по цене и характеристикам всё равно делается локально,
    так что цена такого решения — несколько лишних страниц выдачи.
    """
    query = f"?page={page}" if page > 1 else ""
    return f"{BASE}{CATEGORY}{query}"


def _to_offer(chunk: str) -> Offer | None:
    href = HREF_RE.search(chunk)
    title = TITLE_RE.search(chunk)
    price = PRICE_RE.search(chunk)
    if not href or not title:
        return None

    parsed = parse_title(title.group(1))
    return Offer(
        source="xkom",
        ext_id=href.group(2),
        url=BASE + href.group(1),
        title=title.group(1),
        price=parse_int(price.group(1)) if price else None,
        image=_image(chunk),
        in_stock=not bool(UNAVAILABLE_RE.search(chunk)),
        used=looks_used(title.group(1)),
        brand=parsed["brand"],
        model=parsed["model"],
        model_key=parsed["key"],
        variant=parsed["variant"],
        ram_gb=parsed["ram_gb"],
        storage_gb=parsed["storage_gb"],
    )


def _image(chunk: str) -> str | None:
    match = re.search(r'src="(https://cdn\.x-kom\.pl/[^"]+)"', chunk)
    return match.group(1) if match else None


def fetch(fetcher, cfg: dict) -> list[Offer]:
    max_pages = cfg["sources"]["xkom"].get("max_pages", 6)
    out: list[Offer] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        html = fetcher.get(build_url(cfg, page), referer=f"{BASE}/")
        chunks = html.split(CARD_SPLIT)[1:]
        if not chunks:
            log.warning("x-kom: карточек не найдено на стр. %s", page)
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

    log.info("x-kom: собрано %s предложений", len(out))
    return out


def search(fetcher, query: str, limit: int = 4) -> list[Offer]:
    """
    Поиск товара по названию.

    Нужен для моделей, которых нет в наших страницах выдачи: категория x-kom
    — это сотни страниц, мы читаем первые шесть, а телефон с большой батареей
    (DOOGEE, CUBOT, POCO X8 Pro Max) обычно продаётся и там, просто глубже.
    Один запрос поиска возвращает карточку — и вместе с ней таблицу
    характеристик, без которой модель пришлось бы отбросить как непонятную.
    """
    html = fetcher.get(f"{BASE}/szukaj?q={quote(query)}", referer=f"{BASE}/")
    out: list[Offer] = []
    for chunk in html.split(CARD_SPLIT)[1:]:
        offer = _to_offer(chunk)
        if offer and not is_accessory(offer.title):
            out.append(offer)
        if len(out) >= limit:
            break
    return out


def spec_pairs(html: str) -> list[tuple[str, str]]:
    """Пары «подпись → значение» из таблицы характеристик карточки товара."""
    pairs: list[tuple[str, str]] = []
    for match in SPEC_RE.finditer(html):
        try:
            key = json.loads(f'"{match.group(1)}"')
            values = [json.loads(f'"{v}"') for v in SPEC_VALUE_RE.findall(match.group(2))]
        except json.JSONDecodeError:
            continue
        for value in values:
            pairs.append((key, value))
    return pairs


def product_price(html: str) -> int | None:
    """Актуальная цена из schema.org-разметки карточки."""
    for raw in LD_RE.findall(html):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if not isinstance(obj, dict) or obj.get("@type") != "Product":
                continue
            offers = obj.get("offers")
            offers = offers[0] if isinstance(offers, list) and offers else offers
            if isinstance(offers, dict):
                return parse_int(offers.get("price"))
    return None
