"""
Источник ceneo.pl.

Ceneo — не магазин, а сравнение цен, и в списке источников он занимает особое
место сразу по двум причинам.

* **Цена.** В плитке выдачи есть `data-productminprice` — минимальная цена по
  всем магазинам, которые Ceneo сравнивает (у карточки товара это `lowPrice`
  в schema.org). Туда входят и Media Expert, и RTV Euro AGD, до которых мы
  напрямую не достаём: они отдают 403. То есть Ceneo закрывает ровно ту дыру,
  которая осталась от заблокированных магазинов.
* **Характеристики.** Таблица спецификации у Ceneo — самая формализованная из
  всех: «Stopień ochrony IP», «Pojemność baterii», «Funkcje aparatu» (там же
  OIS), «Zoom optyczny», и — единственный случай среди всех источников —
  отдельная строка «Funkcje AI».

Плата за это — жёсткая защита от роботов: после десятка запросов с одного
адреса Ceneo начинает отдавать на любой URL страницу-заглушку «Przejdź do
Ceneo» с кодом 200. Заглушка распознаётся по отсутствию плиток, и источник
на этом прогоне молча выключается — ждать тут бессмысленно, ограничение
снимается само через часы.
"""
from __future__ import annotations

import html as html_lib
import logging
import re

from ..models import Offer, is_accessory, looks_used, parse_int, parse_title
from . import jsonld

log = logging.getLogger(__name__)

BASE = "https://www.ceneo.pl"
CATEGORY = "/Smartfony"
SPEC_CAPABLE = True

# Плитка товара — внешний `class="cat-prod-row"`, а внутри неё те же буквы
# ещё в пяти классах (`cat-prod-row__body`, `__foto`, …). Отрицательная
# проверка на следующий символ оставляет только внешние: иначе плитка резалась
# бы на куски, и картинка с ценой оказывались бы в разных.
TILE_SPLIT_RE = re.compile(r'class="cat-prod-row(?![_-])')
PID_RE = re.compile(r'data-pid="(\d+)"')
NAME_RE = re.compile(r'data-productName="([^"]{4,160})"', re.IGNORECASE)
BRAND_RE = re.compile(r'data-brand="([^"]{1,40})"', re.IGNORECASE)
PRICE_RE = re.compile(r'data-productminprice="([\d ,.]+)"', re.IGNORECASE)
IMAGE_RE = re.compile(r'<img[^>]+src="(//image\.ceneostatic\.pl/[^"]+)"')

# Строка таблицы характеристик: подпись в одной ячейке, значение в соседней.
SPEC_ROW_RE = re.compile(
    r'row__name">(.*?)</td>\s*'
    r'<td class="product-spec__group__attributes__row__value">(.*?)</td>',
    re.S,
)
# Всплывающая подсказка внутри подписи — в ней целый абзац текста про параметр,
# и без её удаления «подпись» превратилась бы в этот абзац.
HINT_RE = re.compile(r'<span class="hint-handle.*?</span>\s*</span>', re.S)

BLOCK_MARKER = "Przejdź do Ceneo"


def build_url(cfg: dict, page: int = 1) -> str:
    """
    Ссылка на страницу выдачи.

    Нумерация страниц у Ceneo нулевая и живёт в хвосте `;0020-30-0-0-N.htm`,
    поэтому вторая страница — это `-1`. Первую берём по чистому адресу
    категории: он отдаёт то же самое, но без служебного хвоста.
    """
    if page <= 1:
        return f"{BASE}{CATEGORY}"
    return f"{BASE}{CATEGORY};0020-30-0-0-{page - 1}.htm"


def _clean(fragment: str) -> str:
    text = HINT_RE.sub(" ", fragment)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_lib.unescape(text)).strip()


def _to_offer(chunk: str) -> Offer | None:
    pid = PID_RE.search(chunk)
    name = NAME_RE.search(chunk)
    if not pid or not name:
        return None

    title = html_lib.unescape(name.group(1))
    price = PRICE_RE.search(chunk)
    image = IMAGE_RE.search(chunk)
    brand = BRAND_RE.search(chunk)
    parsed = parse_title(title)
    return Offer(
        source="ceneo",
        ext_id=pid.group(1),
        url=f"{BASE}/{pid.group(1)}",
        title=title,
        price=parse_int(price.group(1)) if price else None,
        image=f"https:{image.group(1)}" if image else None,
        used=looks_used(title),
        # Марку Ceneo указывает отдельным атрибутом — берём её, когда по
        # заголовку марку опознать не удалось (китайские «кирпичи» и прочее,
        # чего нет в словаре марок).
        brand=parsed["brand"] or (brand.group(1) if brand else None),
        model=parsed["model"],
        model_key=parsed["key"],
        variant=parsed["variant"],
        ram_gb=parsed["ram_gb"],
        storage_gb=parsed["storage_gb"],
    )


def fetch(fetcher, cfg: dict) -> list[Offer]:
    max_pages = cfg["sources"]["ceneo"].get("max_pages", 4)
    out: list[Offer] = []
    seen: set[str] = set()

    for page in range(1, max_pages + 1):
        html = fetcher.get(build_url(cfg, page), fresh=True)
        chunks = TILE_SPLIT_RE.split(html)[1:]
        if not chunks:
            if BLOCK_MARKER in html:
                log.warning("ceneo: вместо выдачи заглушка «%s» — источник "
                            "выключен до следующего прогона", BLOCK_MARKER)
            else:
                log.warning("ceneo: плиток не найдено на стр. %s", page)
            break

        fresh = 0
        for chunk in chunks:
            offer = _to_offer(chunk)
            if offer is None or offer.key in seen or is_accessory(offer.title):
                continue
            seen.add(offer.key)
            out.append(offer)
            fresh += 1
        if fresh == 0:
            break

    log.info("ceneo: собрано %s предложений", len(out))
    return out


def spec_pairs(html: str) -> list[tuple[str, str]]:
    """Пары «подпись → значение» из таблицы характеристик карточки товара."""
    return [(_clean(name), _clean(value)) for name, value in SPEC_ROW_RE.findall(html)]


def product_price(html: str) -> int | None:
    """Минимальная цена по всем магазинам — `lowPrice` из schema.org."""
    data = jsonld.product(html) or {}
    offers = data.get("offers")
    offers = offers[0] if isinstance(offers, list) and offers else offers
    if not isinstance(offers, dict):
        return None
    return parse_int(offers.get("lowPrice") or offers.get("price"))
