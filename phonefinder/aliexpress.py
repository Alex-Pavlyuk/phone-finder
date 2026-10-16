"""
Вкладка «AliExpress»: телефоны 2026 года прямо из Китая.

Отдельная вкладка, а не ещё один магазин, по двум причинам.

1. **Характеристики здесь со слов продавца.** У польского магазина таблица
   спецификации — это документ: за «IP68» и «7500 mAh» он отвечает. На
   AliExpress то же самое написано в заголовке товара («bateria 7500mAh,
   aparat 50MP»), и написать там можно что угодно. Смешивать такие данные
   с магазинными в одной выдаче нельзя — рейтинг стал бы фикцией.
2. **Год выпуска.** Запрос строится вокруг него («smartphone 2026»), и модель
   без упоминания года в выдачу не идёт — иначе половина результатов это
   прошлогодние аппараты с новой наклейкой.

Что можем — проверяем: если модель нашлась в кеше характеристик (то есть её
продаёт кто-то из польских магазинов), берутся именно магазинные данные, а не
обещания продавца. В карточке видно, откуда характеристики.
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import quote

from . import specs, upcoming
from .models import Phone, Spec, is_accessory, looks_used, parse_int

log = logging.getLogger(__name__)

BASE = "https://pl.aliexpress.com"
SEARCH = BASE + "/w/wholesale-{query}.html"
ITEMS_ANCHOR = '"itemList":{"content":'
KEY_PREFIX = "ali:"          # чтобы слухи и AliExpress не мешались в одной таблице
# Антибот AliExpress отвечает не кодом, а короткой страницей-заглушкой с
# редиректом на проверку. Отличается от выдачи размером и словом «punish».
PUNISH_MARKERS = ("punish", "captcha", "_bx-v", "slide to verify")


def _json_array(text: str, start: int) -> list:
    """
    Вырезает JSON-массив, начинающийся в позиции `start`.

    Регулярным выражением тут не обойтись: внутри массива вложенные скобки и
    строки со скобками, а сам он занимает полмегабайта. Поэтому просто считаем
    баланс, не забывая про строки и escape-последовательности.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:index + 1])
    raise ValueError("массив товаров не закрыт")


def is_blocked(html: str) -> bool:
    """Вместо выдачи пришла антибот-проверка."""
    return len(html) < 20000 and any(m in html for m in PUNISH_MARKERS)


def parse_items(html: str) -> list[dict]:
    """Товары из выдачи поиска."""
    anchor = html.find(ITEMS_ANCHOR)
    if anchor < 0:
        return []
    start = html.find("[", anchor)
    if start < 0:
        return []
    try:
        return _json_array(html, start)
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("aliexpress: выдача не разобралась — %s", exc)
        return []


def _price(item: dict) -> int | None:
    prices = item.get("prices") or {}
    for field in ("salePrice", "originalPrice"):
        block = prices.get(field) or {}
        value = block.get("minPrice")
        if value:
            return int(float(value))
    return None


def _to_phone(item: dict, year: int) -> Phone | None:
    """
    Товар AliExpress -> модель.

    Год обязателен в заголовке: на AliExpress «2026» — часть названия товара
    («NOWY smartfon … na rok 2026»), и это единственный доступный признак
    свежести. Без него в выдаче оказываются прошлогодние аппараты.
    """
    title = ((item.get("title") or {}).get("displayTitle") or "").strip()
    product_id = str(item.get("productId") or "")
    if not title or not product_id or str(year) not in title:
        return None
    if is_accessory(title) or looks_used(title):
        return None

    parsed = upcoming.model_from_headline(title)
    if parsed is None:
        # Марку опознать не удалось — а без неё это не модель, а строка текста.
        return None

    phone = Phone(key=parsed["key"], brand=parsed["brand"], name=parsed["model"],
                  spec=upcoming._spec_from_text(title))
    phone.spec.sources = ["aliexpress"]
    image = ((item.get("image") or {}).get("imgUrl") or "").lstrip("/")
    phone.articles = [{"source": "AliExpress", "title": title,
                       "url": f"{BASE}/item/{product_id}.html"}]
    phone.expected = f"{year} г."
    phone.ali_price = _price(item)
    phone.ali_image = f"https://{image}" if image else None
    phone.ali_title = title
    return phone


def collect(fetcher, cfg: dict, store) -> list[Phone]:
    """Ищет на AliExpress телефоны нужного года и отбирает по тем же критериям."""
    conf = cfg.get("aliexpress") or {}
    if not conf.get("enabled"):
        return []

    year = conf.get("year", 2026)
    found: dict[str, Phone] = {}
    for query in conf.get("queries") or [f"smartphone {year}"]:
        url = SEARCH.format(query=quote(query.replace(" ", "-")))
        try:
            html = fetcher.get(url, fresh=True)
        except Exception as exc:
            log.warning("aliexpress: запрос «%s» не удался — %s", query, exc)
            continue

        if is_blocked(html):
            log.warning("aliexpress: вместо выдачи антибот-проверка — источник "
                        "выключен до следующего прогона")
            break

        items = parse_items(html)
        log.info("aliexpress: «%s» — товаров %s", query, len(items))
        for item in items:
            phone = _to_phone(item, year)
            if phone is None:
                continue
            known = found.get(phone.key)
            if known is None:
                found[phone.key] = phone
            else:
                # Тот же телефон у другого продавца: оставляем цену пониже,
                # а обещания складываем — вдруг второй указал то, что первый нет.
                known.spec.merge(phone.spec, "aliexpress")
                if phone.ali_price and (not known.ali_price
                                        or phone.ali_price < known.ali_price):
                    known.ali_price = phone.ali_price
                    known.articles = phone.articles + known.articles

    out: list[Phone] = []
    for phone in found.values():
        # Если модель продаётся в польских магазинах, её характеристики уже
        # выяснены по таблицам — они и главные, а заголовок только дозаполняет.
        cached = store.get_spec(phone.key, conf.get("spec_recheck_days", 60))
        if cached is not None:
            cached.merge(phone.spec, "aliexpress")
            phone.spec = cached
        specs.apply_ai_rules(phone.spec, phone.title, cfg)
        phone.unknown = phone.spec.missing()
        phone.upcoming = True          # цены магазинов у карточки нет

        if not _passes(phone, cfg, conf):
            continue
        out.append(phone)
        store.save_upcoming(phone, key_prefix=KEY_PREFIX)

    out.sort(key=lambda p: (-(p.spec.battery_mah or 0), p.ali_price or 10 ** 9))
    log.info("aliexpress: моделей %s года по критериям — %s", year, len(out))
    return out


def _passes(phone: Phone, cfg: dict, conf: dict) -> bool:
    """
    Те же критерии, что и в магазинах, с двумя поправками.

    Батарея обязана быть названа: на AliExpress ёмкость пишут почти всегда, и
    товар без неё — либо аксессуар, либо совсем тёмная лошадка. А вот про OIS
    и зум продавцы молчат чаще, чем говорят, поэтому неизвестность здесь не
    отказ (в отличие от магазинной выдачи) — но противоречие критериям отказ.
    """
    cr = cfg["criteria"]
    spec = phone.spec
    battery = spec.battery_mah or spec.battery_min or 0
    if not battery or battery < (cr.get("battery_mah_min") or 0):
        return False
    if battery < (cr.get("battery_mah_floor") or 0):
        return False
    if upcoming._contradicts(spec, cfg):
        return False
    price = phone.ali_price
    if price and cr.get("price_max") and price > cr["price_max"] * (
            1 + (conf.get("price_overshoot_pct", 0) / 100)):
        return False
    if price and cr.get("price_min") and price < cr["price_min"]:
        return False
    return True
