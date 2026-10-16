"""
Отбор моделей по критериям.

Особенность задачи: у характеристики три состояния, а не два. «Батарея 5000»
— это отказ, «батарея неизвестна» — это не отказ, а неизвестность, и вести
себя с ними надо по-разному. Молча выбрасывать неизвестное нельзя: как раз
у новинок таблица характеристик в магазине заполнена хуже всего, а именно
новинки мы и ищем. Поэтому такие модели уходят во вкладку «почти» с честной
пометкой, чего мы про них не знаем.
"""
from __future__ import annotations

from .models import Phone

# Текст пометки целиком, а не «подпись + не указан»: по-русски согласование
# у этих пяти полей разное, и склеивать его на ходу негде.
UNKNOWN_TEXT = {
    "battery": "ёмкость батареи не указана",
    "ip": "класс защиты IP не указан",
    "ois": "оптическая стабилизация не указана",
    "zoom": "оптический зум не указан",
    "ai": "AI-сервисы не подтверждены",
}


def _ip_required_level(value: str | None) -> int:
    if not value:
        return 0
    digits = "".join(c for c in str(value) if c.isdigit())
    return int(digits[:2]) if len(digits) >= 2 else 0


def apply(phone: Phone, cfg: dict) -> Phone:
    """Проставляет reject_reasons / near_miss_reasons. Пусто -> модель подходит."""
    cr = cfg["criteria"]
    nm = cfg.get("near_miss") or {}
    soft_enabled = bool(nm.get("enabled"))
    hard: list[str] = []
    soft: list[str] = []
    spec = phone.spec

    def unknown(field: str) -> None:
        """Неизвестное поле: либо в «почти», либо в отказ — по настройке."""
        allowed = nm.get(f"allow_unknown_{field}", False) and soft_enabled
        (soft if allowed else hard).append(UNKNOWN_TEXT[field])

    # ── батарея ───────────────────────────────────────────────────────────────
    battery_min = cr.get("battery_mah_min") or 0
    floor = cr.get("battery_mah_floor") or 0
    # Абсолютный минимум проверяется первым и не знает про мягкие допуски:
    # телефон с батареей ниже него не нужен ни в какой вкладке, поэтому и
    # near_miss тут ничего решить не может.
    if floor and spec.battery_mah is not None and spec.battery_mah < floor:
        phone.reject_reasons = [f"батарея {spec.battery_mah} мАч — ниже минимума {floor}"]
        phone.near_miss_reasons = []
        phone.near_miss = False
        return phone

    if spec.battery_mah is None:
        # Точного числа нет, но источник мог гарантировать нижнюю границу своим
        # фильтром (Allegro умеет фильтровать по ёмкости). Если гарантия
        # покрывает требование — вопрос закрыт, иначе это неизвестность.
        if spec.battery_min and spec.battery_min >= battery_min:
            pass
        else:
            unknown("battery")
    elif spec.battery_mah < battery_min:
        slack = nm.get("battery_slack_mah", 0) if soft_enabled else 0
        if spec.battery_mah >= battery_min - slack:
            soft.append(f"батарея {spec.battery_mah} мАч вместо {battery_min}")
        else:
            hard.append(f"батарея {spec.battery_mah} мАч < {battery_min}")

    # ── защита от воды ────────────────────────────────────────────────────────
    ip_min = _ip_required_level(cr.get("ip_min"))
    if ip_min:
        if spec.ip is None:
            unknown("ip")
        elif spec.ip_level < ip_min:
            hard.append(f"{spec.ip} слабее IP{ip_min}")

    # ── стабилизация ──────────────────────────────────────────────────────────
    if cr.get("ois_required"):
        if spec.ois is None:
            unknown("ois")
        elif spec.ois is False:
            hard.append("нет оптической стабилизации")

    # ── зум ───────────────────────────────────────────────────────────────────
    zoom_min = cr.get("optical_zoom_min") or 0
    if zoom_min:
        if spec.optical_zoom is None:
            unknown("zoom")
        elif spec.optical_zoom < zoom_min:
            slack = nm.get("zoom_slack_x", 0) if soft_enabled else 0
            if spec.optical_zoom >= zoom_min - slack:
                soft.append(f"зум {_zoom(spec.optical_zoom)} вместо {_zoom(zoom_min)}")
            else:
                hard.append(f"зум {_zoom(spec.optical_zoom)} < {_zoom(zoom_min)}")

    # ── AI ────────────────────────────────────────────────────────────────────
    # Отсутствие AI в таблице характеристик — не доказательство: магазины про
    # него почти не пишут, поэтому «не подтверждён» по умолчанию мягкий отказ.
    if cr.get("ai_required") and not spec.ai:
        unknown("ai")

    # ── цена и наличие ────────────────────────────────────────────────────────
    price = phone.price
    price_max = cr.get("price_max") or 0
    if price is None:
        hard.append("нет цены на новый")
    elif price_max and price > price_max:
        overshoot = (price - price_max) / price_max * 100
        if soft_enabled and overshoot <= nm.get("price_overshoot_pct", 0):
            soft.append(f"дороже бюджета на {overshoot:.0f}%")
        else:
            hard.append(f"цена {price} zł > {price_max} zł")
    elif cr.get("price_min") and price < cr["price_min"]:
        hard.append(f"цена {price} zł ниже {cr['price_min']} zł — вероятно не телефон")

    phone.reject_reasons = hard
    phone.near_miss_reasons = soft
    phone.near_miss = bool(soft) and not hard
    return phone


def _zoom(value: float) -> str:
    return f"{value:g}×"
