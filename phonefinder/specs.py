"""
Характеристики из карточек товара.

Каждый магазин отдаёт спецификацию по-своему (x-kom — JSON с ключами и
значениями, Morele — schema.org PropertyValue, MediaMarkt — GraphQL-фичи,
Komputronik — просто текст таблицы), но во всех случаях это пары
«польская подпись → значение». Разбор подписей общий и живёт здесь, а дело
источника — только достать пары. Так добавление магазина не требует нового
разбора характеристик.

Отдельная история — AI. Магазины почти никогда не пишут об этом в таблице
(исключение — Morele с «Funkcje AI»), поэтому кроме прямых упоминаний
работают правила по семейству модели из config.yaml: Galaxy S — Galaxy AI,
Pixel — Gemini, iPhone 15 Pro и новее — Apple Intelligence.
"""
from __future__ import annotations

import logging
import re

from .models import Spec, parse_float, parse_int

log = logging.getLogger(__name__)

# Версия разбора характеристик. Кеш в базе живёт долго (телефон не меняет
# батарею), поэтому улучшение парсера иначе не дошло бы до уже разобранных
# моделей: их запись считается свежей. Номер здесь нужно увеличивать, когда
# разбор начинает извлекать то, чего раньше не извлекал, — тогда прошлые
# записи считаются устаревшими и модели перечитываются.
#
# 2: «Zoom» отсутствует в таблице, а модули камеры подписаны и телеобъектива
#    среди них нет -> оптического зума нет (1×), а не «неизвестно».
# 3: то же для стабилизации: функции камеры перечислены, OIS среди них нет ->
#    стабилизации нет, а не «неизвестно».
# 4: английские подписи и значения (GSMArena) — «Battery», «Protection», «OIS»,
#    «5x optical zoom».
# 5: у GSMArena перечисленные модули камеры без «OIS» и без «telephoto» означают
#    «стабилизации нет» и «зума нет», а не «неизвестно».
PARSER_VERSION = 5

# ── подписи полей в польских магазинах ────────────────────────────────────────
# Английские подписи нужны для GSMArena: он единственный, кто пишет про ещё не
# вышедшие телефоны в виде таблицы характеристик, а не прозы (см. upcoming.py).
BATTERY_LABELS = ("pojemność baterii", "pojemność akumulatora", "pojemnosc baterii",
                  "bateria", "akumulator", "battery type", "battery capacity")
IP_LABELS = ("wodoszczelno", "wodoodporn", "pyłoszczelno", "pyloszczelno",
             "stopień ochrony", "stopien ochrony", "odporno", "szczelno",
             "certyfikat", "norma ip", "protection", "build")
OIS_LABELS = ("stabilizacja",)
CAMERA_FEATURE_LABELS = ("dodatkowe cechy aparatu", "cechy aparatu", "funkcje aparatu")
ZOOM_LABELS = ("zoom", "teleobiektyw", "powiększenie", "telephoto", "periscope")
CAMERA_LABELS = ("aparat", "kamera", "rozdzielczość aparatu", "obiektyw",
                 "main camera", "selfie camera", "modules")
AI_LABELS = ("funkcje ai", "sztuczna inteligencja", "asystent ai", "ai")
OS_LABELS = ("system operacyjny",)
RELEASE_LABELS = ("data premiery", "premiera", "rok produkcji")
CHARGE_LABELS = ("moc ładowania", "szybkie ładowanie", "ładowanie przewodowe")
SCREEN_LABELS = ("przekątna ekranu", "przekatna ekranu", "wielkość ekranu")
REFRESH_LABELS = ("częstotliwość odświeżania", "czestotliwosc odswiezania",
                  "odświeżanie")
WEIGHT_LABELS = ("waga",)

IP_RE = re.compile(r"\bIP\s?-?\s?(6[89])\s?(K?)\b", re.IGNORECASE)
MAH_RE = re.compile(r"(\d{3,6})\s*mah", re.IGNORECASE)
MP_RE = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*(?:mpix|mpx|mp\b|megapiks)", re.IGNORECASE)
ZOOM_X_RE = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*[-\s]?(?:x|×|krotny|krotnie)", re.IGNORECASE)
YES_RE = re.compile(r"^(tak|yes|jest|obsługuje|obsluguje|wbudowana|dostępn|dostepn|"
                    r"optyczna|ois|\+)", re.IGNORECASE)
NO_RE = re.compile(r"^(nie\b|brak|bez\b|-$|none)", re.IGNORECASE)
# «OIS» как отдельное слово: у GSMArena стабилизация написана внутри строки
# камеры («50 MP, f/1.8, 24mm (wide), PDAF, OIS»), а не отдельной подписью.
_OIS_EN_RE = re.compile(r"\bOIS\b")

# Названия AI-сервисов, как их пишут в польских магазинах и в описаниях.
AI_NAMES = [
    ("galaxy ai", "Galaxy AI"),
    ("apple intelligence", "Apple Intelligence"),
    ("gemini nano", "Gemini Nano"),
    ("gemini", "Gemini"),
    ("magic ai", "MagicOS AI"),
    ("magicos", "MagicOS AI"),
    ("hyperai", "HyperAI"),
    ("hyperos ai", "HyperOS AI"),
    ("xiaomi ai", "Xiaomi AI"),
    ("moto ai", "Moto AI"),
    ("motoai", "Moto AI"),
    ("copilot", "Copilot"),
    ("bixby", "Bixby"),
    ("aicore", "AICore"),
    ("circle to search", "Circle to Search"),
    ("perplexity", "Perplexity"),
    ("essential space", "Essential Space"),
    ("air command", "AI Air Command"),
    ("sztuczna inteligencja", "AI"),
    ("funkcje ai", "AI"),
    ("asystent ai", "AI"),
]

# Текстовый путь: для магазинов, где спецификация не размечена, а просто
# написана в таблице. Подпись здесь — якорь, значение — то, что за ней.
TEXT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Pojemność akumulatora", re.compile(
        r"Pojemno[śs][ćc] (?:akumulatora|baterii)\s*:?\s*(\d{3,6}\s*mAh)", re.IGNORECASE)),
    ("Wodoszczelność", re.compile(
        r"(?:Py[łl]oszczelno[śs][ćc] i wodoszczelno[śs][ćc]|Wodoszczelno[śs][ćc]|"
        r"Stopie[ńn] ochrony[^:]{0,30}|Odporno[śs][ćc][^:]{0,20})\s*:?\s*(IP\s?6[89]K?)",
        re.IGNORECASE)),
    ("Optyczna stabilizacja obrazu", re.compile(
        r"Optyczna stabilizacja obrazu(?:\s*\(OIS\))?\s*:?\s*(tak|nie)", re.IGNORECASE)),
    ("Zoom optyczny", re.compile(
        r"Zoom optyczny\s*:?\s*(\d{1,2}(?:[.,]\d)?\s*x)", re.IGNORECASE)),
    ("Aparat główny", re.compile(
        r"(?:Rozdzielczo[śs][ćc] aparatu(?: - ty[łl]| g[łl][óo]wnego)?|Aparat g[łl][óo]wny)"
        r"\s*:?\s*([\d,.\s+]{2,40}Mpix)", re.IGNORECASE)),
    ("System operacyjny", re.compile(
        r"System operacyjny\s*:?\s*([A-Za-z][\w .]{2,20})", re.IGNORECASE)),
    ("Moc ładowania", re.compile(
        r"Moc [łl]adowania\s*:?\s*(?:max\.?\s*)?(\d{1,3}\s*W)", re.IGNORECASE)),
    ("Przekątna ekranu", re.compile(
        r"Przek[ąa]tna ekranu\s*:?\s*(\d{1,2}[.,]?\d?)\s*[\"″']", re.IGNORECASE)),
    ("Częstotliwość odświeżania", re.compile(
        r"(?:Cz[ęe]stotliwo[śs][ćc] od[śs]wie[żz]ania|Od[śs]wie[żz]anie)[^:]{0,20}"
        r"\s*:?\s*(\d{2,3}\s*Hz)", re.IGNORECASE)),
    ("Waga", re.compile(r"Waga\s*:?\s*(\d{2,3}\s*g)\b", re.IGNORECASE)),
]
# Про AI по тексту страницы намеренно не ищем: названия сервисов попадаются
# в блоках «похожие товары» и в баннерах, и Redmi легко получал бы «Galaxy AI»
# от соседней карточки Samsung. AI берётся только из размеченной таблицы
# характеристик и из правил по модели (см. apply_ai_rules).


def strip_tags(html: str) -> str:
    """HTML -> плоский текст. Скрипты и стили выбрасываем целиком."""
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace(" ", " ")
    text = re.sub(r"&[a-z]{2,8};", " ", text)
    return re.sub(r"\s+", " ", text)


def pairs_from_text(text: str) -> list[tuple[str, str]]:
    """Пары «подпись → значение» из плоского текста таблицы характеристик."""
    pairs: list[tuple[str, str]] = []
    for label, pattern in TEXT_PATTERNS:
        for match in pattern.finditer(text):
            pairs.append((label, match.group(1).strip()))
    return pairs


def _has(label: str, needles: tuple[str, ...]) -> bool:
    return any(n in label for n in needles)


def build(pairs: list[tuple[str, str]], source: str) -> Spec:
    """Собирает `Spec` из пар «подпись → значение» одного магазина."""
    spec = Spec(sources=[source])
    mp_main: float | None = None
    mp_tele: float | None = None
    mp_front: float | None = None
    # Подписаны ли модули камеры («- ultraszerokokątny», «- teleobiektyw»).
    # Если подписаны, отсутствие телеобъектива в списке — это факт, а не пробел.
    labeled_modules = False
    # Перечислял ли магазин функции камеры («Dodatkowe cechy aparatu»,
    # «Funkcje aparatu»). Тогда отсутствие в списке OIS — тоже факт.
    camera_features = False

    for raw_label, raw_value in pairs:
        label = (raw_label or "").strip().lower()
        value = (raw_value or "").strip()
        if not value:
            continue
        low = value.lower()

        if spec.battery_mah is None and _has(label, BATTERY_LABELS):
            match = MAH_RE.search(value)
            number = parse_int(match.group(1)) if match else None
            # «Bateria: Li-Ion» и прочее без числа — не характеристика ёмкости.
            if number and 1000 <= number <= 30000:
                spec.battery_mah = number

        if spec.ip is None:
            # «Stopień ochrony IP: IP68, IP69, MIL-STD-810H» у Ceneo — берём
            # старший класс, иначе телефон с IP69K числился бы как IP68.
            found = IP_RE.findall(value)
            if found and (_has(label, IP_LABELS) or "ip6" in low.replace(" ", "")):
                digit, suffix = max(found, key=lambda m: (m[0], m[1]))
                spec.ip = f"IP{digit}{suffix.upper()}"

        if _has(label, CAMERA_FEATURE_LABELS):
            camera_features = True

        if _has(label, OIS_LABELS) or "stabilizacja" in low or _OIS_EN_RE.search(value):
            if "optyczn" in low or _OIS_EN_RE.search(value):
                spec.ois = True
            elif YES_RE.match(low) and "optyczn" in label:
                spec.ois = True
            elif NO_RE.match(low) and spec.ois is None and "optyczn" in label:
                spec.ois = False

        # Зум считаем только оптический. «10x zoom cyfrowy» — это кроп, а
        # «8x zoom jakości optycznej» у Apple — тот же кроп, названный красиво:
        # оба попадают в строку рядом с настоящей кратностью и оба врут вверх.
        optical = ("optyczn" in low or "optyczn" in label
                   or "optical zoom" in low or "optical" in low and "zoom" in low)
        if (( _has(label, ZOOM_LABELS) or "optical zoom" in low) and optical
                and not any(bad in low for bad in ("cyfrow", "jako", "digital",
                                                   "hybryd", "hybrid"))):
            match = ZOOM_X_RE.search(value)
            zoom = parse_float(match.group(1)) if match else None
            if zoom and 1 < zoom <= 30:
                spec.optical_zoom = max(spec.optical_zoom or 0, zoom)

        if _has(label, CAMERA_LABELS) and MP_RE.search(value):
            # «50 + 12 + 10 MPix» у Morele — три модуля с одной единицей на
            # конце, поэтому число ищется не только вплотную к «Mpix».
            values = [parse_float(m) for m in re.findall(r"\d{1,3}(?:[.,]\d)?", value)]
            values = [v for v in values if v and v <= 250]
            if any(mark in low for mark in ("ultraszerok", "teleobiektyw", "peryskop",
                                            "szerokok", "makro", "ultrawide", "telephoto",
                                            "periscope", "(wide)", "macro")):
                labeled_modules = True
                # У GSMArena строка «Main Camera» перечисляет каждый модуль с его
                # ролью и пишет «OIS», когда он есть. Если модули перечислены, а
                # OIS не упомянут — стабилизации нет, и это факт, а не пробел.
                camera_features = True
            if values:
                if "przód" in label or "przedni" in label or "przod" in label:
                    mp_front = max([mp_front or 0] + values) or None
                elif any(t in low for t in ("teleobiektyw", "peryskop", "telephoto", "periscope")):
                    mp_tele = max([mp_tele or 0] + values) or None
                else:
                    mp_main = max([mp_main or 0] + values) or None

        for needle, name in AI_NAMES:
            if needle in low or (needle in label and needle != "ai"):
                if name not in spec.ai:
                    spec.ai.append(name)

        if spec.os is None and _has(label, OS_LABELS):
            spec.os = value[:40]
        if spec.release is None and _has(label, RELEASE_LABELS):
            spec.release = value[:30]
        if spec.charge_w is None and _has(label, CHARGE_LABELS):
            watts = parse_int(value)
            if watts and 5 <= watts <= 400:
                spec.charge_w = watts
        if spec.screen_in is None and _has(label, SCREEN_LABELS):
            inches = parse_float(value)
            if inches and 3 <= inches <= 10:
                spec.screen_in = inches
        if spec.refresh_hz is None and _has(label, REFRESH_LABELS):
            hz = parse_int(value)
            if hz and 30 <= hz <= 240:
                spec.refresh_hz = hz
        if spec.weight_g is None and label.strip() in WEIGHT_LABELS:
            grams = parse_int(value)
            if grams and 80 <= grams <= 500:
                spec.weight_g = grams

    spec.main_mp = mp_main
    spec.tele_mp = mp_tele
    spec.front_mp = mp_front

    # Строки «Zoom» в таблице нет — но это не всегда «неизвестно». Если магазин
    # перечислил модули камеры и подписал их («50 Mpix», «12 Mpix -
    # ultraszerokokątny»), а телеобъектива среди них не оказалось, то оптики
    # у телефона просто нет. Разница существенная: «не указан» отправляет
    # модель в отказ с формулировкой «магазин не написал», а 1× — с честной
    # «телеобъектива нет», и её видно в отчёте.
    if spec.optical_zoom is None and labeled_modules and mp_tele is None:
        spec.optical_zoom = 1.0
    # То же со стабилизацией: магазин перечислил функции камеры, OIS среди них
    # нет — значит её нет. Иначе «не указана» смешивала бы два разных случая:
    # телефон без стабилизации и телефон, чью карточку мы ещё не открыли.
    if spec.ois is None and camera_features:
        spec.ois = False

    _tidy_ai(spec)
    # Телеобъектив — сам по себе доказательство оптического зума: если в таблице
    # есть «10 Mpix - teleobiektyw», но нет строки с кратностью, зум всё равно
    # есть, просто неизвестно какой. Ставить число наугад нельзя, поэтому
    # оставляем None и полагаемся на другой магазин.
    return spec


def _tidy_ai(spec: Spec) -> None:
    """
    Убирает безымянное «AI», когда сервис уже назван.

    «Funkcje AI: Galaxy AI» в таблице Morele даёт совпадение и по названию
    сервиса, и по самой подписи — в отчёте это выглядело бы как «Galaxy AI · AI».
    """
    if len(spec.ai) > 1 and "AI" in spec.ai:
        spec.ai.remove("AI")


def apply_ai_rules(spec: Spec, phone_title: str, cfg: dict) -> None:
    """
    Дополняет список AI-сервисов по правилам из config.yaml.

    Нужно потому, что таблица характеристик про AI молчит почти всегда, а
    критерий у пользователя жёсткий. Правило — это регулярное выражение по
    «марка модель» и название сервиса, которое оно даёт.
    """
    title = phone_title.lower()
    for pattern, name in (cfg.get("ai_rules") or {}).items():
        try:
            if re.search(pattern, title, re.IGNORECASE) and name not in spec.ai:
                spec.ai.append(name)
        except re.error as exc:
            log.warning("плохое правило AI %r: %s", pattern, exc)
    _tidy_ai(spec)


def from_html(html: str, source: str, labeled: list[tuple[str, str]] | None = None) -> Spec:
    """
    Общий путь: размеченные пары от источника, дальше добор по тексту страницы.

    Именно двумя проходами, а не одним списком пар: текст страницы — источник
    заведомо более грязный (маркетинговые абзацы, блоки «похожие товары»), и
    он должен только закрывать пропуски, а не спорить с таблицей. Один проход
    этого не даёт: для полей, которые собираются по всем парам сразу (зум,
    камеры), текст переписывал бы разобранное значение.
    """
    spec = build(list(labeled or []), source)
    if labeled:
        spec.merge(build(pairs_from_text(strip_tags(html)), source), source)
        return spec
    return build(pairs_from_text(strip_tags(html)), source)
