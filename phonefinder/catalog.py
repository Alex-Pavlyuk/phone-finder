"""
Сборка моделей из предложений и выяснение их характеристик.

Предложений за прогон — тысячи, моделей среди них — сотни, а карточку товара
надо открыть ровно один раз на модель: именно она стоит запрос к магазину.
Поэтому:

  * предложения группируются по ключу модели (см. models.parse_title);
  * характеристики кешируются в базе навсегда — телефон не меняет ёмкость
    батареи после релиза;
  * за один прогон открывается ограниченное число карточек, и первыми —
    те модели, которых мы раньше не видели. Так новинка попадает в отчёт
    в тот же прогон, в котором появилась в магазине, а разбор старого
    ассортимента растягивается на несколько прогонов.
"""
from __future__ import annotations

import logging

from . import specs, upcoming
from .http import Cancelled
from .models import Offer, Phone, Spec
from .sources import ALL, SPEC_ORDER, morele, xkom

log = logging.getLogger(__name__)


def group(offers: list[Offer]) -> list[Phone]:
    """Предложения -> модели. Ключ — «марка + модель» без памяти и цвета."""
    phones: dict[str, Phone] = {}
    for offer in offers:
        if not offer.model_key:
            continue
        phone = phones.get(offer.model_key)
        if phone is None:
            phone = Phone(key=offer.model_key, brand=offer.brand,
                          name=offer.model or offer.title)
            phones[offer.model_key] = phone
        phone.offers.append(offer)
        # Название модели берём самое короткое: длинные заголовки — это
        # заголовки Allegro с приписками продавца.
        if offer.model and len(offer.model) < len(phone.name):
            phone.name = offer.model
        if phone.brand is None and offer.brand:
            phone.brand = offer.brand
    return list(phones.values())


def _worth_specs(phone: Phone, cfg: dict) -> bool:
    """
    Стоит ли тратить запрос на выяснение характеристик.

    Отсекаем то, что уже не купить по цене и что продаётся только б/у:
    характеристики такой модели ничего не изменят, а запрос стоит столько же.
    """
    cr = cfg["criteria"]
    new_priced = [o.price for o in phone.new_offers if o.price]
    if not new_priced:
        return False
    cheapest = min(new_priced)
    if cr.get("price_max") and cheapest > cr["price_max"] * 1.25:
        return False
    if cr.get("price_min") and cheapest < cr["price_min"]:
        return False
    return True


def _spec_candidates(phone: Phone) -> list[Offer]:
    """Предложения, чью карточку имеет смысл открыть, в порядке полноты таблиц."""
    by_source: dict[str, Offer] = {}
    for offer in phone.offers:
        if offer.source in SPEC_ORDER and offer.source not in by_source:
            by_source[offer.source] = offer
    return [by_source[name] for name in SPEC_ORDER if name in by_source]


def _priority(phone: Phone, cfg: dict) -> tuple:
    """
    Очередь на выяснение характеристик. Лимит запросов за прогон конечен, и
    порядок решает, про что мы узнаем сегодня, а про что через три прогона.

    Первыми идут модели, которые уже прошли по батарее — по числу из таблицы
    или по фильтру Allegro (`battery_min_guaranteed`). Именно им осталось
    выяснить только камеру, то есть один запрос отделяет их от ответа «да» или
    «нет». Раньше первым критерием было число магазинов, и телефоны с большой
    батареей, которые продаются только на Allegro, оказывались в конце очереди
    — то есть ровно то, что искали, разбиралось последним.
    """
    required = cfg["criteria"].get("battery_mah_min") or 0
    guaranteed = max((o.battery_min_guaranteed or 0) for o in phone.offers) if phone.offers else 0
    known = phone.spec.battery_mah or 0
    qualifies = max(guaranteed, known) >= required if required else True
    return (
        bool(phone.spec.sources),   # ничего не знаем — вперёд
        0 if qualifies else 1,      # проходит по батарее — вперёд
        -len(phone.stores),         # и то, что продаётся в нескольких местах
    )


def _gsm_lookup(phone: Phone, fetcher, conf: dict) -> Spec | None:
    try:
        return upcoming.gsm_spec(fetcher, phone, conf.get("gsm_brand_pages", 3))
    except Cancelled:
        raise
    except Exception as exc:
        log.debug("gsmarena: «%s» не нашлась — %s", phone.title, exc)
        return None


def _find_by_name(phone: Phone, fetcher) -> Offer | None:
    """
    Ищет карточку модели по названию, когда её нет ни в одной нашей выдаче.

    Так выглядит типичная модель с большой батареей: на Allegro она есть (там
    фильтр по ёмкости и её видно сразу), а в наших шести страницах категории
    x-kom — нет, потому что категория это сотни страниц. Без поиска такая
    модель навсегда осталась бы «характеристики неизвестны», то есть
    отброшенной, — при том что магазин её продаёт и всё про неё пишет.
    """
    for module in (xkom, morele):
        try:
            found = module.search(fetcher, phone.title)
        except Cancelled:
            raise
        except Exception as exc:
            log.debug("поиск «%s» в %s не удался — %s", phone.title, module.BASE, exc)
            continue
        for offer in found:
            # Совпадение ключа обязательно: поиск охотно подсовывает соседние
            # модели, а чужие характеристики хуже отсутствующих.
            if offer.model_key == phone.key:
                return offer
    return None


def resolve(phones: list[Phone], store, fetcher, cfg: dict) -> dict:
    """
    Заполняет `phone.spec` — из кеша базы или загрузкой карточек товара.

    Возвращает счётчики прогона: сколько взято из кеша, сколько загружено,
    сколько моделей осталось без характеристик из-за лимита запросов.
    """
    conf = cfg.get("specs") or {}
    budget = conf.get("max_pages_per_run", 40)
    per_model = conf.get("max_pages_per_model", 2)
    recheck_days = conf.get("recheck_after_days", 60)
    lookups = conf.get("max_lookups_per_run", 12)
    retry_lookup_days = conf.get("retry_lookup_after_days", 14)

    stats = {"cached": 0, "fetched": 0, "deferred": 0, "failed": 0, "found": 0}
    pending: list[Phone] = []

    for phone in phones:
        cached = store.get_spec(phone.key, recheck_days)
        if cached is not None:
            phone.spec = cached
            stats["cached"] += 1
            # Долги добираем позже: если в кеше не хватает ключевого поля,
            # модель всё равно попадает в очередь на доуточнение.
            if not _critical_gaps(phone.spec) or not _worth_specs(phone, cfg):
                continue
        if _worth_specs(phone, cfg):
            pending.append(phone)

    pending.sort(key=lambda p: _priority(p, cfg))

    for phone in pending:
        if budget <= 0:
            stats["deferred"] += 1
            continue

        candidates = _spec_candidates(phone)
        if (not candidates and lookups > 0
                and not store.lookup_tried(phone.key, retry_lookup_days)):
            # Модель есть только там, где характеристик не дают (обычно
            # Allegro) — ищем её карточку по названию. Поиск идёт по двум
            # магазинам, поэтому и из лимита запросов берём столько же.
            budget -= 2
            lookups -= 1
            found = _find_by_name(phone, fetcher)
            if found is None:
                # Магазины не возят — остаётся GSMArena: у него есть всё, что
                # когда-либо выпускалось, включая «кирпичи» с Allegro.
                gsm = _gsm_lookup(phone, fetcher, conf)
                store.save_lookup(phone.key, gsm is not None)
                if gsm is None:
                    stats["failed"] += 1
                    continue
                phone.spec.merge(gsm, "gsmarena")
                store.save_spec(phone.key, phone.spec)
                stats["found"] += 1
                budget -= 1
                continue
            store.save_lookup(phone.key, True)
            phone.offers.append(found)
            candidates = [found]
            stats["found"] += 1

        used = 0
        for offer in candidates:
            if used >= per_model or budget <= 0 or not _critical_gaps(phone.spec):
                break
            module = ALL[offer.source]
            try:
                html = fetcher.get(offer.url, referer=module.BASE)
            except Exception as exc:
                log.debug("%s: карточка %s не открылась — %s", offer.source, offer.url, exc)
                stats["failed"] += 1
                budget -= 1
                used += 1
                continue
            budget -= 1
            used += 1
            stats["fetched"] += 1

            pairs = module.spec_pairs(html) if hasattr(module, "spec_pairs") else []
            fresh = specs.from_html(html, offer.source, pairs)
            phone.spec.merge(fresh, offer.source)

            price = getattr(module, "product_price", lambda _: None)(html)
            if price:
                # Цена в карточке точнее, чем в выдаче: в листинге у части
                # магазинов показана цена «с кодом» или до скидки.
                offer.price = price

        if phone.spec.sources:
            store.save_spec(phone.key, phone.spec)

    for phone in phones:
        # Модель могла побывать во вкладке «Скоро» до выхода в продажу —
        # тогда её характеристики уже выяснены по GSMArena, и терять их,
        # получив более бедную таблицу магазина, незачем. Магазинные значения
        # главнее (они про конкретный SKU), из «Скоро» берутся только пробелы.
        if _critical_gaps(phone.spec):
            earlier = store.spec_from_upcoming(phone.key)
            if earlier is not None:
                phone.spec.merge(earlier, "gsmarena")
                stats["from_upcoming"] = stats.get("from_upcoming", 0) + 1

        specs.apply_ai_rules(phone.spec, phone.title, cfg)
        _apply_guaranteed_battery(phone)
        phone.unknown = phone.spec.missing()

    log.info("характеристики: из кеша %s, загружено карточек %s, найдено поиском %s, "
             "дополнено из «Скоро» %s, не нашлось %s, отложено моделей %s",
             stats["cached"], stats["fetched"], stats["found"],
             stats.get("from_upcoming", 0), stats["failed"], stats["deferred"])
    return stats


def _apply_guaranteed_battery(phone: Phone) -> None:
    """
    Переносит на модель нижнюю границу ёмкости, гарантированную источником.

    Точное число из таблицы характеристик всегда важнее: его пишет магазин, а
    границу — фильтр, который опирается на параметры, заполненные продавцом.
    Поэтому граница применяется только там, где числа нет вовсе.
    """
    if phone.spec.battery_mah is not None:
        return
    guaranteed = max((o.battery_min_guaranteed or 0) for o in phone.offers) if phone.offers else 0
    if guaranteed:
        phone.spec.battery_min = guaranteed


def _critical_gaps(spec: Spec) -> list[str]:
    """Поля, без которых по модели нельзя принять решение."""
    return [gap for gap in spec.missing() if gap in ("battery", "ip", "ois", "zoom")]
