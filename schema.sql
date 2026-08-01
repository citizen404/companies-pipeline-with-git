-- ============================================================
-- Схема БД для каталога компаний
-- Источник данных: постраничная JSON-выгрузка (page_001.json..page_020.json)
-- ============================================================

-- Справочник категорий компаний (в исходных данных 22 уникальных значения)
CREATE TABLE IF NOT EXISTS categories (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- Справочник городов (в исходных данных 20 уникальных значений)
CREATE TABLE IF NOT EXISTS cities (
    id   SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- Основная таблица компаний
CREATE TABLE IF NOT EXISTS companies (
    -- id из исходных данных (вида "c_000001") используется как естественный
    -- первичный ключ -- это и есть основной механизм дедупликации:
    -- повторная загрузка того же id перезапишет/пропустит запись, а не
    -- создаст дубль (см. ON CONFLICT в load.py).
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    category_id    INTEGER NOT NULL REFERENCES categories(id),
    city_id        INTEGER NOT NULL REFERENCES cities(id),
    address        TEXT,
    -- рейтинг в исходных данных бывает NULL (нет отзывов/оценок)
    rating         NUMERIC(2,1) CHECK (rating IS NULL OR (rating BETWEEN 0 AND 5)),
    reviews_count  INTEGER NOT NULL DEFAULT 0 CHECK (reviews_count >= 0),
    site           TEXT,
    phone          TEXT,
    loaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Доп. защита от дублей: если у двух записей случайно разные id,
    -- но совпадают название+город+адрес -- это одна и та же компания.
    CONSTRAINT uq_companies_identity UNIQUE (name, city_id, address)
);

-- ------------------------------------------------------------
-- Индексы под целевые аналитические запросы (queries.sql)
-- ------------------------------------------------------------

-- Топ-5 категорий по числу компаний / доля компаний с сайтом по категориям
CREATE INDEX IF NOT EXISTS idx_companies_category_id ON companies(category_id);

-- Средний рейтинг по городам среди компаний с 10+ отзывами
CREATE INDEX IF NOT EXISTS idx_companies_city_id ON companies(city_id);
CREATE INDEX IF NOT EXISTS idx_companies_reviews_count ON companies(reviews_count);

-- Частый фильтр "есть сайт / нет сайта" -- частичный индекс компактнее полного
CREATE INDEX IF NOT EXISTS idx_companies_has_site ON companies(category_id) WHERE site IS NOT NULL;
