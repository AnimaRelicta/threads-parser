-- 017_post_category: категория ПОСТА (classify/jev_posts_*.py), отдельно от категории профиля.
-- Те же категории (categories), но результат относится к тексту поста: у поста нет review_status.
SET search_path = threads;

ALTER TABLE posts
    ADD COLUMN category_id SMALLINT REFERENCES categories(id),
    ADD COLUMN category_raw JSONB,
    ADD COLUMN category_model TEXT,
    ADD COLUMN category_prompt_version SMALLINT;

CREATE INDEX posts_category ON posts (category_id) WHERE category_id IS NOT NULL;

COMMENT ON COLUMN posts.category_id IS 'Категория ПОСТА (categories.id), результат cli.py jev-posts';
COMMENT ON COLUMN posts.category_raw IS
    'Ответ Jev: category, top, top_prob, fallback_reason, confidence, noul, probabilities, токены, error';
