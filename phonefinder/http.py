"""
HTTP-слой.

Allegro, x-kom, Morele, MediaMarkt и Komputronik стоят за Cloudflare и его
роднёй, которые отбраковывают запрос по TLS-отпечатку: обычный `requests`
получает 403 ещё до того, как дело дойдёт до заголовков. `curl_cffi`
имперсонирует TLS-стек Chrome — только ради этого он и нужен.

Троттлинг здесь по хостам, а не общий: за один прогон опрашиваются пять
магазинов, и общая пауза замедляла бы их впятеро без всякой пользы —
для каждого магазина мы всё равно остаёмся одним неспешным клиентом.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from urllib.parse import urlsplit

from curl_cffi import requests

log = logging.getLogger(__name__)


class Cancelled(BaseException):
    """
    Прогон прерван снаружи — обычно потому, что приложение закрывают.

    Наследуется от `BaseException` по той же причине, что и `KeyboardInterrupt`:
    конвейер построен на «падение одного магазина не роняет прогон», и десяток
    `except Exception` вокруг сетевых вызовов гасил бы отмену, продолжая
    опрашивать дальше по списку.
    """


# Заголовков ровно столько, сколько нужно. `Cache-Control: no-cache` и `Pragma`
# сюда добавлять нельзя: живой браузер посылает их только при принудительной
# перезагрузке, и Allegro на такой запрос отвечает 403 (проверено). Свежесть
# выдачи от них всё равно не зависит — страницы категорий не кешируются надолго.
HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Upgrade-Insecure-Requests": "1",
}


class Fetcher:
    """
    Сетевой слой одного прогона.

    Прогон — это сотни запросов с паузами, то есть большую часть времени
    процесс просто ждёт. Поэтому точка отмены именно здесь: `stop` прерывает
    ожидание немедленно, и прогон сворачивается на первом же запросе, а не
    через полминуты.
    """

    def __init__(self, timeout: int = 40, retries: int = 3, delay: float = 1.2,
                 host_delay: dict[str, float] | None = None,
                 stop: threading.Event | None = None):
        self.timeout = timeout
        self.retries = retries
        self.delay = delay
        # Отдельная пауза для отдельных хостов: Allegro считает запросы жёстче
        # магазинов и на частые отвечает 429.
        self.host_delay = host_delay or {}
        self.stop = stop or threading.Event()
        self.session = requests.Session(impersonate="chrome")
        self._last: dict[str, float] = {}
        # Хосты, которые ответили 429 и до конца прогона не опрашиваются.
        self._throttled: set[str] = set()
        self.requests_made = 0

    def close(self) -> None:
        try:
            self.session.close()
        except Exception as exc:
            log.debug("не удалось закрыть сессию: %s", exc)

    def get(self, url: str, referer: str | None = None, fresh: bool = False) -> str:
        """
        Загружает страницу. `fresh` — поднять под этот запрос новую TLS-сессию.

        Нужно для Allegro: он отдаёт 200 только на первый запрос в сессии, а на
        все следующие — 403, независимо от паузы между ними. То есть держать
        соединение там не только бесполезно, но и вредно; остальные магазины,
        наоборот, спокойно работают в одной сессии весь прогон.
        """
        host = urlsplit(url).netloc
        if host in self._throttled:
            # Магазин уже сказал 429 в этом прогоне. Ждать его дальше — значит
            # тратить минуты на каждую страницу; остальные магазины важнее.
            raise RuntimeError(f"{host} ограничил частоту запросов — пропускаем")

        headers = dict(HEADERS)
        if referer:
            headers["Referer"] = referer

        last_error: Exception | None = None
        for attempt in range(1, self.retries + 1):
            self._throttle(url)
            session = requests.Session(impersonate="chrome") if fresh else self.session
            try:
                resp = session.get(url, headers=headers, timeout=self.timeout)
                self.requests_made += 1
                if resp.status_code == 200:
                    return resp.text
                last_error = RuntimeError(f"HTTP {resp.status_code}")
                log.warning("%s -> HTTP %s (попытка %s/%s)",
                            url, resp.status_code, attempt, self.retries)
                # 404 не лечится повтором: страницы просто нет.
                if resp.status_code == 404:
                    break
                # 429 лечится только ожиданием, и обычным backoff'ом тут не
                # обойтись: магазин просит подождать десятки секунд. Если и
                # это не помогло — до конца прогона к хосту не возвращаемся.
                if resp.status_code == 429:
                    if attempt >= self.retries:
                        self._throttled.add(host)
                        break
                    self._sleep(_retry_after(resp) or 15.0 * attempt)
                    continue
            except Exception as exc:  # сеть, таймаут, TLS
                last_error = exc
                log.warning("%s -> %s (попытка %s/%s)", url, exc, attempt, self.retries)
            finally:
                if fresh:
                    try:
                        session.close()
                    except Exception as exc:
                        log.debug("не удалось закрыть разовую сессию: %s", exc)
            self._sleep(min(2 ** attempt, 10) + random.random())

        raise RuntimeError(f"Не удалось загрузить {url}: {last_error}")

    def check_cancelled(self) -> None:
        if self.stop.is_set():
            raise Cancelled("прогон прерван")

    def _throttle(self, url: str) -> None:
        self.check_cancelled()
        host = urlsplit(url).netloc
        delay = next((v for k, v in self.host_delay.items() if k in host), self.delay)
        elapsed = time.monotonic() - self._last.get(host, 0.0)
        wait = delay - elapsed
        if wait > 0:
            self._sleep(wait + random.uniform(0, 0.4))
        self._last[host] = time.monotonic()

    def _sleep(self, seconds: float) -> None:
        """Пауза, прерываемая отменой: ждём на событии, а не на `time.sleep`."""
        if self.stop.wait(seconds):
            raise Cancelled("прогон прерван")


def _retry_after(resp) -> float | None:
    """Сколько магазин просит подождать, если он это сказал заголовком."""
    raw = (resp.headers or {}).get("Retry-After")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return min(seconds, 120.0)
