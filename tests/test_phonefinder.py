"""Тесты на хрупкие места: разбор заголовков, характеристик, фильтры, источники."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from phonefinder import (aliexpress, catalog, filters, news, report,  # noqa: E402
                         scoring, specs, upcoming)
from phonefinder.models import (Offer, Phone, Spec, is_accessory,  # noqa: E402
                                looks_used, parse_int, parse_title)
from phonefinder.sources import (allegro, ceneo, jsonld, komputronik,  # noqa: E402
                                 mediamarkt, morele, xkom)
from phonefinder.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CFG = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

# Ёмкость, которая заведомо попадает в «Почти»: ниже порога основной вкладки,
# но не ниже абсолютного минимума. Вычисляется из конфига, а не вписана числом,
# — пороги меняются по ходу поиска, и тесты не должны от этого падать.
NEAR_BATTERY = max(CFG["criteria"]["battery_mah_floor"],
                   CFG["criteria"]["battery_mah_min"] - CFG["near_miss"]["battery_slack_mah"])


def offer(**kw) -> Offer:
    base = dict(source="xkom", ext_id="1", url="https://x-kom.pl/p/1-x.html",
                title="Samsung Galaxy S25 12/128GB Zielony", price=3000)
    base.update(kw)
    parsed = parse_title(base["title"])
    base.setdefault("brand", parsed["brand"])
    base.setdefault("model", parsed["model"])
    base.setdefault("model_key", parsed["key"])
    return Offer(**base)


def phone(spec: Spec, price: int = 3000, **kw) -> Phone:
    p = Phone(key="test model", brand="Samsung", name="Galaxy Test", spec=spec,
              offers=[offer(price=price)])
    for name, value in kw.items():
        setattr(p, name, value)
    return p


# ── разбор заголовков магазинов ───────────────────────────────────────────────
@pytest.mark.parametrize("title,key,variant", [
    ("Samsung Galaxy S25 12/128GB Zielony", "samsung galaxy s25", "12/128 GB"),
    ("Smartfon Samsung Galaxy S25 5G 12/128GB Granatowy (SM-S931BDB)",
     "samsung galaxy s25", "12/128 GB"),
    ("Smartfon SAMSUNG Galaxy S26 Ultra 5G 16 GB/ 1 TB Czarny",
     "samsung galaxy s26 ultra", "16/1024 GB"),
    ("Apple iPhone 17 Pro Max 1TB Srebrny", "apple iphone17 pro max", "1024 GB"),
    ("Telefon Samsung Galaxy A57 128GB 5G Dual SIM Granatowy",
     "samsung galaxy a57", "128 GB"),
    ("Motorola edge 60 5G 12/512GB Gibraltar Sea 120Hz", "motorola edge60", "12/512 GB"),
    ("Smartfon XIAOMI Redmi Note 15 4G (LTE) 8 GB/ 256 GB Czarny",
     "xiaomi redmi note15", "8/256 GB"),
])
def test_parse_title(title, key, variant):
    parsed = parse_title(title)
    assert parsed["key"] == key
    assert parsed["variant"] == variant


@pytest.mark.parametrize("title,key", [
    # приписки продавцов Allegro: прилагательные, цвет перед памятью, диагональ,
    # единицы измерения, «5 G» с пробелом, маркетинговые цвета после номера
    ("Smartfon DOOGEE V40 czerwony 12 GB RAM 512 GB 108 MP 8680 mAh", "doogee v40"),
    ("Smartfon Pancerny DOOGEE S200MAX 5G 16/512GB NFC DUAL SIM IP69K", "doogee s200max"),
    ("Smartfon DOOGEE S200 wodoodporny 10100 mAh", "doogee s200"),
    ('Pancerny Smartfon Oukitel WP55 6,6" 12/256GB DS. 11000mAh Pomarańczowy', "oukitel wp55"),
    ("Xiaomi Redmi Note 17 Pro 5 G 12/256GB Sky Teal", "xiaomi redmi note17 pro"),
    ("Xiaomi Redmi Note 17 Pro MAX Cloud Blue 12/512GB", "xiaomi redmi note17 pro max"),
    ("Ulefone Armor 26 Ultra Walkie-Talkie 5G 12/512GB 15600mAh", "ulefone armor26 ultra"),
    # а это трогать нельзя: обычные слова до номера и известные индексы после
    ("Nothing Phone (3a) 12/256GB Czarny", "nothing phone3a"),
    ("Cubot King Kong 11 5G 16/256GB", "cubot king kong11"),
    ("Google Pixel 10 Pro XL 16/256GB", "google pixel10 pro xl"),
    ("vivo X500 E 12/256GB", "vivo x500 e"),
])
def test_seller_junk_does_not_leak_into_model_key(title, key):
    """Грязный ключ дробит модель на несколько и ломает поиск характеристик."""
    assert parse_title(title)["key"] == key


def test_same_model_from_different_stores_gets_one_key():
    """Ключ модели — единственное, что связывает пять магазинов между собой."""
    keys = {
        parse_title("Samsung Galaxy S25 12/128GB Zielony")["key"],
        parse_title("Smartfon Samsung Galaxy S25 5G 12/128GB Granatowy (SM-S931BDB)")["key"],
        parse_title("Smartfon SAMSUNG Galaxy S25 5G 12 GB/ 128 GB Niebieski")["key"],
        parse_title("SAMSUNG Galaxy S25 5G 12/128GB czarny")["key"],
    }
    assert keys == {"samsung galaxy s25"}


def test_letter_digit_spacing_does_not_split_a_model():
    """«OPPO Reno15 Pro» и «OPPO Reno 15 Pro» — один телефон, а не два."""
    assert (parse_title("OPPO Reno15 Pro 12/256GB")["key"]
            == parse_title("OPPO Reno 15 Pro 12/256GB Czarny")["key"])
    assert (parse_title("HONOR Magic8 Pro 12/512GB")["key"]
            == parse_title("HONOR Magic 8 Pro 12/512GB")["key"])


def test_display_name_keeps_original_case():
    assert parse_title("Apple iPhone 17 Pro 256GB")["model"] == "iPhone 17 Pro"


@pytest.mark.parametrize("value,expected", [
    ("2 949,00 zł", 2949),          # цена из выдачи x-kom
    ("4283.99", 4283),              # цена из schema.org Morele — гроши точкой
    (4283.99, 4283),
    ("4000 mAh", 4000),
    ("1 234", 1234),
    ("", None),
])
def test_parse_int(value, expected):
    assert parse_int(value) == expected


@pytest.mark.parametrize("title,accessory", [
    ("Etui Spigen do Samsung Galaxy S25", True),
    ("Szkło hartowane 3mk do iPhone 17", True),
    ("Ładowarka sieciowa Samsung 45W", True),
    ("Powerbank Baseus 20000mAh", True),
    ("Smartfon Samsung Galaxy S25 12/128GB", False),
    ("HONOR Magic8 Pro 12/512GB", False),
])
def test_accessory_filter(title, accessory):
    assert is_accessory(title) is accessory


def test_used_detection():
    assert looks_used("Samsung Galaxy S24 128GB używany klasa A")
    assert not looks_used("Samsung Galaxy S24 128GB")


# ── характеристики ────────────────────────────────────────────────────────────
XKOM_PAIRS = [
    ("Pojemność baterii", "4000 mAh"),
    ("Odporności", "Pyłoszczelność i wodoszczelność (IP68)"),
    ("Dodatkowe cechy aparatu", "Optyczna stabilizacja obrazu"),
    ("Zoom - kamera tylna", "3x zoom optyczny"),
    ("Zoom - kamera tylna", "30x zoom cyfrowy"),
    ("Rozdzielczość aparatu - tył", "50.0 Mpix"),
    ("Rozdzielczość aparatu - tył", "10.0 Mpix - teleobiektyw"),
    ("Rozdzielczość aparatu - przód", "12.0 Mpix"),
    ("Moc ładowania", "max. 25 W"),
    ("Przekątna ekranu", '6,2"'),
    ("Częstotliwość odświeżania ekranu", "120 Hz"),
    ("Data premiery", "Styczeń 2025"),
]


def test_specs_from_xkom_pairs():
    spec = specs.build(XKOM_PAIRS, "xkom")
    assert spec.battery_mah == 4000
    assert spec.ip == "IP68"
    assert spec.ois is True
    assert spec.optical_zoom == 3        # 30x цифрового не считаем
    assert spec.main_mp == 50.0
    assert spec.tele_mp == 10.0
    assert spec.front_mp == 12.0
    assert spec.charge_w == 25
    assert spec.screen_in == 6.2
    assert spec.refresh_hz == 120
    assert spec.release == "Styczeń 2025"


def test_no_telephoto_means_no_zoom_not_unknown():
    """
    Строки «Zoom» в таблице нет — но модули камеры подписаны, и телеобъектива
    среди них нет. Это «оптики нет» (1×), а не «магазин не написал».
    """
    spec = specs.build([("Rozdzielczość aparatu - tył", "50.0 Mpix"),
                        ("Rozdzielczość aparatu - tył", "8.0 Mpix - ultraszerokokątny"),
                        ("Dodatkowe cechy aparatu", "Optyczna stabilizacja obrazu")], "xkom")
    assert spec.optical_zoom == 1.0
    assert "zoom" not in spec.missing()


def test_listed_camera_features_without_ois_mean_no_ois():
    """«Не указана» и «нет» — разные вещи, и путать их нельзя."""
    spec = specs.build([("Dodatkowe cechy aparatu", "Wbudowana lampa błyskowa"),
                        ("Dodatkowe cechy aparatu", "Face ID")], "xkom")
    assert spec.ois is False
    assert "ois" not in spec.missing()

    # карточку не открывали — остаётся неизвестностью
    assert specs.build([("Pojemność baterii", "9000 mAh")], "xkom").ois is None


def test_unlabeled_camera_list_stays_unknown():
    """У Morele модули не подписаны — по такому списку вывод делать нельзя."""
    spec = specs.build([("Aparat główny", "50 + 12 + 10 MPix")], "morele")
    assert spec.optical_zoom is None


def test_telephoto_in_the_list_is_not_touched():
    spec = specs.build(XKOM_PAIRS, "xkom")
    assert spec.optical_zoom == 3 and spec.tele_mp == 10.0


def test_optical_quality_zoom_is_not_optical():
    """У Apple «8x zoom jakości optycznej» — это кроп, а не оптика."""
    spec = specs.build([("Zoom - kamera tylna", "4x zoom optyczny"),
                        ("Zoom - kamera tylna", "8x zoom jakości optycznej")], "xkom")
    assert spec.optical_zoom == 4


def test_specs_from_morele_pairs():
    spec = specs.build([("Wodoodporny/pyłoodporny", "IP68"),
                        ("Funkcje AI", "Galaxy AI"),
                        ("Aparat główny", "50 + 12 + 10 MPix"),
                        ("Pojemność baterii", "4000 mAh")], "morele")
    assert spec.ip == "IP68"
    assert spec.ai == ["Galaxy AI"]
    assert spec.main_mp == 50.0
    assert spec.battery_mah == 4000


def test_specs_from_komputronik_text():
    text = ("Bateria Pojemność akumulatora 4300 mAh Ładowanie bezprzewodowe tak "
            "Zoom optyczny 3 x Zoom cyfrowy 30 x Optyczna stabilizacja obrazu (OIS) tak "
            "Pyłoszczelność i wodoszczelność IP68 Waga 162 g")
    spec = specs.build(specs.pairs_from_text(text), "komputronik")
    assert (spec.battery_mah, spec.optical_zoom, spec.ois, spec.ip) == (4300, 3.0, True, "IP68")
    assert spec.weight_g == 162


def test_labeled_pairs_win_over_page_text():
    """Текст страницы только закрывает пропуски: в нём хватает чужих чисел."""
    html = ("<div>Zoom optyczny 10x zoom cyfrowy</div>"
            "<div>Pojemność akumulatora 20000 mAh</div>")
    spec = specs.from_html(html, "xkom", [("Zoom - kamera tylna", "3x zoom optyczny")])
    assert spec.optical_zoom == 3.0
    assert spec.battery_mah == 20000     # своего значения не было — взяли из текста


def test_ai_never_comes_from_free_text():
    """Иначе Redmi получал бы Galaxy AI из блока «похожие товары»."""
    html = "<p>Porównaj z Samsung Galaxy S25 z Galaxy AI</p>"
    spec = specs.from_html(html, "komputronik", [])
    assert spec.ai == []


def test_ai_rules_by_model():
    spec = Spec()
    specs.apply_ai_rules(spec, "Samsung Galaxy S25 Ultra", CFG)
    assert spec.ai == ["Galaxy AI"]
    other = Spec()
    specs.apply_ai_rules(other, "Google Pixel 10 Pro", CFG)
    assert other.ai == ["Gemini Nano"]


def test_spec_merge_fills_gaps_only():
    base = Spec(battery_mah=5000, ip="IP68", sources=["xkom"])
    extra = Spec(battery_mah=9999, optical_zoom=5.0, ai=["Galaxy AI"])
    base.merge(extra, "morele")
    assert base.battery_mah == 5000      # своё не перезаписываем
    assert base.optical_zoom == 5.0
    assert base.ai == ["Galaxy AI"]
    assert base.sources == ["xkom", "morele"]


@pytest.mark.parametrize("value,level,ip69k", [
    ("IP68", 68, False), ("IP69K", 69, True), ("IP68/IP69K", 69, True), (None, 0, False),
])
def test_ip_level(value, level, ip69k):
    spec = Spec(ip=value)
    assert spec.ip_level == level
    assert spec.ip69k is ip69k


# ── фильтры ───────────────────────────────────────────────────────────────────
def full_spec(**kw) -> Spec:
    base = dict(battery_mah=8200, ip="IP68", ois=True, optical_zoom=3.0, ai=["Galaxy AI"])
    base.update(kw)
    return Spec(**base)


def test_matching_phone_passes():
    p = filters.apply(phone(full_spec()), CFG)
    assert p.reject_reasons == [] and not p.near_miss


def test_battery_below_floor_is_never_shown():
    """Ниже абсолютного минимума модель не попадает ни в одну вкладку."""
    floor = CFG["criteria"]["battery_mah_floor"]
    p = filters.apply(phone(full_spec(battery_mah=floor - 1)), CFG)
    assert p.reject_reasons and not p.near_miss and not p.near_miss_reasons

    # и никакая щедрость допуска этого не отменяет
    loose = json_copy(CFG)
    loose["near_miss"]["battery_slack_mah"] = 5000
    assert filters.apply(phone(full_spec(battery_mah=floor - 1)), loose).reject_reasons

    # ровно на границе — уже «Почти»
    edge = filters.apply(phone(full_spec(battery_mah=floor)), CFG)
    assert not edge.reject_reasons and edge.near_miss


def test_small_battery_within_slack_is_near_miss():
    p = filters.apply(phone(full_spec(battery_mah=NEAR_BATTERY)), CFG)
    assert not p.reject_reasons and p.near_miss


def test_small_battery_beyond_slack_is_rejected():
    p = filters.apply(phone(full_spec(battery_mah=4000)), CFG)
    assert p.reject_reasons


@pytest.mark.parametrize("spec", [
    full_spec(ois=False),        # прямо сказано, что стабилизации нет
    full_spec(ois=None),         # магазин не написал — предлагать всё равно нельзя
    full_spec(optical_zoom=None),
])
def test_camera_must_be_confirmed(spec):
    """Без подтверждённых OIS и оптического зума модель не предлагаем вообще."""
    p = filters.apply(phone(spec), CFG)
    assert p.reject_reasons and not p.near_miss


def test_unknown_battery_or_ip_still_lands_in_near_miss():
    """А вот батарею и IP магазины у новинок дописывают позже — их ждём."""
    for spec in (full_spec(battery_mah=None), full_spec(ip=None)):
        p = filters.apply(phone(spec), CFG)
        assert not p.reject_reasons and p.near_miss


def test_weak_ip_is_rejected():
    assert filters.apply(phone(full_spec(ip="IP67")), CFG).reject_reasons


def test_unknown_camera_can_be_relaxed_by_config():
    soft = json_copy(CFG)
    soft["near_miss"]["allow_unknown_zoom"] = True
    p = filters.apply(phone(full_spec(optical_zoom=None)), soft)
    assert not p.reject_reasons and p.near_miss


def test_price_over_budget_is_rejected():
    p = filters.apply(phone(full_spec(), price=CFG["criteria"]["price_max"] * 2), CFG)
    assert p.reject_reasons


def json_copy(cfg: dict) -> dict:
    return json.loads(json.dumps(cfg))


# ── скоринг ───────────────────────────────────────────────────────────────────
def test_more_zoom_scores_higher():
    low = scoring.score(phone(full_spec(optical_zoom=3.0)), CFG).score
    high = scoring.score(phone(full_spec(optical_zoom=10.0)), CFG).score
    assert high > low


def test_bigger_battery_scores_higher():
    small = scoring.score(phone(full_spec(battery_mah=8000)), CFG).score
    big = scoring.score(phone(full_spec(battery_mah=11000)), CFG).score
    assert big > small


def test_unknown_specs_cost_points():
    known = scoring.score(phone(full_spec()), CFG)
    vague = phone(full_spec(optical_zoom=None))
    vague.unknown = ["zoom"]
    assert scoring.score(vague, CFG).score < known.score


# ── группировка ───────────────────────────────────────────────────────────────
def test_group_merges_stores_and_picks_cheapest_new():
    offers = [
        offer(source="xkom", ext_id="1", price=3999,
              title="Samsung Galaxy S25 12/128GB Zielony"),
        offer(source="morele", ext_id="2", price=3499,
              title="Smartfon Samsung Galaxy S25 5G 12/128GB Granatowy (SM-S931BDB)"),
        offer(source="allegro", ext_id="3", price=2999, used=True,
              title="Samsung Galaxy S25 12/128GB używany"),
    ]
    phones = catalog.group(offers)
    assert len(phones) == 1
    found = phones[0]
    assert found.price == 3499            # б/у в лучшую цену не попадает
    assert len(found.stores) == 2
    assert found.name == "Galaxy S25"


# ── источники ─────────────────────────────────────────────────────────────────
def test_allegro_parses_state_and_unwraps_click_url():
    element = {
        "type": "product", "offerId": "123", "id": "abc",
        "title": {"text": "Smartfon Samsung Galaxy S25 12/128GB czarny"},
        "price": {"mainPrice": {"amount": "3499.00", "currency": "PLN"}},
        "url": "https://allegro.pl/events/clicks?type=OFFER&redirect="
               "https%3A%2F%2Fallegro.pl%2Foferta%2Fgalaxy-s25-123%3Fbi_s%3Dads",
        "mainThumbnail": "https://a.allegroimg.com/x.jpg",
        "seller": {"login": "sklep"},
    }
    parsed = allegro._to_offer(element)
    assert parsed.price == 3499
    assert parsed.url == "https://allegro.pl/oferta/galaxy-s25-123"
    assert parsed.model_key == "samsung galaxy s25"


def test_allegro_url_respects_criteria():
    url = allegro.build_url(CFG, page=2)
    assert "stan=nowe" in url and "price_to=" in url and "p=2" in url
    # Фильтр Allegro по ёмкости — единственный, который применяет сам источник:
    # без него его страницы уходят на телефоны с 5000 мАч.
    assert f"pojemnosc-akumulatora-od={CFG['criteria']['battery_mah_min']}" in url


def test_allegro_offer_carries_guaranteed_battery():
    element = {
        "type": "product", "offerId": "1", "title": {"text": "Smartfon DOOGEE S200 12/256GB"},
        "price": {"mainPrice": {"amount": "1581.00", "currency": "PLN"}},
        "url": "https://allegro.pl/oferta/doogee-s200-1",
    }
    assert allegro._to_offer(element, 7500).battery_min_guaranteed == 7500


def test_guaranteed_battery_satisfies_the_requirement():
    """Точного числа нет, но выдача Allegro запрошена с фильтром по ёмкости."""
    p = phone(full_spec(battery_mah=None))
    p.offers = [offer(source="allegro", ext_id="9", battery_min_guaranteed=7500)]
    catalog._apply_guaranteed_battery(p)
    p.unknown = p.spec.missing()
    assert p.spec.battery_min == 7500
    assert "battery" not in p.unknown

    filters.apply(p, CFG)
    assert not p.reject_reasons and not p.near_miss


def test_store_number_wins_over_guarantee():
    """Таблицу характеристик пишет магазин, а фильтр — параметры продавца."""
    p = phone(full_spec(battery_mah=NEAR_BATTERY))
    p.offers = [offer(source="allegro", ext_id="9",
                      battery_min_guaranteed=CFG["criteria"]["battery_mah_min"])]
    catalog._apply_guaranteed_battery(p)
    assert p.spec.battery_min is None
    assert filters.apply(p, CFG).near_miss   # число магазина — это «Почти»

    # а если магазин говорит 5000, гарантия Allegro не спасает: ниже минимума
    weak = phone(full_spec(battery_mah=CFG["criteria"]["battery_mah_floor"] - 1))
    weak.offers = [offer(source="allegro", ext_id="8",
                         battery_min_guaranteed=CFG["criteria"]["battery_mah_min"])]
    catalog._apply_guaranteed_battery(weak)
    assert filters.apply(weak, CFG).reject_reasons


def test_jsonld_itemlist():
    html = ('<script type="application/ld+json">'
            + json.dumps({"@type": "ItemList", "itemListElement": [
                {"@type": "ListItem", "item": {
                    "@type": "Product", "name": "Smartfon HONOR Magic8 Pro 12/512GB",
                    "url": "https://www.morele.net/smartfon-honor-magic8-pro-15847126/",
                    "offers": {"@type": "Offer", "price": "4499.00", "priceCurrency": "PLN"},
                    "image": ["https://images.morele.net/x.jpg"]}}]})
            + "</script>")
    items = jsonld.itemlist_products(html)
    assert items[0]["price"] == "4499.00"
    parsed = morele._to_offer(items[0])
    assert parsed.ext_id == "15847126"
    assert parsed.price == 4499
    assert parsed.model_key == "honor magic8 pro"


def test_xkom_card_parsing():
    chunk = ('data-name="productCard" ... '
             '<a title="HONOR Magic8 Pro 12/512GB Czarny" font-size="s" color="link" '
             'href="/p/1477394-smartfon-telefon-honor-magic8-pro.html">'
             '<span aria-label="Cena: 4 499,00 zł"></span>'
             '<img src="https://cdn.x-kom.pl/i/x.jpg">')
    parsed = xkom._to_offer(chunk.split('data-name="productCard"')[-1])
    assert (parsed.ext_id, parsed.price) == ("1477394", 4499)
    assert parsed.url.endswith("/p/1477394-smartfon-telefon-honor-magic8-pro.html")
    assert parsed.in_stock is True


def test_battery_qualified_models_are_resolved_first():
    """
    Лимит запросов конечен: модель, которой осталось выяснить только камеру,
    должна попасть в начало очереди, а не в конец.
    """
    allegro_only = phone(Spec())
    allegro_only.offers = [offer(source="allegro", ext_id="1", battery_min_guaranteed=7500)]
    popular = phone(Spec())
    popular.offers = [offer(source="xkom", ext_id="2"), offer(source="morele", ext_id="3"),
                      offer(source="mediamarkt", ext_id="4")]

    queue = sorted([popular, allegro_only], key=lambda p: catalog._priority(p, CFG))
    assert queue[0] is allegro_only


def test_xkom_search_finds_model_absent_from_listing_pages(monkeypatch):
    """
    Модель с большой батареей видно на Allegro, а в первых страницах категории
    x-kom её нет — тогда карточка ищется по названию.
    """
    page = ('data-name="productCard"'
            '<a title="Doogee S200 12/256GB Szary" font-size="s" color="link" '
            'href="/p/1318777-smartfon-telefon-doogee-s200-12-256gb-szary.html">'
            '<span aria-label="Cena: 1 499,00 zł"></span>')

    class FakeFetcher:
        def get(self, url, referer=None, fresh=False):
            assert "szukaj?q=" in url and "Doogee" in url
            return page

    found = xkom.search(FakeFetcher(), "Doogee S200")
    assert found[0].model_key == "doogee s200"

    target = Phone(key="doogee s200", brand="Doogee", name="S200")
    assert catalog._find_by_name(target, FakeFetcher()).ext_id == "1318777"
    # чужую модель поиск не подсовывает
    assert catalog._find_by_name(Phone(key="doogee s300", brand="Doogee", name="S300"),
                                 FakeFetcher()) is None


def test_lookup_falls_back_to_morele():
    """x-kom не возит «кирпичи» с большой батареей, а Morele возит."""
    morele_page = ('data-link-href-param="/smartfon-oukitel-wp300-5g-12-512gb-'
                   'czarny-wp300-bk-ol-15397581/" '
                   'title="Smartfon Oukitel WP300 5G 12/512GB Czarny (WP300-BK/OL)"')

    class FakeFetcher:
        def get(self, url, referer=None, fresh=False):
            if "x-kom" in url:
                return ""            # в x-kom такого нет
            return morele_page

    found = catalog._find_by_name(Phone(key="oukitel wp300", brand="Oukitel", name="WP300"),
                                  FakeFetcher())
    assert found is not None and found.source == "morele"
    assert found.url.endswith("-15397581/")


def test_komputronik_tile_parsing():
    chunk = ('<a href="/product/985399/telefon-motorola-moto-g06-power-4-64gb.html" '
             'title="Motorola Moto G06 Power 4/64GB Dual SIM Tapestry"></a>'
             '<div data-price-type="final" class="x">399 zł </div>'
             '<div data-name="statusAvailable"></div>')
    parsed = komputronik._to_offer(chunk)
    assert (parsed.ext_id, parsed.price, parsed.in_stock) == ("985399", 399, True)


def test_mediamarkt_features_and_offer():
    html = ('{"name":"Stopień ochrony przed wnikaniem","type":null,"unit":null,'
            '"value":"IP68"}')
    assert mediamarkt.spec_pairs(html) == [("Stopień ochrony przed wnikaniem", "IP68")]
    parsed = mediamarkt._to_offer({
        "name": "Smartfon SAMSUNG Galaxy S26 Ultra 5G 16 GB/ 1 TB Czarny",
        "url": "https://mediamarkt.pl/pl/product/_smartfon-samsung-1502114.html",
        "price": 6999, "image": None, "currency": "PLN"})
    assert (parsed.ext_id, parsed.model_key) == ("1502114", "samsung galaxy s26 ultra")


CENEO_TILE = '''class="cat-prod-row js_category-list-item"
data-pid="196107845" data-brand="Motorola"
data-productName="Motorola Edge 70 Pro 8/256GB Granatowy"
data-productminprice="2107">
<div class="cat-prod-row__foto"><a href="/196107845">
<img src="//image.ceneostatic.pl/data/products/196107845/f-motorola.jpg" alt="x" /></a></div>'''

CENEO_SPEC = '''<tr class="product-spec__group__attributes__row">
<td class="product-spec__group__attributes__row__name">
Stopie&#324; ochrony IP
 <span class="hint-handle js_x" data-tiptitle="IP">?<span class="hidden">Odporno&#347;&#263;
 na wod&#281; i py&#322;</span></span>
 </td>
<td class="product-spec__group__attributes__row__value">
IP68, IP69, MIL-STD-810H  </td>
</tr>
<tr class="product-spec__group__attributes__row">
<td class="product-spec__group__attributes__row__name">
Pojemno&#347;&#263; baterii
 </td>
<td class="product-spec__group__attributes__row__value">
6500mAh  </td>
</tr>
<tr class="product-spec__group__attributes__row">
<td class="product-spec__group__attributes__row__name">
Funkcje aparatu
 </td>
<td class="product-spec__group__attributes__row__value">
Makro, Autofokus, OIS, Optyczna stabilizacja obrazu  </td>
</tr>
<tr class="product-spec__group__attributes__row">
<td class="product-spec__group__attributes__row__name">
Zoom cyfrowy
 </td>
<td class="product-spec__group__attributes__row__value">
30x  </td>
</tr>
<tr class="product-spec__group__attributes__row">
<td class="product-spec__group__attributes__row__name">
Funkcje AI
 </td>
<td class="product-spec__group__attributes__row__value">
Tak  </td>
</tr>'''


def test_ceneo_tile_parsing():
    chunk = ceneo.TILE_SPLIT_RE.split(CENEO_TILE)[-1]
    parsed = ceneo._to_offer(chunk)
    assert (parsed.ext_id, parsed.price, parsed.brand) == ("196107845", 2107, "Motorola")
    assert parsed.url == "https://www.ceneo.pl/196107845"
    assert parsed.image.startswith("https://image.ceneostatic.pl/")
    assert parsed.model_key == "motorola edge70 pro"


def test_ceneo_nested_classes_do_not_split_a_tile():
    """`cat-prod-row__body` внутри плитки не должен обрывать её на куски."""
    assert len(ceneo.TILE_SPLIT_RE.split(CENEO_TILE)) == 2


def test_ceneo_spec_table():
    pairs = ceneo.spec_pairs(CENEO_SPEC)
    assert ("Pojemność baterii", "6500mAh") in pairs
    # подсказка «?» с абзацем текста не должна попасть в подпись
    assert ("Stopień ochrony IP", "IP68, IP69, MIL-STD-810H") in pairs

    spec = specs.build(pairs, "ceneo")
    assert spec.battery_mah == 6500
    assert spec.ip == "IP69"          # из перечисления берём старший класс
    assert spec.ip_level == 69
    assert spec.ois is True           # OIS спрятан в «Funkcje aparatu»
    assert spec.optical_zoom is None  # 30x — цифровой, не считается
    assert spec.ai == ["AI"]          # «Funkcje AI: Tak»


def test_ceneo_page_two_url_is_zero_based():
    assert ceneo.build_url(CFG, 1).endswith("/Smartfony")
    assert ceneo.build_url(CFG, 2).endswith(";0020-30-0-0-1.htm")


def test_morele_page_two_url_keeps_filter_slots():
    """Без запятых-заглушек Morele отдаёт первую страницу вместо второй."""
    assert morele.build_url(CFG, 2).endswith("/,,,,,,,,0,,,,/2/")


# ── вкладка «Скоро» ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("headline,key", [
    ("Samsung Galaxy S26 Ultra launch date leaked", "samsung galaxy s26 ultra"),
    ("Redmi Note 17 Pro Max spotted with IP69", "xiaomi redmi note17 pro max"),
    ("Google Deals: Pixel 11 series is up to $200 off", "google pixel11"),
    ("Weekly poll results: the best phone of 2026", None),      # это не модель
    ("Cubot Teases KINGKONG Dura a Rugged Phone", None),        # нет номера модели
])
def test_model_from_headline(headline, key):
    parsed = upcoming.model_from_headline(headline)
    assert (parsed["key"] if parsed else None) == key


@pytest.mark.parametrize("expected,result", [
    ("2026, Q4", date(2026, 12, 31)),
    ("February 2026", date(2026, 2, 28)),
    ("2027", date(2027, 12, 31)),
    ("Rumored", None),
])
def test_expected_deadline(expected, result):
    assert upcoming.deadline(expected, date(2026, 8, 13)) == result


def test_upcoming_keeps_unknowns_but_drops_contradictions():
    """У слуха характеристики неполные — это не повод его прятать."""
    assert upcoming._contradicts(Spec(battery_mah=8000), CFG) is None
    assert upcoming._contradicts(Spec(), CFG) is None
    assert upcoming._contradicts(Spec(battery_mah=5000), CFG) == "батарея"
    assert upcoming._contradicts(Spec(ip="IP67"), CFG) == "IP"
    assert upcoming._contradicts(Spec(ois=False), CFG) == "OIS"


def test_gsmarena_pairs_keep_section_in_label():
    """«Type» есть и у батареи, и у экрана — без секции их не различить."""
    html = ('<table><tr><th>Battery</th><td class="ttl"><a>Type</a></td>'
            '<td class="nfo">Li-Ion 9000 mAh</td></tr>'
            '<tr><th>Body</th><td class="ttl"><a>Protection</a></td>'
            '<td class="nfo">IP69K dust/water resistant</td></tr></table>')
    pairs = upcoming._gsm_pairs(html)
    assert ("Battery Type", "Li-Ion 9000 mAh") in pairs
    spec = specs.build(pairs, "gsmarena")
    assert spec.battery_mah == 9000 and spec.ip == "IP69K"


def test_news_feed_parsing_rss_and_atom():
    rss = ("<rss><channel><item><title>OPPO Find X9 Pro leak</title>"
           "<link>https://x/1</link><description>7500 mAh, IP69</description>"
           "</item></channel></rss>")
    atom = ('<feed><entry><title>Galaxy S26 Ultra tipped</title>'
            '<link href="https://y/2"/><summary>5x optical zoom</summary></entry></feed>')
    assert news.parse_feed(rss, "gsmarena")[0].url == "https://x/1"
    assert news.parse_feed(atom, "xda")[0].title == "Galaxy S26 Ultra tipped"


def test_news_separates_upcoming_from_reviews():
    upcoming_article = news.Article("gsmarena", "Xiaomi 18 Ultra launch date leaked", "u", "")
    review = news.Article("gsmarena", "Xiaomi 17 Ultra review", "r", "")
    assert news.looks_upcoming(upcoming_article)
    assert not news.looks_upcoming(review)


def test_specs_from_article_text_only_what_is_named():
    spec = upcoming._spec_from_text("The phone packs a 7800 mAh battery, IP69 rating, "
                                    "OIS and 5x optical zoom")
    assert (spec.battery_mah, spec.ip, spec.ois, spec.optical_zoom) == (7800, "IP69", True, 5.0)
    empty = upcoming._spec_from_text("New flagship with a huge battery and great camera")
    assert empty.battery_mah is None and empty.ois is None


def test_upcoming_store_marks_first_sighting(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    soon = Phone(key="oppo f35 pro", brand="OPPO", name="F35 Pro", spec=Spec(battery_mah=10000))
    soon.expected = "2026, August"
    assert store.save_upcoming(soon) is True
    assert store.save_upcoming(soon) is False
    assert store.get_upcoming("oppo f35 pro", recheck_days=7)["expected"] == "2026, August"
    assert store.get_upcoming("oppo f35 pro", recheck_days=0) is None
    store.close()


# ── вкладка «AliExpress» ──────────────────────────────────────────────────────
ALI_PAGE = ('{"mods":{"itemList":{"content":[' + ",".join([
    # подходит: год, батарея выше минимума, цена в бюджете
    '{"productId":"100500","title":{"displayTitle":"NOWY smartfon OnePlus Nord 6 '
    'na rok 2026, 12 + 512GB, bateria 7500mAh, IP69, aparat 50MP"},'
    '"prices":{"salePrice":{"minPrice":1949.0}},"image":{"imgUrl":"//img/a.jpg"}}',
    # без года в заголовке — не наш
    '{"productId":"100501","title":{"displayTitle":"Smartfon OnePlus Nord 5 '
    'bateria 8000mAh"},"prices":{"salePrice":{"minPrice":999.0}}}',
    # батарея ниже минимума
    '{"productId":"100502","title":{"displayTitle":"Smartfon Xiaomi Redmi 20 '
    '2026, bateria 5000mAh"},"prices":{"salePrice":{"minPrice":700.0}}}',
    # аксессуар
    '{"productId":"100503","title":{"displayTitle":"Etui do OnePlus Nord 6 2026 '
    '7500mAh"},"prices":{"salePrice":{"minPrice":30.0}}}',
]) + ']}}}')


class AliFetcher:
    def get(self, url, referer=None, fresh=False):
        assert "wholesale-" in url
        return ALI_PAGE


def test_aliexpress_parses_items_and_prices():
    items = aliexpress.parse_items(ALI_PAGE)
    assert len(items) == 4
    assert aliexpress._price(items[0]) == 1949


def test_aliexpress_keeps_only_this_year_and_fitting_specs(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    cfg = json_copy(CFG)
    cfg["aliexpress"]["queries"] = ["smartphone 2026"]
    found = aliexpress.collect(AliFetcher(), cfg, store)
    store.close()

    assert [p.key for p in found] == ["oneplus nord6"]
    phone = found[0]
    assert phone.spec.battery_mah == 7500 and phone.spec.ip == "IP69"
    assert phone.ali_price == 1949
    assert phone.ali_image == "https://img/a.jpg"
    assert phone.expected == "2026 г."


def test_aliexpress_prefers_store_specs_over_seller_words(tmp_path):
    """Если модель продаётся в Польше, верим таблице магазина, а не заголовку."""
    store = Store(tmp_path / "db.sqlite")
    store.save_spec("oneplus nord6", Spec(battery_mah=6800, ip="IP68", ois=True,
                                          optical_zoom=3.0, sources=["xkom"]))
    cfg = json_copy(CFG)
    cfg["aliexpress"]["queries"] = ["smartphone 2026"]
    found = aliexpress.collect(AliFetcher(), cfg, store)
    store.close()

    # 7500 из заголовка продавца проиграли 6800 из таблицы x-kom — и модель
    # выпала из выдачи, потому что 6800 меньше требуемого минимума
    assert found == []


def test_aliexpress_recognises_antibot_page():
    assert aliexpress.is_blocked('<html><script>window.location="/punish?x=1"</script>')
    assert not aliexpress.is_blocked(ALI_PAGE + " " * 25000)


# ── GSMArena по странице марки ────────────────────────────────────────────────
GSM_MAKERS_HTML = '<td><a href=doogee-phones-129.php>Doogee<br><span>162 devices</span></a></td>'
GSM_BRAND_HTML = ('<a href="doogee_s200-13094.php"><img src="x"><strong><span>S200</span>'
                  '</strong></a><a href="doogee_s300_pro-14000.php"><img src="x">'
                  '<strong><span>S300 Pro</span></strong></a>'
                  '<a href="doogee-phones-f-129-0-p2.php">2</a>')
GSM_DEVICE_HTML = ('<table><tr><th>Battery</th><td class="ttl"><a>Type</a></td>'
                   '<td class="nfo">Li-Po 10100 mAh</td></tr>'
                   '<tr><th>Main Camera</th><td class="ttl"><a>Triple</a></td>'
                   '<td class="nfo">100 MP, f/1.9, (wide), PDAF, OIS</td></tr>'
                   '<tr><th>Body</th><td class="ttl"><a>Protection</a></td>'
                   '<td class="nfo">IP68/IP69K</td></tr></table>')


class GsmFetcher:
    def __init__(self):
        self.calls = []

    def get(self, url, referer=None, fresh=False):
        self.calls.append(url)
        if url.endswith("makers.php3"):
            return GSM_MAKERS_HTML
        if "doogee-phones-129" in url:
            return GSM_BRAND_HTML
        if "doogee-phones-f-129-0-p2" in url:
            return ""                      # вторая страница пустая
        if "doogee_s200-13094" in url:
            return GSM_DEVICE_HTML
        raise AssertionError("лишний запрос: " + url)


def test_gsmarena_camera_row_without_ois_or_telephoto_is_a_fact():
    """«Кирпич» с Allegro: модули перечислены, OIS и телевика среди них нет."""
    spec = specs.build([("Main Camera Triple", "100 MP, f/1.9, (wide), PDAF; "
                                               "8 MP, (ultrawide); 20 MP, (night vision)")],
                       "gsmarena")
    assert spec.ois is False and spec.optical_zoom == 1.0
    flagship = specs.build([("Main Camera Triple", "50 MP, f/1.7, (wide), OIS; "
                                                   "50 MP, (periscope telephoto), 5x optical zoom, OIS")],
                           "gsmarena")
    assert flagship.ois is True and flagship.optical_zoom == 5.0


def test_gsmarena_brand_page_lookup(monkeypatch):
    monkeypatch.setattr(upcoming, "_gsm_makers", {})
    monkeypatch.setattr(upcoming, "_gsm_devices", {})
    fetcher = GsmFetcher()
    spec = upcoming.gsm_spec(fetcher, Phone(key="doogee s200", brand="Doogee", name="S200"))
    assert spec is not None
    assert (spec.battery_mah, spec.ip, spec.ois) == (10100, "IP69K", True)
    # второй Doogee не перечитывает ни марок, ни страницу марки
    fetcher.calls.clear()
    assert upcoming.gsm_spec(fetcher, Phone(key="doogee s999", brand="Doogee", name="S999")) is None
    assert fetcher.calls == []


# ── база ──────────────────────────────────────────────────────────────────────
def test_store_marks_new_then_price_drop(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    p = phone(full_spec(), price=4000)
    assert store.upsert(p)["is_new"] is True

    cheaper = phone(full_spec(), price=3700)
    meta = store.upsert(cheaper)
    assert meta["is_new"] is False and meta["price_drop"] == 300
    assert len(meta["history"]) == 2
    store.close()


def test_store_notices_model_becoming_suitable(tmp_path):
    """Магазин дозаполнил характеристики — для уведомления это новость."""
    store = Store(tmp_path / "db.sqlite")
    vague = phone(full_spec(battery_mah=None), reject_reasons=["батарея не указана"])
    store.upsert(vague)
    good = phone(full_spec())
    assert store.upsert(good)["newly_matched"] is True
    store.close()


def test_store_spec_gaps_are_filled_from_upcoming(tmp_path):
    """
    Модель переехала из «Скоро» в продажу — характеристики, выясненные по
    GSMArena, терять нельзя: таблица магазина бывает беднее (у Morele камера
    это одна строка «200 Mpix», без зума и стабилизации).
    """
    store = Store(tmp_path / "db.sqlite")
    rumour = Phone(key="xiaomi poco f9 ultra", brand="Xiaomi", name="POCO F9 Ultra",
                   spec=Spec(battery_mah=8050, ip="IP69", ois=True, optical_zoom=5.0,
                             sources=["gsmarena"]))
    store.save_upcoming(rumour)

    # то же самое, но из магазина: батарея и IP есть, камеры нет
    from_store = store.spec_from_upcoming("xiaomi poco f9 ultra")
    assert from_store.optical_zoom == 5.0 and from_store.ois is True

    shop = Spec(battery_mah=8050, ip="IP68", main_mp=200.0, sources=["morele"])
    shop.merge(from_store, "gsmarena")
    assert shop.ip == "IP68"            # значение магазина главнее
    assert shop.optical_zoom == 5.0     # а пробел закрыт тем, что знали раньше
    assert shop.ois is True
    assert "gsmarena" in shop.sources

    # слова продавца с AliExpress сюда не подмешиваются
    seller = Phone(key="oneplus nord6", brand="OnePlus", name="Nord 6",
                   spec=Spec(battery_mah=7500, sources=["aliexpress"]))
    store.save_upcoming(seller, key_prefix="ali:")
    assert store.spec_from_upcoming("oneplus nord6") is None
    store.close()


def test_kept_phones_round_trip(tmp_path):
    """Отчёт должен перерисовываться из базы без сети — значит и модель тоже."""
    store = Store(tmp_path / "db.sqlite")
    good = phone(full_spec(), price=4200)
    weak = phone(full_spec(battery_mah=4000), reject_reasons=["батарея"])
    weak.key = "other model"
    store.upsert(good)
    store.upsert(weak)

    kept = store.kept_phones()
    assert len(kept) == 1                        # отброшенная модель в отчёт не идёт
    restored = Phone.from_dict(kept[0])
    assert restored.spec.battery_mah == 8200
    assert restored.price == 4200
    assert restored.offers[0].source == "xkom"
    store.close()


def test_old_parser_version_invalidates_incomplete_cache(tmp_path):
    """Улучшение разбора должно доходить до моделей, разобранных раньше."""
    store = Store(tmp_path / "db.sqlite")
    store.save_spec("partial", Spec(battery_mah=8000))
    store.conn.execute("UPDATE specs SET version = 0 WHERE key = 'partial'")
    assert store.get_spec("partial", recheck_days=999) is None

    store.save_spec("full", full_spec())
    store.conn.execute("UPDATE specs SET version = 0 WHERE key = 'full'")
    # полную запись перечитывать незачем — в ней уже всё есть
    assert store.get_spec("full", recheck_days=999) is not None
    store.close()


def test_failed_lookup_is_remembered(tmp_path):
    """
    Иначе модель, которой нет ни в одном магазине с характеристиками, съедала
    бы лимит поиска в каждом прогоне, и до остальных очередь не дошла бы.
    """
    store = Store(tmp_path / "db.sqlite")
    assert not store.lookup_tried("hotwav w11", retry_after_days=14)
    store.save_lookup("hotwav w11", found=False)
    assert store.lookup_tried("hotwav w11", retry_after_days=14)
    # но не навсегда: магазины со временем начинают возить и это
    assert not store.lookup_tried("hotwav w11", retry_after_days=0)
    store.close()


def test_spec_cache_recheck_only_for_incomplete(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.save_spec("complete", full_spec())
    store.save_spec("partial", Spec(battery_mah=8000))
    assert store.get_spec("complete", recheck_days=0) is not None
    assert store.get_spec("partial", recheck_days=0) is None   # просрочено
    assert store.get_spec("partial", recheck_days=99) is not None
    store.close()


# ── отчёт ─────────────────────────────────────────────────────────────────────
def test_report_renders_self_contained_html(tmp_path):
    p = scoring.score(filters.apply(phone(full_spec()), CFG), CFG)
    path = report.render(tmp_path / "report.html", [(p, {"is_new": True, "price_drop": 0})],
                         42, CFG, {"cached": 1, "fetched": 2})
    html = path.read_text(encoding="utf-8")
    assert "__DATA__" not in html and "Galaxy Test" in html
    assert "http://" not in html.split("<script>")[0]     # без внешних ресурсов
