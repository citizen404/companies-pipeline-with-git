-- ============================================================
-- Аналитические запросы
-- ============================================================

-- 1. Топ-5 категорий по числу компаний
SELECT
    c.name          AS category,
    COUNT(*)        AS companies_count
FROM companies co
JOIN categories c ON c.id = co.category_id
GROUP BY c.name
ORDER BY companies_count DESC
LIMIT 5;


-- 2. Средний рейтинг по городам среди компаний с 10+ отзывами
SELECT
    ci.name                         AS city,
    ROUND(AVG(co.rating), 2)        AS avg_rating,
    COUNT(*)                        AS companies_considered
FROM companies co
JOIN cities ci ON ci.id = co.city_id
WHERE co.reviews_count >= 10
  AND co.rating IS NOT NULL
GROUP BY ci.name
ORDER BY avg_rating DESC;


-- 3. Доля компаний с сайтом по категориям
SELECT
    c.name                                                       AS category,
    COUNT(*)                                                     AS total_companies,
    COUNT(co.site)                                                AS companies_with_site,
    ROUND(COUNT(co.site)::numeric / COUNT(*) * 100, 1)            AS with_site_share_pct
FROM companies co
JOIN categories c ON c.id = co.category_id
GROUP BY c.name
ORDER BY with_site_share_pct DESC;
