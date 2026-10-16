"""
Предложение магазина, характеристики модели и сама модель.

Ключевая мысль всего приложения: искать нужно не предложения, а *модели*.
Один и тот же телефон лежит в шести источниках под шестью разными названиями и
в десятке вариантов памяти и цвета, а критерии («батарея, зум, IP68») относятся
к модели, а не к предложению. Поэтому заголовок магазина разбирается на
«марка + модель» и «вариант» (память, цвет), характеристики выясняются один раз
на модель, а цены собираются по всем предложениям этой модели.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# Марки, которые встречаются в польской рознице. Нужны, чтобы отделить марку
# от названия модели в свободном заголовке магазина.
BRANDS = {
    "apple": "Apple", "iphone": "Apple", "samsung": "Samsung", "xiaomi": "Xiaomi",
    "redmi": "Xiaomi", "poco": "Xiaomi", "google": "Google", "pixel": "Google",
    "motorola": "Motorola", "moto": "Motorola", "honor": "HONOR", "huawei": "Huawei",
    "oppo": "OPPO", "oneplus": "OnePlus", "realme": "realme", "vivo": "vivo",
    "nothing": "Nothing", "sony": "Sony", "asus": "ASUS", "zte": "ZTE",
    "nubia": "nubia", "tcl": "TCL", "nokia": "Nokia", "hmd": "HMD",
    "fairphone": "Fairphone", "ulefone": "Ulefone", "doogee": "Doogee",
    "oukitel": "Oukitel", "cubot": "Cubot", "blackview": "Blackview",
    "myphone": "myPhone", "cat": "CAT", "infinix": "Infinix", "tecno": "TECNO",
    "iiif150": "IIIF150", "hotwav": "HOTWAV", "agm": "AGM", "gigaset": "Gigaset",
    "energizer": "Energizer", "umidigi": "UMIDIGI", "hammer": "Hammer",
}

# Подмарки: часть названия модели, а не марка. Магазины пишут и «POCO M8»,
# и «Xiaomi POCO M8» — ключ модели должен получаться одинаковым, поэтому такой
# токен из названия не выбрасывается, а марка берётся родительская.
SUB_BRANDS = {"iphone", "redmi", "poco", "pixel", "moto"}

# Аксессуары в категориях смартфонов — их больше, чем самих телефонов,
# особенно на Allegro. Без этого списка выдача превращается в чехлы и стёкла.
ACCESSORY_RE = re.compile(
    r"\b(etui|case\b|obudowa|pokrowiec|futera[łl]|szk[łl]o|folia|hydrogel|"
    r"[łl]adowarka|kabel|przew[óo]d|adapter|powerbank|power bank|s[łl]uchawki|"
    r"uchwyt|statyw|selfie ?stick|opaska|pasek|smartwatch|zegarek|tablet|"
    r"laptop|karta ?(?:sim|pami[ęe]ci)|starter|zestaw nag[łl]o[śs]|g[łl]o[śs]nik|"
    r"rysik|stylus|nak[łl]adka|zaślepka|ramka|szybka|wy[śs]wietlacz zamienny|"
    r"bateria zamienna|serwis|naprawa|atrapa|makieta|dummy|stojak|podstawka|"
    r"ochronne|ochronna|ochronny|do (?:telefonu|smartfona)|kompatybilne)\b",
    re.IGNORECASE,
)

# Признаки не-нового товара. Влияет на цену настолько, что смешивать нельзя.
USED_RE = re.compile(
    r"\b(u[żz]ywany|u[żz]ywana|poleasingowy|odnowiony|refurbished|renewed|"
    r"outlet|powystawowy|klasa [ab]\b|jak nowy|second ?hand|uszkodzony|"
    r"na cz[ęe][śs]ci|bez blokad|do naprawy)\b",
    re.IGNORECASE,
)

# «12/512GB», «16 GB/ 1 TB», «8 GB / 256 GB» — сначала пара, потом одиночный объём.
MEMORY_RE = re.compile(
    r"\b(\d{1,2})\s*(?:gb)?\s*(?:/|\+)\s*(\d{1,4})\s*(gb|tb)?\b"
    r"|\b(\d{1,4})\s*(gb|tb)\b",
    re.IGNORECASE,
)

# Шум в заголовках: сетевые стандарты, слоты SIM, частота экрана, служебные метки.
NOISE_RE = re.compile(
    r"\b(5g|4g|lte|3g|dual ?sim|ds\b|single ?sim|nfc|esim|"
    r"\d{2,3} ?hz|new\b|nowy\b|pl\b|eu\b|smartfon|smartphone|telefon|"
    r"od [śs]wie[żz]y|orygina[łl]|gwarancja|rat[ay]|promocja|zestaw)\b",
    re.IGNORECASE,
)

# Слова, которые бывают внутри названия модели после её номера: линейки,
# приставки, короткие индексы. Всё остальное после номера — цвет, прозвище
# («Sky Teal», «Gibraltar Sea»), прилагательное продавца — и к модели не
# относится. Список общий для магазинов и заголовков новостей.
MODEL_WORDS = re.compile(
    r"^(pro|ultra|plus|max|lite|mini|fe|air|fold|flip|note|edge|reno|find|magic|"
    r"nord|pixel|galaxy|redmi|poco|iphone|mate|nova|axon|zenfone|rog|neo|turbo|"
    r"power|armor|blade|tank|king|kong|xl|ce|gt|se|vip|thermal|energy|"
    r"[cemrstx]|xs|ts|rs|gs|ls|[a-z]{0,3}\d+[a-z]*)$", re.IGNORECASE)
# Числа с единицами — характеристики, а не часть названия: «10100 mAh»,
# «108 MP», «120 W». Из заголовка их надо убрать до разбора, иначе «10100»
# принимается за индекс модели.
UNITS_RE = re.compile(r"\b\d{1,6}(?:[.,]\d+)?\s?(?:mAh|MP|Mpix|Mpx|W|Hz|nits|GHz)\b",
                      re.IGNORECASE)

# Приписки продавцов. Отдельно от NOISE_RE, потому что их надо убрать ДО
# разбора: «Pancerny Smartfon Oukitel WP55» начинается с них, а у «DOOGEE V40
# czerwony 12 GB RAM» цвет стоит перед памятью, где его раньше не искали.
SELLER_WORDS_RE = re.compile(
    r"\b(pancerny|wodoodporny|wodoszczelny|wytrzyma[łl]y|odporny|termowizja|"
    r"nowo[śs][ćc]|nowy|new|global|wersja globalna|smartfon|smartphone|telefon|"
    r"\d+\s?[x×](?=\s|$))\b", re.IGNORECASE)
# Диагональ экрана в заголовке: «6,6"», «6.79"», «6,7 cala»
SCREEN_RE = re.compile(r"\b\d{1,2}[.,]\d{1,2}\s*(?:\"|″|''|cala|cali)", re.IGNORECASE)

# Цвета — по ним обрезается «хвост» заголовка, когда объёма памяти в нём нет.
COLOR_WORDS = {
    "czarny", "czarna", "czarne", "bia[łl]y", "bia[łl]a", "niebieski", "niebieska",
    "granatowy", "zielony", "zielona", "szary", "szara", "srebrny", "srebrna",
    "z[łl]oty", "z[łl]ota", "r[óo][żz]owy", "fioletowy", "[żz][óo][łl]ty",
    "czerwony", "br[ąa]zowy", "be[żz]owy", "mi[ęe]towy", "grafitowy", "tytanowy",
    "lawendowy", "turkusowy", "kremowy", "morski", "purpurowy", "koralowy",
    "piaskowy", "khaki", "oliwkowy", "black", "white", "blue", "green", "gray",
    "grey", "silver", "gold", "titanium", "midnight", "starlight", "graphite",
    "lavender", "mint", "navy", "sand", "obsidian", "ivory", "cream",
}
COLOR_RE = re.compile(r"\b(" + "|".join(COLOR_WORDS) + r")\b", re.IGNORECASE)

SOURCE_LABEL = {
    "allegro": "Allegro",
    "ceneo": "Ceneo",
    "xkom": "x-kom",
    "morele": "Morele",
    "mediamarkt": "MediaMarkt",
    "komputronik": "Komputronik",
}


def parse_int(value: Any) -> int | None:
    """
    '2 949,00 zł' / '4283.99' / '4000 mAh' -> int (целая часть).

    Дробную часть надо отрезать именно как дробную: цена приходит и «4 283,99»,
    и «4283.99», и если просто выбросить все не-цифры, выйдет 428399 злотых.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d[\d\s.,\u00a0]*", str(value))
    if not match:
        return None
    raw = re.sub(r"[\s\u00a0]", "", match.group(0)).rstrip(".,")
    raw = re.sub(r"[.,](\d{1,2})$", "", raw)      # «,99» / «.99» — это гроши
    digits = re.sub(r"[^\d]", "", raw)
    return int(digits) if digits else None


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:[.,]\d+)?", str(value).replace(" ", " "))
    return float(match.group(0).replace(",", ".")) if match else None


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canon(text: str) -> str:
    """Ключ модели: только буквы, цифры и одиночные пробелы."""
    text = text.lower().replace(" ", " ")
    text = re.sub(r"[^\w\s+]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _drop_codes(text: str) -> str:
    """
    Убирает из заголовка код производителя в скобках, оставляя всё остальное.

    Выбрасывать скобки целиком нельзя: «(SM-S931BDB)» и «(LTE)» — мусор, но
    «Nothing Phone (3a)» без скобок превращается в «Nothing Phone», и три
    разные модели склеиваются в одну.
    """
    def repl(match: re.Match) -> str:
        inner = match.group(1).strip()
        if inner.lower() in ("lte", "4g", "5g", "nowy", "new", "ds", "dual sim"):
            return " "
        code = (len(inner) >= 4
                and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/. ]{3,}", inner)
                and any(c.isdigit() for c in inner)
                and any(c.isalpha() for c in inner))
        return " " if code else f" {inner} "

    return re.sub(r"\(([^)]*)\)", repl, text)


def _trim_after_model_number(tokens: list[str], words: list[str]) -> tuple[list, list]:
    """
    Отрезает всё, что идёт после номера модели и не похоже на часть названия.

    «Redmi Note 17 Sky Teal» -> «Redmi Note 17», «Armor 26 Ultra Walkie-Talkie»
    -> «Armor 26 Ultra». До номера ничего не трогаем: «Nothing Phone 3a» и
    «King Kong 11» состоят из обычных слов, и их нельзя принимать за шум.
    Маркетинговые цвета бесконечны, словарём их не перечислить, — зато слова,
    законные после номера, перечислить можно (см. MODEL_WORDS).
    """
    seen_number = False
    keep = 0
    for index, token in enumerate(tokens):
        if seen_number and not MODEL_WORDS.match(token):
            break
        keep = index + 1
        if any(c.isdigit() for c in token):
            seen_number = True
    return tokens[:keep], words[:keep]


def parse_title(title: str) -> dict:
    """
    Разбирает заголовок магазина на марку, модель и вариант.

    Правило, которое работает во всех источниках: объём памяти в заголовке
    стоит ровно между названием модели и вариантом («Samsung Galaxy S25
    *12/128GB* Zielony»). Поэтому режем по нему: слева — модель, справа —
    цвет и прочие приписки. Если объёма нет, режем по первому слову-цвету.
    """
    raw = title.replace(" ", " ").strip()
    head = _drop_codes(raw)
    # «5 G» с пробелом и диагональ экрана — до всего остального: иначе «5» и
    # «6 6» становятся «номерами модели».
    head = re.sub(r"\b(\d)\s+g\b", r"\1g", head, flags=re.IGNORECASE)
    head = SCREEN_RE.sub(" ", head)
    head = UNITS_RE.sub(" ", head)
    head = SELLER_WORDS_RE.sub(" ", head)

    ram_gb = storage_gb = None
    memory = MEMORY_RE.search(head)
    if memory:
        if memory.group(1):
            ram_gb = int(memory.group(1))
            storage_gb = int(memory.group(2)) * (
                1024 if (memory.group(3) or "").lower() == "tb" else 1
            )
        else:
            storage_gb = int(memory.group(4)) * (
                1024 if (memory.group(5) or "").lower() == "tb" else 1
            )
        head = head[: memory.start()]
    # Цвет режем всегда, а не только без объёма памяти: у Allegro он бывает
    # и перед памятью («DOOGEE V40 czerwony 12 GB RAM 512 GB»).
    color = COLOR_RE.search(head)
    if color:
        head = head[: color.start()]

    head = NOISE_RE.sub(" ", head)
    # Ключ — из приведённой формы, показываем — исходную: «iPhone 17 Pro»
    # читается лучше, чем «iphone 17 pro», а ключом всё равно служит canon.
    words = [w for w in re.split(r"[\s/|,]+", head) if canon(w)]
    tokens = canon(head).split()

    brand = alias = None
    for token in tokens:
        if token in BRANDS:
            brand, alias = BRANDS[token], token
            break
    # Имя марки из названия убираем, подмарку («iPhone», «Redmi», «POCO») —
    # оставляем: без неё «Xiaomi 15» и «Redmi 15» слились бы в одну модель.
    if alias and alias not in SUB_BRANDS:
        tokens = [t for t in tokens if t != alias]
        words = [w for w in words if canon(w) != alias]
    tokens, words = _trim_after_model_number(tokens, words)
    model = " ".join(words).strip(" -–—")

    variant_bits = []
    if ram_gb:
        variant_bits.append(f"{ram_gb}/{storage_gb} GB" if storage_gb else f"{ram_gb} GB")
    elif storage_gb:
        variant_bits.append(f"{storage_gb} GB")

    # Пробел между буквами и цифрами в ключе убираем: «Reno15 Pro» и
    # «Reno 15 Pro» — один телефон, и магазины пишут его и так, и так
    # (то же с «Magic8»/«Magic 8», «iPhone17»/«iPhone 17»).
    key = re.sub(r"(?<=[^\W\d])\s+(?=\d)", "",
                 canon(f"{brand or ''} {' '.join(tokens)}") or canon(raw))
    return {
        "brand": brand,
        "model": model,
        "key": key,
        "variant": " · ".join(variant_bits) or None,
        "ram_gb": ram_gb,
        "storage_gb": storage_gb,
    }


def is_accessory(title: str) -> bool:
    return bool(ACCESSORY_RE.search(title))


def looks_used(title: str) -> bool:
    return bool(USED_RE.search(title))


@dataclass
class Offer:
    """Одно предложение в одном магазине."""

    source: str
    ext_id: str
    url: str
    title: str
    price: int | None = None
    currency: str = "PLN"
    image: str | None = None
    seller: str | None = None
    in_stock: bool | None = None
    used: bool = False
    # Нижняя граница ёмкости, гарантированная самим источником: если выдача
    # запрошена с его фильтром по батарее, всё в ней эту границу проходит.
    battery_min_guaranteed: int | None = None

    # Разбор заголовка
    brand: str | None = None
    model: str | None = None
    model_key: str = ""
    variant: str | None = None
    ram_gb: int | None = None
    storage_gb: int | None = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.ext_id}"

    @property
    def store(self) -> str:
        return SOURCE_LABEL.get(self.source, self.source)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Spec:
    """
    Характеристики модели, собранные из карточек товара.

    Ни один магазин не заполняет таблицу целиком: у x-kom есть зум и OIS,
    у Morele — «Funkcje AI», у MediaMarkt — только базовое. Поэтому спека
    собирается объединением: пустое поле заполняется из следующего источника,
    а `sources` помнит, кто что дал.
    """

    battery_mah: int | None = None
    # «Известно, что не меньше»: из фильтра источника, когда точного числа нет
    # ни в одной таблице характеристик (см. Offer.battery_min_guaranteed).
    battery_min: int | None = None
    ip: str | None = None                 # «IP68», «IP69K»
    ois: bool | None = None
    optical_zoom: float | None = None
    main_mp: float | None = None
    tele_mp: float | None = None
    front_mp: float | None = None
    ai: list[str] = field(default_factory=list)
    os: str | None = None
    release: str | None = None
    charge_w: int | None = None
    screen_in: float | None = None
    refresh_hz: int | None = None
    weight_g: int | None = None
    sources: list[str] = field(default_factory=list)

    # Поля, которые не удалось выяснить ни в одном магазине. Отдельно от None:
    # «неизвестно» и «нет» ведут к разным решениям (см. filters).
    def missing(self) -> list[str]:
        gaps = []
        if self.battery_mah is None and self.battery_min is None:
            gaps.append("battery")
        if self.ip is None:
            gaps.append("ip")
        if self.ois is None:
            gaps.append("ois")
        if self.optical_zoom is None:
            gaps.append("zoom")
        if not self.ai:
            gaps.append("ai")
        return gaps

    def merge(self, other: "Spec", source: str) -> None:
        """Заполняет пустые поля значениями из другого источника."""
        for name in ("battery_mah", "battery_min", "ip", "ois", "optical_zoom",
                     "main_mp", "tele_mp", "front_mp", "os", "release", "charge_w",
                     "screen_in", "refresh_hz", "weight_g"):
            if getattr(self, name) in (None, "") and getattr(other, name) not in (None, ""):
                setattr(self, name, getattr(other, name))
        for item in other.ai:
            if item not in self.ai:
                self.ai.append(item)
        if source not in self.sources:
            self.sources.append(source)

    @property
    def ip_level(self) -> int:
        """
        Числовой уровень защиты для сравнения: IP68 -> 68, IP69K -> 69.

        Строковое сравнение тут не годится: у «IP68/IP69K» и «IP69K» разный
        текст при одинаковом смысле.
        """
        if not self.ip:
            return 0
        levels = [int(m) for m in re.findall(r"IP6(\d)", self.ip.upper())]
        return 60 + max(levels) if levels else 0

    @property
    def ip69k(self) -> bool:
        return "69" in (self.ip or "")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Spec":
        known = {f: data.get(f) for f in cls.__dataclass_fields__ if f in data}
        known["ai"] = list(known.get("ai") or [])
        known["sources"] = list(known.get("sources") or [])
        return cls(**known)


@dataclass
class Phone:
    """Модель телефона со всеми предложениями по ней."""

    key: str
    brand: str | None
    name: str
    spec: Spec = field(default_factory=Spec)
    offers: list[Offer] = field(default_factory=list)

    score: int = 0
    score_parts: dict[str, float] = field(default_factory=dict)
    reject_reasons: list[str] = field(default_factory=list)
    near_miss: bool = False
    near_miss_reasons: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)   # чего не знаем о модели

    # Вкладка «Скоро»: телефон ещё не в продаже
    upcoming: bool = False
    expected: str | None = None          # срок словами источника: «2026, Q2»
    expected_by: Any = None              # он же датой — для сортировки и окна
    articles: list[dict] = field(default_factory=list)   # откуда узнали

    # Вкладка «AliExpress»: своя цена и своё изображение — магазинных нет
    ali_price: int | None = None
    ali_image: str | None = None
    ali_title: str | None = None

    @property
    def title(self) -> str:
        return " ".join(p for p in (self.brand, self.name) if p) or self.key

    @property
    def new_offers(self) -> list[Offer]:
        return [o for o in self.offers if not o.used]

    @property
    def best(self) -> Offer | None:
        """Самое дешёвое новое предложение — то, по которому и стоит покупать."""
        priced = [o for o in self.new_offers if o.price]
        return min(priced, key=lambda o: o.price) if priced else None

    @property
    def price(self) -> int | None:
        best = self.best
        return best.price if best else None

    @property
    def stores(self) -> list[str]:
        seen: list[str] = []
        for offer in self.new_offers:
            if offer.store not in seen:
                seen.append(offer.store)
        return seen

    @property
    def image(self) -> str | None:
        for offer in sorted(self.offers, key=lambda o: o.source != "xkom"):
            if offer.image:
                return offer.image
        return None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["price"] = self.price
        data["stores"] = self.stores
        data["expected_by"] = self.expected_by.isoformat() if self.expected_by else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Phone":
        """Обратно из того, что лежит в базе — для перерисовки отчёта без сети."""
        offers = [
            Offer(**{k: v for k, v in raw.items() if k in Offer.__dataclass_fields__})
            for raw in data.get("offers") or []
        ]
        known = {
            k: v for k, v in data.items()
            if k in cls.__dataclass_fields__ and k not in ("spec", "offers")
        }
        return cls(spec=Spec.from_dict(data.get("spec") or {}), offers=offers, **known)
