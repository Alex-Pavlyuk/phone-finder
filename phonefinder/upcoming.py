"""
Вкладка «Скоро»: телефоны, которых ещё нет в продаже.

Две разные по природе части.

1. **GSMArena, раздел «Rumored».** Единственное место, где про невышедшую
   модель есть не проза, а таблица характеристик — та же по форме, что в
   магазинах («подпись → значение»), поэтому её разбирает общий `specs.py`.
   Отсюда берутся батарея, IP, OIS и кратность зума.

2. **Ленты новостных сайтов** (см. `news.py`). Они дают то, чего нет у
   GSMArena: широту (модель, о которой пишут все, но карточки ещё нет) и срок
   («debiut w marcu», «launching February 25»). Характеристики из статьи берутся
   только те, что названы прямо, — числа из заголовков вроде «7500 mAh».

Критерии применяются те же, что и к магазинам, но с одной поправкой: у слуха
характеристики почти всегда неполные, и жёсткий отказ за «OIS не подтверждён»
оставил бы вкладку пустой. Поэтому здесь неизвестность не отбрасывает модель —
она показывается с явной пометкой, чего про телефон ещё не сказали. Отбрасывает
только прямое противоречие: названная батарея меньше требуемой, IP67 вместо
IP68 и так далее.
"""
from __future__ import annotations

import logging
import re
from datetime import date

from . import news, specs
from .models import (BRANDS, MODEL_WORDS, SCREEN_RE, SUB_BRANDS, UNITS_RE, Phone,
                     Spec, canon, parse_title)
from .models import _drop_codes

log = logging.getLogger(__name__)

GSM_BASE = "https://www.gsmarena.com"
GSM_RUMORED = f"{GSM_BASE}/rumored.php3"
GSM_LINK_RE = re.compile(
    r'<a href="([a-z0-9_\-]+\.php)"[^>]*>\s*<img[^>]*>\s*<strong><span>(.*?)</span>', re.S)
GSM_SECTION_RE = re.compile(
    r'<th[^>]*(?:rowspan="\d+")?[^>]*>(.*?)</th>|'
    r'<td class="ttl">(?:<a[^>]*>)?(.*?)(?:</a>)?</td>\s*<td class="nfo"[^>]*>(.*?)</td>',
    re.S)
GSM_NAME_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S)
# В списке слухов GSMArena не только телефоны: планшеты и часы нам не нужны,
# а по характеристикам они как раз проходят (у планшета батарея 11600 мАч).
NOT_A_PHONE_RE = re.compile(r"\b(tab|pad|watch|band|book|tv|buds)\b", re.IGNORECASE)

# «Exp. release 2026, Q2» / «Coming soon. Exp. release 2026, February»
EXPECTED_RE = re.compile(
    r"Exp(?:ected)?\.? release\s*([A-Za-z0-9, ]{4,24})", re.IGNORECASE)
QUARTER_RE = re.compile(r"\bQ([1-4])\s*,?\s*(20\d\d)|\b(20\d\d)\s*,?\s*Q([1-4])")
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "stycz": 1, "lut": 2, "marc": 3, "marz": 3, "kwie": 4, "maj": 5, "czerw": 6,
    "lip": 7, "sierp": 8, "wrze": 9, "paździ": 10, "pazdzi": 10, "listopad": 11,
    "grud": 12,
}
MONTH_RE = re.compile(
    r"\b(" + "|".join(MONTHS) + r")[a-ząćęłńóśźż]*\.?\s*(20\d\d)?", re.IGNORECASE)


def model_from_headline(title: str) -> dict | None:
    """
    Выделяет из заголовка статьи название модели.

    Заголовок — это предложение, а не название товара, и кормить его целиком
    разбором заголовков магазина нельзя: из «Google Deals: Pixel 11 series is
    up to $200 off» получалась «модель» со словом Deals. Поэтому ищем марку,
    а за ней — только те слова, которые бывают в названии телефона, и требуем
    среди них цифру: у моделей она есть почти всегда, у случайных фраз нет.
    """
    cleaned = UNITS_RE.sub(" ", SCREEN_RE.sub(" ", _drop_codes(title)))
    tokens = canon(cleaned).split()
    for index, token in enumerate(tokens):
        brand = BRANDS.get(token)
        if not brand:
            continue
        # Если совпала подмарка («Pixel», «Redmi»), она часть названия модели,
        # а маркой становится родительская — тот же ключ, что у магазинов.
        model = [token] if token in SUB_BRANDS else []
        for word in tokens[index + 1:index + 5]:
            if not MODEL_WORDS.match(word):
                break
            model.append(word)
        if not model or not any(any(c.isdigit() for c in w) for w in model):
            continue
        key = canon(f"{brand} {' '.join(model)}")
        return {
            "brand": brand,
            "model": " ".join(model),
            "key": re.sub(r"(?<=[^\W\d])\s+(?=\d)", "", key),
        }
    return None


# ── GSMArena как источник характеристик для моделей, которых нет в магазинах ─
# Поиск GSMArena закрыт проверкой Cloudflare Turnstile, зато страницы марок
# открыты: у каждой марки список устройств, новые сверху, с той же разметкой,
# что и у списка слухов. Так находится карточка Doogee, Oukitel или HOTWAV,
# которых не возит ни один польский магазин, — а в карточке GSMArena есть
# и OIS, и кратность зума.
GSM_MAKERS = f"{GSM_BASE}/makers.php3"
GSM_MAKER_RE = re.compile(r'href=["\']?([a-z0-9_]+-phones-\d+\.php)["\']?>([^<]+)<br>')
GSM_PAGE_RE = re.compile(r'href="([a-z0-9_]+-phones-f-\d+-0-p(\d+)\.php)"')
# Названия марок у GSMArena и у нас расходятся не везде, но где расходятся —
# это стоит карточки: подмарки Xiaomi в GSMArena лежат под самой Xiaomi.
GSM_BRAND_ALIAS = {"poco": "xiaomi", "redmi": "xiaomi", "iiif150": "iiif150"}

_gsm_makers: dict[str, str] = {}
_gsm_devices: dict[str, dict[str, str]] = {}      # марка -> {ключ модели: путь}


def _gsm_brand_url(fetcher, brand: str) -> str | None:
    if not _gsm_makers:
        html = fetcher.get(GSM_MAKERS, referer=GSM_BASE + "/")
        for path, name in GSM_MAKER_RE.findall(html):
            _gsm_makers[canon(name)] = path
    low = GSM_BRAND_ALIAS.get(brand.lower(), brand.lower())
    return _gsm_makers.get(low)


def _gsm_brand_devices(fetcher, brand: str, pages: int) -> dict[str, str]:
    """Устройства марки: ключ модели -> путь карточки. Первые `pages` страниц."""
    if brand in _gsm_devices:
        return _gsm_devices[brand]
    devices: dict[str, str] = {}
    url = _gsm_brand_url(fetcher, brand)
    if url:
        path = url
        for page in range(1, pages + 1):
            html = fetcher.get(f"{GSM_BASE}/{path}", referer=GSM_BASE + "/")
            for device_path, raw_name in GSM_LINK_RE.findall(html):
                name = _clean(raw_name)
                if NOT_A_PHONE_RE.search(name):
                    continue
                devices.setdefault(parse_title(f"{brand} {name}")["key"], device_path)
            following = {int(n): p for p, n in GSM_PAGE_RE.findall(html)}
            if page + 1 not in following:
                break
            path = following[page + 1]
    _gsm_devices[brand] = devices
    return devices


def gsm_spec(fetcher, phone: Phone, pages: int = 3) -> Spec | None:
    """
    Характеристики модели из GSMArena — по странице её марки.

    Запросов: страница марок (один раз на процесс), страницы марки (один раз
    на марку) и карточка устройства. Для десятка Doogee подряд это одна
    страница марки и десять карточек.
    """
    if not phone.brand:
        return None
    devices = _gsm_brand_devices(fetcher, phone.brand, pages)
    path = devices.get(phone.key)
    if not path:
        return None
    html = fetcher.get(f"{GSM_BASE}/{path}", referer=GSM_BASE + "/")
    spec = specs.build(_gsm_pairs(html), "gsmarena")
    return spec if spec.sources and not all(v in (None, [], "") for v in
                                            (spec.battery_mah, spec.ip, spec.ois,
                                             spec.optical_zoom)) else None


def _clean(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


def _gsm_pairs(html: str) -> list[tuple[str, str]]:
    """
    Таблица GSMArena -> пары «подпись → значение».

    Подписи внутри секций повторяются («Type» есть и у батареи, и у экрана),
    поэтому к подписи приклеивается название секции: «Battery Type»,
    «Body Protection». Без этого батарея неотличима от типа дисплея.
    """
    pairs: list[tuple[str, str]] = []
    section = ""
    for match in GSM_SECTION_RE.finditer(html):
        header, label, value = match.group(1), match.group(2), match.group(3)
        if header is not None:
            section = _clean(header)
            continue
        pairs.append((f"{section} {_clean(label)}".strip(), _clean(value)))
    return pairs


def _expected(pairs: list[tuple[str, str]], html: str) -> str | None:
    """Ожидаемый срок выхода — как его пишет сам источник."""
    for label, value in pairs:
        low = label.lower()
        if "status" in low or "announced" in low:
            match = EXPECTED_RE.search(value)
            if match:
                return match.group(1).strip(" .,")
            if value and value.lower() not in ("rumored", "not announced yet"):
                return value[:40]
    match = EXPECTED_RE.search(specs.strip_tags(html))
    return match.group(1).strip(" .,") if match else None


def deadline(expected: str | None, today: date | None = None) -> date | None:
    """
    Текст срока -> дата, до которой телефон обещают.

    «2026, Q2» -> конец квартала, «February 2026» -> конец месяца. Нужна не
    точность, а возможность отсечь то, что выйдет через год: «в ближайшие пару
    месяцев» — это окно, а не конкретный день.
    """
    if not expected:
        return None
    today = today or date.today()

    quarter = QUARTER_RE.search(expected)
    if quarter:
        q = int(quarter.group(1) or quarter.group(4))
        year = int(quarter.group(2) or quarter.group(3))
        month = q * 3
        return _end_of_month(year, month)

    month_match = MONTH_RE.search(expected)
    if month_match:
        month = MONTHS[next(k for k in MONTHS if month_match.group(1).lower().startswith(k))]
        year_match = re.search(r"20\d\d", expected)
        year = int(year_match.group(0)) if year_match else today.year
        # «March» без года в конце года означает следующий март.
        if not year_match and month < today.month:
            year += 1
        return _end_of_month(year, month)

    year_only = re.search(r"\b(20\d\d)\b", expected)
    if year_only:
        return date(int(year_only.group(1)), 12, 31)
    return None


def _end_of_month(year: int, month: int) -> date:
    if month >= 12:
        return date(year, 12, 31)
    return date.fromordinal(date(year, month + 1, 1).toordinal() - 1)


def from_gsmarena(fetcher, cfg: dict, store) -> dict[str, Phone]:
    """Слухи GSMArena: модель, ожидаемый срок и таблица характеристик."""
    conf = cfg.get("upcoming") or {}
    limit = conf.get("max_devices", 20)
    found: dict[str, Phone] = {}

    html = fetcher.get(GSM_RUMORED)
    devices = GSM_LINK_RE.findall(html)[:limit]
    log.info("скоро: в списке слухов GSMArena %s моделей", len(devices))

    for path, raw_name in devices:
        name = _clean(raw_name)
        if NOT_A_PHONE_RE.search(name):
            continue
        parsed = parse_title(name)
        key = parsed["key"]
        if not key:
            continue

        cached = store.get_upcoming(key, conf.get("recheck_after_days", 7))
        if cached is not None:
            phone = Phone(key=key, brand=parsed["brand"], name=parsed["model"] or name,
                          spec=Spec.from_dict(cached.get("spec") or {}))
            phone.expected = cached.get("expected")
            phone.articles = list(cached.get("articles") or [])
            found[key] = phone
            continue

        try:
            page = fetcher.get(f"{GSM_BASE}/{path}", referer=GSM_RUMORED)
        except Exception as exc:
            log.debug("скоро: карточка %s не открылась — %s", path, exc)
            continue

        pairs = _gsm_pairs(page)
        phone = Phone(key=key, brand=parsed["brand"], name=parsed["model"] or name,
                      spec=specs.build(pairs, "gsmarena"))
        phone.expected = _expected(pairs, page)
        phone.articles = [{"source": "GSMArena", "title": name,
                           "url": f"{GSM_BASE}/{path}"}]
        found[key] = phone

    return found


def from_news(fetcher, cfg: dict, models: dict[str, Phone]) -> dict[str, Phone]:
    """
    Дополняет список моделями и сроками из новостных лент.

    Из статьи берётся только то, что названо прямо: числа в тексте («7500 mAh»,
    «IP69», «5x optical zoom»). Домысливать по заголовку нельзя — «новый
    флагман с огромной батареей» характеристикой не является.
    """
    conf = cfg.get("upcoming") or {}
    articles = news.fetch(fetcher, conf.get("feeds") or {},
                          conf.get("articles_per_feed", 40))

    for article in articles:
        if not news.looks_upcoming(article):
            continue
        parsed = model_from_headline(article.title)
        if parsed is None:
            continue
        key = parsed["key"]

        phone = models.get(key)
        if phone is None:
            phone = Phone(key=key, brand=parsed["brand"], name=parsed["model"])
            models[key] = phone

        phone.spec.merge(_spec_from_text(article.text), article.source)
        if not phone.expected:
            phone.expected = _expected_from_text(article.text)
        if len(phone.articles) < 6 and not any(a["url"] == article.url
                                               for a in phone.articles):
            phone.articles.append({"source": article.source, "title": article.title,
                                   "url": article.url})
    return models


def _spec_from_text(text: str) -> Spec:
    """Характеристики, названные в статье прямым текстом."""
    spec = Spec()
    battery = re.search(r"(\d{4,5})\s*mAh", text, re.IGNORECASE)
    if battery:
        value = int(battery.group(1))
        if 3000 <= value <= 30000:
            spec.battery_mah = value
    ip = specs.IP_RE.search(text)
    if ip:
        spec.ip = f"IP{ip.group(1)}{ip.group(2).upper()}"
    if re.search(r"\bOIS\b|optical image stabili|stabilizacj[aię] optyczn", text, re.I):
        spec.ois = True
    zoom = re.search(r"(\d{1,2}(?:[.,]\d)?)\s*x\s*(?:optical|zoom optyczn)", text, re.I)
    if zoom:
        spec.optical_zoom = float(zoom.group(1).replace(",", "."))
    return spec


def _expected_from_text(text: str) -> str | None:
    match = EXPECTED_RE.search(text)
    if match:
        return match.group(1).strip(" .,")
    # «launching on February 25» / «debiut w marcu»
    near = re.search(r"(?:launch(?:ing|es)?|debut|premiera|debiut|zadebiutuje)"
                     r"[^.]{0,30}?\b([A-Za-zĄąĆćĘꣳŃńÓ󌜏źŻż]{3,12}\s*(?:20\d\d)?)",
                     text, re.IGNORECASE)
    if near and MONTH_RE.match(near.group(1)):
        return near.group(1).strip()
    return None


def collect(fetcher, cfg: dict, store) -> list[Phone]:
    """
    Собирает список «скоро выйдет», отфильтрованный по тем же критериям.

    Возвращает модели, отсортированные по сроку: чем ближе обещанная дата,
    тем выше.
    """
    conf = cfg.get("upcoming") or {}
    if not conf.get("enabled"):
        return []

    models = from_gsmarena(fetcher, cfg, store)
    if conf.get("feeds"):
        models = from_news(fetcher, cfg, models)

    window = conf.get("window_days", 90)
    today = date.today()
    out: list[Phone] = []
    for phone in models.values():
        specs.apply_ai_rules(phone.spec, phone.title, cfg)
        phone.unknown = phone.spec.missing()
        phone.upcoming = True
        phone.expected_by = deadline(phone.expected, today)

        if phone.expected_by:
            days = (phone.expected_by - today).days
            if days < -30 or days > window:
                continue        # уже должен был выйти или обещан слишком нескоро
        elif conf.get("require_date", False):
            continue

        if _contradicts(phone.spec, cfg):
            continue
        # Пустой слух («о телефоне пишут, но ничего не сказали») во вкладке
        # только мешает: ни срока, ни одной характеристики из наших критериев.
        known = any((phone.spec.battery_mah, phone.spec.ip, phone.spec.ois,
                     phone.spec.optical_zoom))
        if conf.get("require_signal", True) and not (phone.expected or known):
            continue
        out.append(phone)
        store.save_upcoming(phone)

    out.sort(key=lambda p: (p.expected_by or date(2099, 1, 1),
                            -len(p.spec.sources), p.title))
    log.info("скоро: моделей в окне %s дней — %s", window, len(out))
    return out


def _contradicts(spec: Spec, cfg: dict) -> str | None:
    """
    Прямое противоречие критериям — единственная причина выбросить слух.

    Неизвестность здесь не отказ: у невышедшего телефона половина
    характеристик ещё не названа, и требовать их — значит не показать ничего.
    """
    cr = cfg["criteria"]
    if spec.battery_mah and spec.battery_mah < (cr.get("battery_mah_min") or 0):
        return "батарея"
    if spec.ip and cr.get("ip_min"):
        required = int("".join(c for c in str(cr["ip_min"]) if c.isdigit())[:2] or 0)
        if spec.ip_level and spec.ip_level < required:
            return "IP"
    if spec.ois is False and cr.get("ois_required"):
        return "OIS"
    if spec.optical_zoom and spec.optical_zoom < (cr.get("optical_zoom_min") or 0):
        return "зум"
    return None
