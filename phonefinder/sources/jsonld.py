"""
Разбор schema.org-разметки, общий для магазинов.

Morele и MediaMarkt раздают выдачу категории одинаково: `<script
type="application/ld+json">` с `ItemList`, внутри которого у каждого товара
есть название, ссылка, цена и картинка. Это разметка для поисковиков, то есть
единственная часть страницы, которую магазины не переделывают вместе с вёрсткой,
— поэтому разбирать выгоднее её, а не HTML.
"""
from __future__ import annotations

import json
import logging
import re

log = logging.getLogger(__name__)

LD_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.S)


def _blocks(html: str) -> list:
    out = []
    for raw in LD_RE.findall(html):
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            continue
        out.extend(data if isinstance(data, list) else [data])
    return out


def itemlist_products(html: str) -> list[dict]:
    """Товары из `ItemList` выдачи: name, url, price, image."""
    products: list[dict] = []
    for obj in _blocks(html):
        if not isinstance(obj, dict) or obj.get("@type") != "ItemList":
            continue
        for element in obj.get("itemListElement") or []:
            item = element.get("item") if isinstance(element, dict) else None
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            offers = item.get("offers")
            offers = offers[0] if isinstance(offers, list) and offers else offers
            image = item.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            products.append({
                "name": (item.get("name") or "").strip(),
                "url": item.get("url"),
                "price": (offers or {}).get("price") if isinstance(offers, dict) else None,
                "currency": (offers or {}).get("priceCurrency", "PLN")
                            if isinstance(offers, dict) else "PLN",
                "availability": (offers or {}).get("availability")
                                if isinstance(offers, dict) else None,
                "image": image,
                "sku": item.get("sku") or item.get("productID"),
            })
    return products


def product(html: str) -> dict | None:
    """Разметка `Product` со страницы товара — цена и наличие."""
    for obj in _blocks(html):
        if isinstance(obj, dict) and obj.get("@type") == "Product":
            return obj
    return None
