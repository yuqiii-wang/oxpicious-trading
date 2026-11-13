
-- Reference set: the group of news articles an AI QA answer was based on.
-- Plain entity table — deliberately NOT INHERITS (text.news): table
-- inheritance would leak news_groups rows into SELECT * FROM text.news.
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Reference sets of articles cited together — e.g. the sources behind one AI answer.
-- tags: news groups, 新闻组, reference set, 引用集合

CREATE TABLE IF NOT EXISTS text.news_groups (
    news_group_id SERIAL PRIMARY KEY,
    news_group_name TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE text.news_groups IS
  'Reference-set entity: a group of news articles cited together (e.g. by '
  'one AI QA answer). Membership is in text.news_group_items.';
