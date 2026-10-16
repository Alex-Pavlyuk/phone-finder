"""Один прогон: собрать предложения → свести в модели → выяснить характеристики
→ отфильтровать → оценить → отчёт и уведомления."""
from __future__ import annotations

import logging
import threading
from collections import Counter
from pathlib import Path

from . import aliexpress, catalog, filters, notify, report, scoring, upcoming
from .http import Cancelled, Fetcher
from .models import Offer, Phone
from .sources import ALL
from .store import Store

log = logging.getLogger(__name__)


def collect(cfg: dict, fetcher: Fetcher) -> list[Offer]:
    offers: list[Offer] = []
    for name, module in ALL.items():
        if not cfg["sources"].get(name, {}).get("enabled"):
            continue
        try:
            offers.extend(module.fetch(fetcher, cfg))
        except Cancelled:
            raise
        except Exception as exc:
            # Падение одного магазина не должно ронять весь прогон: остальные
            # четыре дадут и цены, и характеристики.
            log.error("%s: сбор не удался — %s", name, exc)
    return offers


def _funnel(phones: list, cfg: dict) -> dict:
    """
    Сколько моделей прошло по батарее и у скольких из них известна камера.

    Без этих двух чисел отчёт вводит в заблуждение: на Allegro телефонов
    с нужной батареей действительно много (его фильтр по ёмкости их и
    приносит), а в выдаче пусто — и непонятно, потеряли мы их или отбраковали.
    Ответ обычно второй: у таких моделей никто из магазинов не пишет про
    оптическую стабилизацию и зум.
    """
    required = cfg["criteria"].get("battery_mah_min") or 0
    battery_ok = [p for p in phones
                  if max(p.spec.battery_mah or 0, p.spec.battery_min or 0) >= required]
    camera_known = [p for p in battery_ok
                    if p.spec.ois is not None and p.spec.optical_zoom is not None]
    log.info("по батарее ≥%s мАч прошло моделей: %s, камера известна у %s",
             required, len(battery_ok), len(camera_known))
    return {"battery_ok": len(battery_ok), "camera_known": len(camera_known)}


def run(cfg: dict, data_dir: Path, stop: threading.Event | None = None) -> dict:
    """
    Один полный прогон.

    `stop` — отмена: взведённое событие прерывает прогон на ближайшем сетевом
    вызове через `Cancelled`. Всё, что успело записаться в базу, остаётся —
    транзакция закрывается в `finally`, так что прерванный прогон не портит
    ни базу, ни отчёт.
    """
    rt = cfg.get("runtime", {})
    fetcher = Fetcher(
        timeout=rt.get("timeout_sec", 40),
        retries=rt.get("retries", 3),
        delay=rt.get("request_delay_sec", 1.2),
        host_delay=rt.get("host_delay_sec") or {},
        stop=stop,
    )

    try:
        offers = collect(cfg, fetcher)
        phones = catalog.group(offers)
        log.info("собрано %s предложений, моделей %s", len(offers), len(phones))

        first_run = not (data_dir / "listings.db").exists()
        store = Store(data_dir / "listings.db")
        try:
            spec_stats = catalog.resolve(phones, store, fetcher, cfg)

            for phone in phones:
                filters.apply(phone, cfg)
                scoring.score(phone, cfg)

            keep = [p for p in phones if not p.reject_reasons]
            matched = [p for p in keep if not p.near_miss]
            log.info("подходит моделей: %s (и ещё %s почти)",
                     len(matched), len(keep) - len(matched))
            spec_stats.update(_funnel(phones, cfg))

            rejected = Counter(p.reject_reasons[0] for p in phones if p.reject_reasons)
            for reason, count in rejected.most_common(8):
                log.info("  отсеяно %4s — %s", count, reason)

            enriched: list[tuple] = []
            fresh = []
            for phone in keep:
                meta = store.upsert(phone)
                if meta["is_new"] or meta.get("newly_matched"):
                    fresh.append(phone)
                enriched.append((phone, meta))
            # Модели, не прошедшие фильтр, тоже сохраняем: иначе при каждом
            # прогоне они снова будут «новыми», и в тот прогон, когда магазин
            # наконец допишет им характеристики, они не отличатся от старых.
            for phone in phones:
                if phone.reject_reasons:
                    store.upsert(phone)

            # Телефоны, которых ещё нет в продаже: отдельная вкладка отчёта.
            # Сбой здесь не должен ронять прогон — магазины важнее слухов.
            try:
                soon = upcoming.collect(fetcher, cfg, store)
            except Cancelled:
                raise
            except Exception as exc:
                log.error("скоро: сбор не удался — %s", exc)
                soon = []

            try:
                ali = aliexpress.collect(fetcher, cfg, store)
            except Cancelled:
                raise
            except Exception as exc:
                log.error("aliexpress: сбор не удался — %s", exc)
                ali = []

            # Источники слухов и AliExpress ограничивают частоту и в любой
            # момент могут ответить проверкой вместо выдачи. Ронять из-за этого
            # целую вкладку нельзя: лучше показать сохранённое с прошлого раза,
            # чем пустоту — данные там всё равно живут дольше одного прогона.
            if not soon:
                soon = [Phone.from_dict(raw) for raw in store.kept_upcoming()]
                if soon:
                    log.info("скоро: свежих данных нет, показываю сохранённые (%s)",
                             len(soon))
            if not ali:
                ali = [Phone.from_dict(raw) for raw in store.kept_aliexpress()]
                if ali:
                    log.info("aliexpress: свежих данных нет, показываю сохранённые (%s)",
                             len(ali))

            gone = store.mark_gone(rt.get("stale_after_days", 30))
            store.record_run(len(offers), len(phones), len(matched), len(fresh))
            store.commit()
        finally:
            store.close()

        # Если отмена пришла на последних запросах, рисовать отчёт и слать
        # уведомления не нужно — приложение закрывается.
        if fetcher.stop.is_set():
            raise Cancelled("прогон прерван перед отчётом")

        out = report.render(data_dir / "report.html", enriched, len(offers), cfg,
                            spec_stats, soon, ali)
        # Уведомления только про новинки и только если это не первый прогон:
        # на пустой базе новыми окажутся все модели сразу.
        if not first_run:
            notify.send([p for p in fresh if not p.near_miss], cfg, out)
    finally:
        fetcher.close()

    return {
        "offers": len(offers),
        "phones": len(phones),
        "matched": len(matched),
        "upcoming": len(soon),
        "aliexpress": len(ali),
        "near_miss": len(keep) - len(matched),
        "new": len(fresh),
        "gone": gone,
        "specs": spec_stats,
        "requests": fetcher.requests_made,
        "report": out,
        "top": sorted(matched, key=lambda p: -p.score)[:10],
    }
