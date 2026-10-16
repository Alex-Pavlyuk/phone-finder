"""CLI: python -m phonefinder <run|watch|urls|spec>"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import threading
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("curl_cffi").setLevel(logging.WARNING)


def cmd_run(args, cfg: dict) -> int:
    from .pipeline import run

    result = run(cfg, DATA)
    print()
    print(f"  предложений ...... {result['offers']}")
    print(f"  моделей .......... {result['phones']}")
    print(f"  подходит ......... {result['matched']} (почти: {result['near_miss']})")
    print(f"  новых ............ {result['new']}")
    print(f"  запросов ......... {result['requests']} "
          f"(карточек товара: {result['specs'].get('fetched', 0)}, "
          f"из кеша: {result['specs'].get('cached', 0)})")
    print(f"  отчёт ............ {result['report']}")
    if result["top"]:
        print("\n  Топ-5:")
        for phone in result["top"][:5]:
            spec = phone.spec
            bits = [f"{spec.battery_mah or '?'} мАч",
                    f"{spec.optical_zoom or '?'}×",
                    spec.ip or "IP?"]
            price = f"{phone.price:,}".replace(",", " ") if phone.price else "—"
            print(f"   {phone.score:3}  {price:>7} zł  {phone.title[:38]:38} "
                  f"{' · '.join(str(b) for b in bits)}")
    if args.open:
        subprocess.run(["open", str(result["report"])], check=False)
    return 0


def cmd_watch(args, cfg: dict) -> int:
    """
    Наблюдение в терминале — та же роль, что у приложения, только без значка.

    Поэтому и замок тот же: два наблюдателя (или наблюдатель вместе
    с `PhoneFinder.app`) опрашивали бы магазины вдвое чаще без всякой пользы.
    И то же завершение: по Ctrl+C или `kill` прогон отменяется сразу, а не
    после текущего запроса, и процесс не остаётся висеть.
    """
    from .http import Cancelled
    from .lifecycle import AlreadyRunning, InstanceLock, on_termination
    from .pipeline import run

    DATA.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(DATA / "app.lock")
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        logging.error("уже работает другой экземпляр (%s) — второй не нужен", exc)
        return 1

    stop = threading.Event()
    on_termination(lambda name: stop.set())

    interval = args.interval * 60
    logging.info("режим наблюдения: каждые %s мин. Ctrl+C для выхода.", args.interval)
    try:
        while not stop.is_set():
            try:
                result = run(cfg, DATA, stop=stop)
                logging.info("готово: подходит %s, новых %s → %s",
                             result["matched"], result["new"], result["report"])
            except (Cancelled, KeyboardInterrupt):
                break
            except Exception as exc:
                logging.error("прогон упал: %s", exc)
            if stop.wait(interval):
                break
    finally:
        lock.release()
    logging.info("наблюдение остановлено")
    return 0


def cmd_report(args, cfg: dict) -> int:
    """
    Перерисовывает отчёт из базы, без единого запроса в сеть.

    Нужно каждый раз, когда меняется оформление отчёта или порог в критериях,
    а тратить полный прогон (и терпение магазинов) ради этого незачем.
    """
    from . import report
    from .models import Phone
    from .store import Store

    store = Store(DATA / "listings.db")
    try:
        phones = [(Phone.from_dict(raw), {"is_new": False, "price_drop": 0})
                  for raw in store.kept_phones()]
        soon = [Phone.from_dict(raw) for raw in store.kept_upcoming()]
        ali = [Phone.from_dict(raw) for raw in store.kept_aliexpress()]
    finally:
        store.close()

    if not phones:
        print("в базе нет подходящих моделей — сначала сделайте прогон: run")
        return 1
    out = report.render(DATA / "report.html", phones, 0, cfg, None, soon, ali)
    print(f"перерисовано моделей: {len(phones)}, скоро выйдет: {len(soon)}, "
          f"AliExpress: {len(ali)} → {out}")
    if args.open:
        subprocess.run(["open", str(out)], check=False)
    return 0


def cmd_urls(args, cfg: dict) -> int:
    """Печатает поисковые URL — удобно проверить выдачу руками в браузере."""
    from .sources import ALL

    for name, module in ALL.items():
        state = "" if cfg["sources"].get(name, {}).get("enabled") else "  (выключен)"
        print(f"{name:12}{state} {module.build_url(cfg)}")
    return 0


def cmd_spec(args, cfg: dict) -> int:
    """
    Разбирает характеристики по ссылке на карточку товара.

    Нужно, когда модель неожиданно оказалась в «почти» с пометкой «зум не
    указан»: команда показывает, что именно вычиталось из страницы магазина.
    """
    from . import specs
    from .http import Fetcher
    from .sources import ALL

    source = next((name for name, module in ALL.items()
                   if module.BASE.split("//")[-1].removeprefix("www.") in args.url), None)
    if source is None:
        print("не понял, какой это магазин:", args.url)
        return 1

    fetcher = Fetcher(delay=0.2)
    try:
        html = fetcher.get(args.url, referer=ALL[source].BASE)
    finally:
        fetcher.close()

    module = ALL[source]
    pairs = module.spec_pairs(html) if hasattr(module, "spec_pairs") else []
    spec = specs.from_html(html, source, pairs)
    print(f"магазин: {source}, размеченных пар: {len(pairs)}")
    for name, value in spec.to_dict().items():
        print(f"  {name:14} {value}")
    if args.verbose:
        for name, value in pairs:
            print(f"    · {name}: {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="phonefinder",
        description="Поиск телефонов на Allegro, x-kom, Morele, MediaMarkt и Komputronik",
    )
    parser.add_argument("-c", "--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="один прогон + отчёт")
    p_run.add_argument("--open", action="store_true", help="открыть отчёт в браузере")
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="крутиться в фоне и обновлять отчёт")
    p_watch.add_argument("--interval", type=int, default=60, help="минут между прогонами")
    p_watch.set_defaults(func=cmd_watch)

    p_report = sub.add_parser("report", help="перерисовать отчёт из базы, без сети")
    p_report.add_argument("--open", action="store_true", help="открыть отчёт в браузере")
    p_report.set_defaults(func=cmd_report)

    sub.add_parser("urls", help="показать поисковые URL").set_defaults(func=cmd_urls)

    p_spec = sub.add_parser("spec", help="разобрать характеристики по ссылке на товар")
    p_spec.add_argument("url")
    p_spec.set_defaults(func=cmd_spec)

    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    cfg = load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
