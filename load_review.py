#!/usr/bin/env python3
"""
Задача 3: загрузка review.csv в ту же БД компаний.

review.csv называется "review", но по факту содержит НЕ отзывы, а ещё одну
порцию записей о компаниях в той же схеме, что и page_*.json:
id,name,category,city,address,rating,reviews_count,site,phone

Файл оказался "грязным" -- полный список того, что было найдено и как
обнаружено, см. в ANOMALIES.md. Здесь -- только логика обработки.

Стратегия (сознательно консервативная):
  - строки, которые нельзя безопасно восстановить (потеряна обязательная
    колонка) -- пропускаются и логируются, а не угадываются;
  - точечные "битые" значения в необязательных полях (rating, reviews_count,
    site, phone) -- исправляются, если исправление однозначно (опечатка в
    протоколе, запятая вместо точки), иначе поле обнуляется, а не выдумывается;
  - города нормализуются к каноническому написанию из справочника cities,
    чтобы не наплодить фиктивных "новых" городов из-за регистра/опечаток/
    битой кодировки.

Использование:
    python load_review.py --csv ./data/review.csv --dsn "postgresql://..."
"""
import argparse
import csv
import os
import re
import sys

import psycopg2
import psycopg2.extras

ID_PATTERN = re.compile(r"^c_\d{6}$")
PHONE_PATTERN = re.compile(r"^\+7 \(\d{3}\) \d{3}-\d{2}-\d{2}$")

# Известные варианты испорченного/нестандартного написания города ->
# каноническое написание, как оно хранится в таблице cities.
CITY_ALIASES = {
    "moscow": "Москва",
    "москва": "Москва",
    "санкат-петербург": "Санкт-Петербург",
}


def fix_mojibake(value: str) -> str:
    """
    Чинит классическую порчу кодировки: UTF-8 байты ошибочно декодировали
    как cp1251 и заново сохранили в UTF-8 ("РњРѕСЃРєРІР°" вместо "Москва").
    Кодируем обратно в cp1251 (получаем исходные UTF-8 байты) и декодируем
    как UTF-8. Для нормального текста это почти всегда падает с ошибкой --
    тогда просто возвращаем исходную строку без изменений.
    """
    try:
        return value.encode("cp1251").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def normalize_city(raw: str) -> str:
    s = fix_mojibake(raw.strip())
    return CITY_ALIASES.get(s.lower(), s)


def normalize_rating(raw: str, anomalies: list, row_id: str):
    raw = raw.strip()
    if not raw:
        return None
    candidate = raw.replace(",", ".")  # "4,5" -> "4.5" (локаль с запятой)
    try:
        value = float(candidate)
    except ValueError:
        anomalies.append(f"{row_id}: rating='{raw}' не число -> оставлено NULL")
        return None
    if not (0 <= value <= 5):
        anomalies.append(f"{row_id}: rating={value} вне диапазона 0..5 -> оставлено NULL")
        return None
    if candidate != raw:
        anomalies.append(f"{row_id}: rating='{raw}' исправлено на {candidate} (запятая -> точка)")
    return value


def normalize_reviews_count(raw: str, anomalies: list, row_id: str) -> int:
    raw = raw.strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        anomalies.append(
            f"{row_id}: reviews_count='{raw}' не целое число -> заменено на 0"
        )
        return 0
    if value < 0:
        anomalies.append(f"{row_id}: reviews_count={value} отрицательное -> заменено на 0")
        return 0
    return value


def normalize_site(raw: str, anomalies: list, row_id: str):
    raw = raw.strip()
    if not raw:
        return None
    if raw.lower() in {"нет сайта", "н/д", "n/a", "-"}:
        anomalies.append(f"{row_id}: site='{raw}' -- текст-заглушка, а не URL -> NULL")
        return None
    fixed = re.sub(r"^htp://", "http://", raw)  # частая опечатка в протоколе
    if fixed != raw:
        anomalies.append(f"{row_id}: site='{raw}' исправлено на '{fixed}' (опечатка в http://)")
    if not re.match(r"^https?://", fixed):
        anomalies.append(f"{row_id}: site='{raw}' не похоже на URL -> NULL")
        return None
    return fixed


def normalize_phone(raw: str, anomalies: list, row_id: str):
    raw = raw.strip()
    if not raw:
        return None
    if not PHONE_PATTERN.match(raw):
        anomalies.append(f"{row_id}: phone='{raw}' не соответствует формату -> NULL")
        return None
    return raw


def load_csv(path: str):
    with open(path, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("id")]
    print(f"[review] прочитано непустых строк: {len(rows)}")
    return rows


def process_rows(rows: list, anomalies: list) -> list:
    # 1. точные дубли строк внутри самого файла
    seen_exact = set()
    deduped = []
    for r in rows:
        key = tuple(r.values())
        if key in seen_exact:
            anomalies.append(f"{r['id']}: точный дубль строки внутри review.csv -- пропущен")
            continue
        seen_exact.add(key)
        deduped.append(r)

    clean_rows = []
    for r in deduped:
        rid = r["id"].strip()

        if not ID_PATTERN.match(rid):
            anomalies.append(f"'{rid}': id не соответствует формату c_NNNNNN -- строка пропущена")
            continue

        category = r["category"].strip()
        city_raw = r["city"].strip()
        address = r["address"].strip() or None

        # Обнаруженный сдвиг колонок (см. ANOMALIES.md): в поле category
        # оказывается название города, в city -- адрес, а сама категория
        # потеряна. Восстановить её нельзя, поэтому строку не грузим.
        if category in KNOWN_CITIES_HINT and city_raw.lower().startswith(("ул.", "просп.", "пр-т", "пер.")):
            anomalies.append(
                f"{rid}: похоже на сдвиг колонок (category='{category}' выглядит как город, "
                f"city='{city_raw}' выглядит как адрес) -- категория безвозвратно потеряна, "
                f"строка пропущена, а не догадана"
            )
            continue

        city = normalize_city(city_raw)

        clean_rows.append(
            {
                "id": rid,
                "name": r["name"].strip(),
                "category": category,
                "city": city,
                "address": address,
                "rating": normalize_rating(r["rating"], anomalies, rid),
                "reviews_count": normalize_reviews_count(r["reviews_count"], anomalies, rid),
                "site": normalize_site(r["site"], anomalies, rid),
                "phone": normalize_phone(r["phone"], anomalies, rid),
            }
        )

    return clean_rows


# Список городов подтягивается из БД перед обработкой (см. main); используется
# только для эвристики "это похоже на сдвиг колонок"
KNOWN_CITIES_HINT: set = set()


def upsert(conn, records: list):
    with conn.cursor() as cur:
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

        rows = [
            (
                r["id"], r["name"], category_ids[r["category"]], city_ids[r["city"]],
                r["address"], r["rating"], r["reviews_count"], r["site"], r["phone"],
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
                name = EXCLUDED.name, category_id = EXCLUDED.category_id,
                city_id = EXCLUDED.city_id, address = EXCLUDED.address,
                rating = EXCLUDED.rating, reviews_count = EXCLUDED.reviews_count,
                site = EXCLUDED.site, phone = EXCLUDED.phone, loaded_at = now()
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default="./data/review.csv")
    parser.add_argument("--dsn", default=None)
    args = parser.parse_args()

    dsn = args.dsn or os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("Не указан DSN. Передайте --dsn или переменную DATABASE_URL.")

    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT name FROM cities")
        global KNOWN_CITIES_HINT
        KNOWN_CITIES_HINT = {row[0] for row in cur.fetchall()}

    raw_rows = load_csv(args.csv)

    anomalies: list = []
    clean_rows = process_rows(raw_rows, anomalies)

    print(f"[review] к загрузке после очистки: {len(clean_rows)} из {len(raw_rows)}")
    print(f"[review] аномалий обнаружено: {len(anomalies)}")

    try:
        loaded = upsert(conn, clean_rows)
        print(f"[review] загружено/обновлено: {loaded}")
    finally:
        conn.close()

    if anomalies:
        report_path = "review_anomalies_raw.txt"
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(anomalies))
        print(f"[review] полный список аномалий (сырой лог) сохранён в {report_path}")
        print("[review] см. также ANOMALIES.md за читаемым разбором по категориям")

    print("[review] готово.")


if __name__ == "__main__":
    main()
