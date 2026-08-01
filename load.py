#!/usr/bin/env python3
"""
Загружает данные компаний из постраничных JSON-файлов в PostgreSQL.

Использование:
    python load.py --data-dir ./data --dsn "postgresql://user:pass@localhost:5432/companies"

DSN можно не передавать -- тогда скрипт возьмёт его из переменной окружения
DATABASE_URL (см. .env.example / docker-compose.yml).
"""
import argparse
import glob
import json
import os
import sys

import psycopg2
import psycopg2.extras


def load_records(data_dir: str) -> list[dict]:
    """Читает все page_*.json из data_dir и возвращает список сырых записей."""
    files = sorted(glob.glob(os.path.join(data_dir, "page_*.json")))
    if not files:
        raise SystemExit(f"В {data_dir} не найдено файлов page_*.json")

    all_items = []
    declared_total = None
    for path in files:
        with open(path, encoding="utf-8") as f:
            page = json.load(f)
        declared_total = page.get("total", declared_total)
        all_items.extend(page["items"])

    print(f"[load] прочитано файлов: {len(files)}, записей (сырых): {len(all_items)}")
    if declared_total is not None and declared_total != len(all_items):
        print(
            f"[load] ВНИМАНИЕ: total в JSON ({declared_total}) "
            f"!= количеству прочитанных записей ({len(all_items)})",
            file=sys.stderr,
        )
    return all_items


def dedupe(records: list[dict]) -> list[dict]:
    """Убирает точные дубли по id (в исходных данных встречаются 1-в-1 повторы)."""
    seen = {}
    for r in records:
        seen[r["id"]] = r  # при повторе последняя запись перезапишет предыдущую
    deduped = list(seen.values())
    dropped = len(records) - len(deduped)
    if dropped:
        print(f"[load] удалено дублей по id: {dropped}")
    return deduped


def get_dsn(cli_dsn: str | None) -> str:
    dsn = cli_dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit(
            "Не указан DSN. Передайте --dsn или переменную окружения DATABASE_URL."
        )
    return dsn


def upsert(conn, records: list[dict]) -> None:
    with conn.cursor() as cur:
        # 1. справочники (категории/города) -- сначала собираем уникальные значения
        categories = sorted({r["category"] for r in records})
        cities = sorted({r["city"] for r in records})

        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO categories (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(c,) for c in categories],
        )
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO cities (name) VALUES %s ON CONFLICT (name) DO NOTHING",
            [(c,) for c in cities],
        )

        cur.execute("SELECT id, name FROM categories")
        category_ids = {name: cid for cid, name in cur.fetchall()}
        cur.execute("SELECT id, name FROM cities")
        city_ids = {name: cid for cid, name in cur.fetchall()}

        # 2. компании -- upsert по первичному ключу id.
        #    При повторной загрузке того же id данные обновятся (не задублируются).
        rows = [
            (
                r["id"],
                r["name"],
                category_ids[r["category"]],
                city_ids[r["city"]],
                r.get("address"),
                r.get("rating"),
                r.get("reviews_count", 0) or 0,
                r.get("site"),
                r.get("phone"),
            )
            for r in records
        ]

        psycopg2.extras.execute_values(
            cur,
            """
            INSERT INTO companies
                (id, name, category_id, city_id, address, rating, reviews_count, site, phone)
            VALUES %s
            ON CONFLICT (id) DO UPDATE SET
                name          = EXCLUDED.name,
                category_id   = EXCLUDED.category_id,
                city_id       = EXCLUDED.city_id,
                address       = EXCLUDED.address,
                rating        = EXCLUDED.rating,
                reviews_count = EXCLUDED.reviews_count,
                site          = EXCLUDED.site,
                phone         = EXCLUDED.phone,
                loaded_at     = now()
            """,
            rows,
        )
    conn.commit()
    print(f"[load] загружено/обновлено компаний: {len(rows)}")
    print(f"[load] категорий: {len(category_ids)}, городов: {len(city_ids)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="./data", help="Папка с page_*.json")
    parser.add_argument("--dsn", default=None, help="Postgres DSN (иначе берётся DATABASE_URL)")
    args = parser.parse_args()

    dsn = get_dsn(args.dsn)
    records = load_records(args.data_dir)
    records = dedupe(records)

    conn = psycopg2.connect(dsn)
    try:
        upsert(conn, records)
    finally:
        conn.close()

    print("[load] готово.")


if __name__ == "__main__":
    main()
