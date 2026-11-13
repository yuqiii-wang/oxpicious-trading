
-- Junction: group membership. One group may cite many articles; one article
-- may be cited by many groups.
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: Which articles belong to which reference group.
-- tags: news groups, 新闻组, membership, 成员

CREATE TABLE IF NOT EXISTS text.news_group_items (
    news_group_id INTEGER     NOT NULL,
    news_id       BIGINT      NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_news_group_items PRIMARY KEY (news_group_id, news_id),
    CONSTRAINT fk_news_group_items_news_groups FOREIGN KEY (news_group_id)
        REFERENCES text.news_groups (news_group_id),
    CONSTRAINT fk_news_group_items_news FOREIGN KEY (news_id)
        REFERENCES text.news (news_id)
);

COMMENT ON TABLE text.news_group_items IS
  'Junction between text.news_groups and text.news: the articles each group '
  'cites. PK keeps membership idempotent on re-insert.';
