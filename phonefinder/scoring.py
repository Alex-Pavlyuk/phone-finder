"""
Скоринг моделей: 0..100. Все веса — в config.yaml -> scoring.

Логика аддитивная и прозрачная: в отчёте показывается разбивка по слагаемым,
чтобы было видно, почему модель оказалась наверху. Главный вес — у зума
(«чем больше, тем лучше») и у запаса по батарее: это те две характеристики,
по которым модели, проходящие фильтр, отличаются друг от друга сильнее всего.
"""
from __future__ import annotations

from .models import Phone


def score(phone: Phone, cfg: dict) -> Phone:
    s = cfg["scoring"]
    cr = cfg["criteria"]
    spec = phone.spec
    parts: dict[str, float] = {"база": s.get("base", 40)}

    # Зум: считаем сверх минимума, с потолком — иначе перископ 10× забивает
    # собой все остальные слагаемые.
    if spec.optical_zoom:
        above = max(0.0, spec.optical_zoom - (cr.get("optical_zoom_min") or 0))
        parts["зум"] = round(min(above * s.get("zoom_per_x", 6),
                                s.get("zoom_cap", 28)), 1)

    # Батарея: за каждую 1000 мАч сверх минимума.
    if spec.battery_mah:
        above = max(0, spec.battery_mah - (cr.get("battery_mah_min") or 0))
        parts["батарея"] = round(min(above / 1000 * s.get("battery_per_1000_mah", 5),
                                    s.get("battery_cap", 20)), 1)

    if spec.ois:
        parts["стабилизация"] = s.get("ois_bonus", 5)
    if spec.ip69k:
        parts["IP69K"] = s.get("ip69k_bonus", 5)
    if spec.ai:
        parts["AI"] = s.get("ai_bonus", 6)

    # Камера: телеобъектив важнее основного модуля — именно им снимают на зуме.
    if spec.tele_mp:
        for threshold, bonus in (s.get("tele_mp_bonus") or []):
            if spec.tele_mp >= threshold:
                parts["телеобъектив"] = bonus
                break
    if spec.main_mp:
        for threshold, bonus in (s.get("main_mp_bonus") or []):
            if spec.main_mp >= threshold:
                parts["основная камера"] = bonus
                break

    # Запас по бюджету: дешевле — лучше, но вес небольшой, характеристики важнее.
    price = phone.price
    if price and cr.get("price_max"):
        margin = max(0.0, (cr["price_max"] - price) / cr["price_max"])
        parts["запас по цене"] = round(margin * s.get("price_margin_weight", 10), 1)

    # Несколько магазинов — это и выбор, и подтверждение, что модель реально
    # продаётся, а не висит одной карточкой у одного продавца.
    stores = len(phone.stores)
    if stores > 1:
        parts["в нескольких магазинах"] = min((stores - 1) * s.get("store_bonus", 2),
                                              s.get("store_cap", 6))

    brand_bonus = (s.get("brand_bonus") or {}).get((phone.brand or "").lower())
    if brand_bonus:
        parts[f"марка ({phone.brand})"] = brand_bonus

    # Неизвестные характеристики — минус: такая модель может оказаться и хуже,
    # и лучше, и держать её вровень с проверенной нечестно.
    if phone.unknown:
        parts["неизвестные характеристики"] = -s.get("unknown_penalty", 4) * len(phone.unknown)

    total = sum(parts.values())
    phone.score_parts = parts
    phone.score = int(max(0, min(100, round(total))))
    return phone
