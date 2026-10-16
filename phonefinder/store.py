"""
SQLite-хранилище.

Нужно ровно для четырёх вещей:
  * кеш характеристик — карточку товара за модель открываем один раз навсегда;
  * знать, какая модель появилась в продаже впервые (бейдж «НОВИНКА» и
    уведомление — собственно то, ради чего это приложение фоновое);
  * история лучшей цены по модели (бейдж «−300 zł»);
  * помечать исчезнувшие модели, чтобы отчёт не показывал мёртвые ссылки.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .models import Phone, Spec, iso_now
from .specs import PARSER_VERSION

SCHEMA = """
CREATE TABLE IF NOT EXISTS phones (
    key         TEXT PRIMARY KEY,
    brand       TEXT,
    name        TEXT,
    price       INTEGER,
    first_price INTEGER,
    score       INTEGER,
    matched     INTEGER DEFAULT 0,
    first_seen  TEXT,
    last_seen   TEXT,
    is_gone     INTEGER DEFAULT 0,
    data        TEXT
);
CREATE INDEX IF NOT EXISTS idx_phones_lastseen ON phones(last_seen);

CREATE TABLE IF NOT EXISTS price_history (
    key   TEXT,
    ts    TEXT,
    price INTEGER
);
CREATE INDEX IF NOT EXISTS idx_price_key ON price_history(key);

CREATE TABLE IF NOT EXISTS offers (
    key        TEXT PRIMARY KEY,
    phone_key  TEXT,
    source     TEXT,
    url        TEXT,
    title      TEXT,
    price      INTEGER,
    first_seen TEXT,
    last_seen  TEXT
);
CREATE INDEX IF NOT EXISTS idx_offers_phone ON offers(phone_key);

-- Характеристики модели. Кешируются надолго: телефон не меняет ёмкость
-- батареи после релиза, а открытие карточки товара — самая дорогая операция
-- прогона. `complete` = все ключевые поля известны, такие записи не
-- перепроверяются вообще.
CREATE TABLE IF NOT EXISTS specs (
    key      TEXT PRIMARY KEY,
    ts       TEXT,
    complete INTEGER DEFAULT 0,
    version  INTEGER DEFAULT 0,
    data     TEXT
);

-- Попытки найти карточку модели по названию. Помним и неудачные: модель,
-- которую не возит ни один магазин с таблицей характеристик, иначе съедала бы
-- лимит поиска в каждом прогоне, и до остальных очередь не доходила бы никогда.
CREATE TABLE IF NOT EXISTS lookups (
    key   TEXT PRIMARY KEY,
    ts    TEXT,
    found INTEGER DEFAULT 0
);

-- Телефоны, которых ещё нет в продаже (вкладка «Скоро»). Хранятся, чтобы
-- знать, какой слух появился впервые, и чтобы не открывать одну и ту же
-- карточку GSMArena в каждом прогоне: слухи меняются не по часам.
CREATE TABLE IF NOT EXISTS upcoming (
    key        TEXT PRIMARY KEY,
    ts         TEXT,
    first_seen TEXT,
    data       TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    ts        TEXT,
    offers    INTEGER,
    phones    INTEGER,
    matched   INTEGER,
    new_count INTEGER
);
"""

CRITICAL = ("battery", "ip", "ois", "zoom")


# Доливки в уже существующую базу: `CREATE TABLE IF NOT EXISTS` новые столбцы
# не добавляет, а сносить кеш характеристик из-за одного поля жалко — он стоит
# сотен запросов к магазинам.
MIGRATIONS = ("ALTER TABLE specs ADD COLUMN version INTEGER DEFAULT 0",)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for statement in MIGRATIONS:
            try:
                self.conn.execute(statement)
            except sqlite3.OperationalError:
                pass          # столбец уже есть — это нормальный случай
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # ── характеристики ────────────────────────────────────────────────────────
    def get_spec(self, key: str, recheck_days: int) -> Spec | None:
        row = self.conn.execute(
            "SELECT ts, complete, version, data FROM specs WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        spec = Spec.from_dict(json.loads(row["data"]))
        if (row["version"] or 0) < PARSER_VERSION and not row["complete"]:
            # Разбор с тех пор научился большему, а запись неполная — значит
            # именно её и стоит перечитать (полную перечитывать незачем: всё,
            # что нужно, в ней уже есть).
            return None
        if row["complete"]:
            return spec
        # Неполную запись через какое-то время стоит проверить снова: магазины
        # дозаполняют таблицы характеристик уже после появления товара.
        try:
            age = datetime.now().astimezone() - datetime.fromisoformat(row["ts"])
        except ValueError:
            return spec
        return None if age > timedelta(days=recheck_days) else spec

    def save_spec(self, key: str, spec: Spec) -> None:
        complete = 0 if [g for g in spec.missing() if g in CRITICAL] else 1
        self.conn.execute(
            "INSERT OR REPLACE INTO specs (key, ts, complete, version, data) "
            "VALUES (?,?,?,?,?)",
            (key, iso_now(), complete, PARSER_VERSION,
             json.dumps(spec.to_dict(), ensure_ascii=False)),
        )

    # ── «скоро выйдет» ────────────────────────────────────────────────────────
    def get_upcoming(self, key: str, recheck_days: int) -> dict | None:
        row = self.conn.execute(
            "SELECT ts, data FROM upcoming WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            age = datetime.now().astimezone() - datetime.fromisoformat(row["ts"])
        except ValueError:
            return None
        if age > timedelta(days=recheck_days):
            return None       # слух мог обрасти подробностями — перечитаем
        return json.loads(row["data"])

    def save_upcoming(self, phone: Phone, key_prefix: str = "") -> bool:
        """
        Сохраняет запись вкладки «Скоро» или «AliExpress».

        Одна таблица на две вкладки, но ключи с префиксом: один и тот же
        телефон может быть и слухом, и товаром из Китая, и это разные записи
        с разными данными. True — если такая встретилась впервые.
        """
        now = iso_now()
        key = key_prefix + phone.key
        row = self.conn.execute(
            "SELECT first_seen FROM upcoming WHERE key = ?", (key,)
        ).fetchone()
        first_seen = row["first_seen"] if row else now
        self.conn.execute(
            "INSERT OR REPLACE INTO upcoming (key, ts, first_seen, data) VALUES (?,?,?,?)",
            (key, now, first_seen, json.dumps(phone.to_dict(), ensure_ascii=False)),
        )
        return row is None

    def spec_from_upcoming(self, key: str) -> Spec | None:
        """
        Что мы успели выяснить про модель, пока она была слухом.

        Телефон не меняет характеристики, выходя в продажу, а таблица магазина
        бывает беднее, чем спецификация GSMArena: у Morele камера описана одной
        строкой «200 Mpix», и ни зума, ни стабилизации оттуда не достать. Срок
        годности у такой записи не проверяется — она про железо, а не про цену.

        Записи AliExpress (ключ с префиксом `ali:`) сюда не попадают намеренно:
        там характеристики со слов продавца.
        """
        row = self.conn.execute(
            "SELECT data FROM upcoming WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        spec = (json.loads(row["data"]) or {}).get("spec")
        return Spec.from_dict(spec) if spec else None

    def kept_upcoming(self, fresh_days: int = 14) -> list[dict]:
        """
        Слухи, которые ещё не устарели — для перерисовки отчёта без сети.

        Пустые записи (ни срока, ни одной характеристики) отбрасываются на
        чтении: в базе могли остаться слухи, сохранённые до того, как отбор
        стал строже, и в отчёте они выглядели бы мусором.
        """
        cutoff = (datetime.now().astimezone() - timedelta(days=fresh_days)).isoformat()
        rows = self.conn.execute(
            "SELECT data FROM upcoming WHERE ts > ? AND key NOT LIKE 'ali:%' "
            "ORDER BY ts DESC", (cutoff,)
        ).fetchall()
        out = []
        for row in rows:
            data = json.loads(row["data"])
            spec = data.get("spec") or {}
            if data.get("expected") or any(spec.get(f) for f in
                                           ("battery_mah", "ip", "ois", "optical_zoom")):
                out.append(data)
        return out

    def kept_aliexpress(self, fresh_days: int = 3) -> list[dict]:
        """
        Товары AliExpress с прошлого прогона — для перерисовки отчёта без сети.

        Срок короче, чем у слухов: выдача и цены там меняются каждый день.
        """
        cutoff = (datetime.now().astimezone() - timedelta(days=fresh_days)).isoformat()
        rows = self.conn.execute(
            "SELECT data FROM upcoming WHERE ts > ? AND key LIKE 'ali:%' "
            "ORDER BY ts DESC", (cutoff,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # ── попытки поиска по названию ────────────────────────────────────────────
    def lookup_tried(self, key: str, retry_after_days: int) -> bool:
        """Пробовали ли уже искать эту модель — и не пора ли попробовать снова."""
        row = self.conn.execute(
            "SELECT ts FROM lookups WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return False
        try:
            age = datetime.now().astimezone() - datetime.fromisoformat(row["ts"])
        except ValueError:
            return True
        return age <= timedelta(days=retry_after_days)

    def save_lookup(self, key: str, found: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO lookups (key, ts, found) VALUES (?,?,?)",
            (key, iso_now(), 1 if found else 0),
        )

    # ── модели ────────────────────────────────────────────────────────────────
    def upsert(self, phone: Phone) -> dict:
        """
        Сохраняет модель и возвращает её «дельту» с прошлого прогона:
        {is_new, price_drop, first_seen, first_price, history}.
        """
        now = iso_now()
        price = phone.price
        row = self.conn.execute(
            "SELECT * FROM phones WHERE key = ?", (phone.key,)
        ).fetchone()
        matched = 0 if phone.reject_reasons else (2 if phone.near_miss else 1)

        if row is None:
            self.conn.execute(
                "INSERT INTO phones (key, brand, name, price, first_price, score, "
                "matched, first_seen, last_seen, is_gone, data) VALUES (?,?,?,?,?,?,?,?,?,0,?)",
                (phone.key, phone.brand, phone.name, price, price, phone.score, matched,
                 now, now, json.dumps(phone.to_dict(), ensure_ascii=False)),
            )
            if price:
                self._add_price(phone.key, now, price)
            meta = {"is_new": True, "price_drop": 0,
                    "first_seen": now, "first_price": price}
        else:
            price_drop = 0
            if price is not None and row["price"] is not None:
                price_drop = row["price"] - price
            if price is not None and price_drop != 0:
                self._add_price(phone.key, now, price)
            self.conn.execute(
                "UPDATE phones SET brand = ?, name = ?, price = ?, score = ?, "
                "matched = ?, last_seen = ?, is_gone = 0, data = ? WHERE key = ?",
                (phone.brand, phone.name, price, phone.score, matched, now,
                 json.dumps(phone.to_dict(), ensure_ascii=False), phone.key),
            )
            meta = {
                "is_new": False,
                "price_drop": price_drop,
                "first_seen": row["first_seen"],
                "first_price": row["first_price"],
                # Модель могла существовать в базе как непроходная и стать
                # подходящей после того, как магазин дозаполнил характеристики.
                # Для уведомлений это тоже новость.
                "newly_matched": matched == 1 and row["matched"] != 1,
            }

        for offer in phone.offers:
            self._upsert_offer(phone.key, offer, now)
        meta["history"] = self.price_history(phone.key)
        return meta

    def _add_price(self, key: str, ts: str, price: int) -> None:
        self.conn.execute(
            "INSERT INTO price_history (key, ts, price) VALUES (?,?,?)", (key, ts, price)
        )

    def _upsert_offer(self, phone_key: str, offer, now: str) -> None:
        self.conn.execute(
            "INSERT INTO offers (key, phone_key, source, url, title, price, "
            "first_seen, last_seen) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET price = excluded.price, "
            "last_seen = excluded.last_seen, url = excluded.url, title = excluded.title",
            (offer.key, phone_key, offer.source, offer.url, offer.title,
             offer.price, now, now),
        )

    def price_history(self, key: str) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT ts, price FROM price_history WHERE key = ? ORDER BY ts", (key,)
        ).fetchall()
        return [(r["ts"], r["price"]) for r in rows]

    def mark_gone(self, stale_after_days: int) -> int:
        cutoff = (datetime.now().astimezone()
                  - timedelta(days=stale_after_days)).isoformat()
        cur = self.conn.execute(
            "UPDATE phones SET is_gone = 1 WHERE last_seen < ? AND is_gone = 0", (cutoff,)
        )
        return cur.rowcount

    def record_run(self, offers: int, phones: int, matched: int, new_count: int) -> None:
        self.conn.execute(
            "INSERT INTO runs (ts, offers, phones, matched, new_count) VALUES (?,?,?,?,?)",
            (iso_now(), offers, phones, matched, new_count),
        )

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM runs ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    def kept_phones(self) -> list[dict]:
        """
        Модели, прошедшие фильтр в прошлый прогон (подходят или «почти»).

        Нужно для перерисовки отчёта без сети: `matched` — то же деление,
        что и в отчёте, 1 — подходит, 2 — почти.
        """
        rows = self.conn.execute(
            "SELECT data FROM phones WHERE is_gone = 0 AND matched > 0"
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    def known_models(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM phones").fetchone()
        return row["n"] if row else 0
