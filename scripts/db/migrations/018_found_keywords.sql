-- 018_found_keywords: список ключевых слов (поисковых запросов), по которым найден пост.
-- Пост хранится один раз, а находок у него может быть много (post_hits -> search_jobs.query);
-- posts.found_keywords держит те же запросы готовым списком (без повторов, в порядке находки).
-- Ключ нормализован: пробелы схлопнуты, нижний регистр. Посты, собранные только профилем, -> '{}'.
SET search_path = threads;

ALTER TABLE posts ADD COLUMN found_keywords TEXT[] NOT NULL DEFAULT '{}';

UPDATE posts p SET found_keywords = k.kw
FROM (
    SELECT q.post_code, array_agg(q.query ORDER BY q.first_seen) AS kw
    FROM (
        SELECT h.post_code, lower(btrim(regexp_replace(sj.query, '\s+', ' ', 'g'))) AS query,
               min(h.seen_at) AS first_seen
        FROM post_hits h JOIN search_jobs sj ON sj.id = h.job_id
        GROUP BY 1, 2
    ) q
    GROUP BY q.post_code
) k
WHERE k.post_code = p.code;

CREATE INDEX posts_found_keywords ON posts USING GIN (found_keywords);

COMMENT ON COLUMN posts.found_keywords IS
    'Все запросы поиска, по которым найден пост (нижний регистр, без повторов, в порядке находки)';
