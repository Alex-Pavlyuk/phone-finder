"""
Источник allegro.pl.

Выдача лежит в JSON внутри `<script type="application/json">` под ключом
`__listing_StoreState`. Официальный REST API есть, но требует регистрации
приложения и OAuth-токена, а отдаёт для поиска то же самое — HTML-страница
проще и не требует от пользователя ключей.

Характеристик Allegro не даёт: в карточке выдачи только цвет, экран и память,
а параметры в объявлении заполняет продавец. Поэтому Allegro здесь — источник
*цен и наличия*, а характеристики выясняются по магазинам (см. specs).
"""
from __future__ import annotations

import json
import logging
import re
from urllib.parse import parse_qs, unquote, urlsplit

from ..models import Offer, is_accessory, looks_used, parse_int, parse_title

log = logging.getLogger(__name__)

BASE = "https://allegro.pl"
CATEGORY = "/kategoria/smartfony-i-telefony-komorkowe-165"
STATE_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(\{"__listing_StoreState".*?)</script>', re.S
)
SPEC_CAPABLE = False


def build_url(cfg: dict, page: int = 1) -> str:
    """
    Ссылка на выдачу категории.

    Главное здесь — `pojemnosc-akumulatora-od`: у Allegro есть собственный
    фильтр по ёмкости аккумулятора, и он применяется на его стороне. Без него
    страницы выдачи уходили на телефоны с 5000 мАч, а модели с большой батареей
    (их на Allegro много — DOOGEE, CUBOT, Ulefone, POCO X8 Pro Max) в первые
    страницы просто не попадали. С ним каждая страница — это ровно то, что
    проходит по батарее.
    """
    cr = cfg["criteria"]
    params = ["order=n"]                      # сначала недавно выставленные
    if cr.get("battery_mah_min"):
        params.append(f"pojemnosc-akumulatora-od={cr['battery_mah_min']}")
    if cr.get("exclude_used"):
        params.append("stan=nowe")
    if cr.get("price_min"):
        params.append(f"price_from={cr['price_min']}")
    if cr.get("price_max"):
        params.append(f"price_to={cr['price_max']}")
    if page > 1:
        params.append(f"p={page}")
    return f"{BASE}{CATEGORY}?" + "&".join(params)


def _real_url(url: str) -> str:
    """
    Разворачивает ссылку-счётчик спонсорских карточек.

    Реклама в выдаче ведёт на `/events/clicks?...&redirect=<url>`: такая ссылка
    живёт считанные часы и как ключ предложения бесполезна.
    """
    if "/events/clicks" not in url:
        return url
    target = parse_qs(urlsplit(url).query).get("redirect")
    if not target:
        return url
    return unquote(target[0]).split("?")[0]


def _to_offer(element: dict, battery_min: int | None = None) -> Offer | None:
    title = ((element.get("title") or {}).get("text") or "").strip()
    offer_id = str(element.get("offerId") or element.get("id") or "")
    price = parse_int(((element.get("price") or {}).get("mainPrice") or {}).get("amount"))
    if not title or not offer_id or not price:
        return None

    parsed = parse_title(title)
    seller = (element.get("seller") or {}).get("login")
    return Offer(
        source="allegro",
        ext_id=offer_id,
        url=_real_url(element.get("url") or f"{BASE}/oferta/{offer_id}"),
        title=title,
        price=price,
        currency=((element.get("price") or {}).get("mainPrice") or {}).get("currency", "PLN"),
        image=element.get("mainThumbnail"),
        seller=seller,
        in_stock=True,
        used=looks_used(title),
        # Ёмкость мы тут не знаем, но знаем нижнюю границу: выдача запрошена
        # с фильтром Allegro по батарее, и всё, что в ней есть, эту границу
        # проходит. Для модели, которой нет ни в одном магазине с таблицей
        # характеристик, это единственный источник факта про батарею.
        battery_min_guaranteed=battery_min,
        brand=parsed["brand"],
        model=parsed["model"],
        model_key=parsed["key"],
        variant=parsed["variant"],
        ram_gb=parsed["ram_gb"],
        storage_gb=parsed["storage_gb"],
    )


def fetch(fetcher, cfg: dict) -> list[Offer]:
    max_pages = cfg["sources"]["allegro"].get("max_pages", 6)
    out: list[Offer] = []
    seen: set[str] = set()
    skipped_accessories = 0

    for page in range(1, max_pages + 1):
        # fresh=True: Allegro отвечает 200 только на первый запрос в сессии
        # (см. Fetcher.get). Referer намеренно не ставим — лишний признак
        # автоматизации там, где и без него всё работает.
        html = fetcher.get(build_url(cfg, page), fresh=True)
        match = STATE_RE.search(html)
        if not match:
            log.warning("allegro: не найден __listing_StoreState на стр. %s", page)
            break
        try:
            state = json.loads(match.group(1))["__listing_StoreState"]
        except (json.JSONDecodeError, KeyError) as exc:
            log.warning("allegro: выдача не разобралась на стр. %s — %s", page, exc)
            break

        elements = (state.get("items") or {}).get("elements") or []
        products = [e for e in elements if e.get("type") == "product"]
        if not products:
            break

        fresh = 0
        for element in products:
            offer = _to_offer(element, cfg["criteria"].get("battery_mah_min") or None)
            if offer is None or offer.key in seen:
                continue
            if is_accessory(offer.title):
                skipped_accessories += 1
                continue
            seen.add(offer.key)
            out.append(offer)
            fresh += 1
        if fresh == 0:
            break

    log.info("allegro: собрано %s предложений (аксессуаров пропущено %s)",
             len(out), skipped_accessories)
    return out
