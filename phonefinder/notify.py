"""
Уведомления macOS о новых подходящих моделях.

Это единственное, что делает приложение агентом, а не генератором отчёта:
телефон с батареей 8000 мАч, IP68 и оптическим зумом — редкость, ждать его
можно месяцами, и смысл фонового прогона именно в том, чтобы узнать о нём
в день появления в магазине, не открывая отчёт.

Поэтому уведомление приходит только на модели, которых раньше не было в базе
(или которые только что стали подходящими, когда магазин дозаполнил
характеристики), и только выше порога рейтинга — иначе оно превращается в шум.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from .models import Phone

log = logging.getLogger(__name__)


def _osascript(script: str) -> None:
    subprocess.run(["osascript", "-e", script], check=False,
                   capture_output=True, timeout=10)


def banner(title: str, text: str, subtitle: str | None = None) -> None:
    """
    Разовое уведомление в обход rumps.

    Нужно для сообщений до старта приложения — например «уже запущен»:
    `rumps.notification` требует поднятого NSApplication, которого в этот
    момент ещё нет.
    """
    if not shutil.which("osascript"):
        return
    script = f"display notification {_quote(text)} with title {_quote(title)}"
    if subtitle:
        script += f" subtitle {_quote(subtitle)}"
    _osascript(script)


def send(new_phones: list[Phone], cfg: dict, report_path) -> int:
    conf = cfg.get("notify") or {}
    if not conf.get("enabled") or not new_phones:
        return 0
    if not shutil.which("osascript"):
        log.debug("osascript недоступен — уведомления пропущены")
        return 0

    min_score = conf.get("min_score", 60)
    max_items = conf.get("max_per_run", 5)

    worthy = sorted((p for p in new_phones if p.score >= min_score),
                    key=lambda p: -p.score)
    if not worthy:
        return 0

    for phone in worthy[:max_items]:
        spec = phone.spec
        bits = []
        if phone.price:
            bits.append(f"{phone.price} zł")
        if spec.battery_mah:
            bits.append(f"{spec.battery_mah} мАч")
        if spec.optical_zoom:
            bits.append(f"зум {spec.optical_zoom:g}×")
        if spec.ip:
            bits.append(spec.ip)
        if phone.stores:
            bits.append(", ".join(phone.stores[:2]))

        _osascript(
            'display notification {} with title {} subtitle {}'.format(
                _quote(" · ".join(bits)),
                _quote(phone.title[:60]),
                _quote(f"Phone Finder · новая модель, рейтинг {phone.score}"),
            )
        )

    extra = len(worthy) - max_items
    if extra > 0:
        _osascript(
            'display notification {} with title {}'.format(
                _quote(f"…и ещё {extra} — смотрите отчёт"), _quote("Phone Finder")
            )
        )

    log.info("отправлено уведомлений: %s", min(len(worthy), max_items))
    return min(len(worthy), max_items)


def _quote(text: str) -> str:
    """Строковый литерал AppleScript."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
