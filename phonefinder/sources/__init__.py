"""Адаптеры магазинов. Каждый модуль умеет build_url/fetch, часть — spec_pairs."""
from . import allegro, ceneo, komputronik, mediamarkt, morele, xkom

# Порядок важен: так источники опрашиваются за характеристиками. Первым идёт
# x-kom — у него в карточке самая полная таблица (зум, OIS, дата премьеры),
# сразу за ним Ceneo: его таблица столь же формализована и там единственная
# отдельная строка «Funkcje AI». Дальше остальные магазины.
ALL = {
    "xkom": xkom,
    "ceneo": ceneo,
    "morele": morele,
    "komputronik": komputronik,
    "mediamarkt": mediamarkt,
    "allegro": allegro,
}

SPEC_ORDER = [name for name, module in ALL.items() if getattr(module, "SPEC_CAPABLE", False)]
