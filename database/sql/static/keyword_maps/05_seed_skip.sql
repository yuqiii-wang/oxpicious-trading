-- ============================================================================
--  static.keyword_maps seed — SKIP rows (category_type=3).
--
--  Low-semantic function words: the search-index tokenization post-process
--  (_common.tokenization) drops every segmented token equal to one of
--  these — document vectors stop indexing 其实/也/就是说-class and the/a/of-
--  class noise, and query terms segment to content tokens only. The zh
--  rows match segmented StructBERT tokens; the en rows (language=1)
--  match the whitespace-split passthrough tokens of unsegmented English —
--  case-insensitively (The/THE hit 'the') — English skips the segmenter
--  but still goes through the same post-process. Hand-authored INSERT SQL —
--  THIS FILE is the vocabulary's source of record: add / edit / remove
--  entries here and re-apply (the file re-applies by DELETE of the
--  category_type=3 slice + INSERT; run the whole file in ONE transaction).
--
--  Unlike the extraction types, matching is EXACT-TOKEN equality (a
--  segmented/split word == the keyword, ASCII case-insensitively), so
--  single-char entries are safe (也 can never substring-hit inside another
--  word here). parts_of_speech carries the kw_pos 'function' code (3) for
--  every row — these are 虚词 (adverbs / conjunctions / particles /
--  discourse markers / articles / auxiliaries), not content words.
--
--  DELIBERATELY EXCLUDED — never seed these as skip:
--    * negators 不/没/未/无/非/别 (…) and English no/not/never/without/
--      hardly/barely/cannot/nor: dropping them from search tokens would
--      make 不看多 match 看多 queries (and 'not bullish' match 'bullish')
--      — the negation flip must survive into the tsvector/tsquery pair;
--    * time nouns 今天/明天/昨天 (…): they carry real query semantics
--      (今天的行情), unlike the pure aspect markers here;
--    * the pronoun 'us': case-insensitive matching cannot separate it
--      from US (United States) — a content word in financial news
--      ('US market'); the rare bare pronoun stays indexed instead;
--    * anything already seeded as an extraction keyword (industry /
--      market / broadmarket) — the taxonomy would lose it.
-- ============================================================================

BEGIN;
DELETE FROM static.keyword_maps WHERE category_type = 3;
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation) VALUES
    -- single-char particles / copulas / prepositions / adverbs
    ('的', 3, NULL, NULL, 3, FALSE),
    ('了', 3, NULL, NULL, 3, FALSE),
    ('是', 3, NULL, NULL, 3, FALSE),
    ('在', 3, NULL, NULL, 3, FALSE),
    ('有', 3, NULL, NULL, 3, FALSE),
    ('和', 3, NULL, NULL, 3, FALSE),
    ('与', 3, NULL, NULL, 3, FALSE),
    ('或', 3, NULL, NULL, 3, FALSE),
    ('及', 3, NULL, NULL, 3, FALSE),
    ('等', 3, NULL, NULL, 3, FALSE),
    ('个', 3, NULL, NULL, 3, FALSE),
    ('之', 3, NULL, NULL, 3, FALSE),
    ('以', 3, NULL, NULL, 3, FALSE),
    ('从', 3, NULL, NULL, 3, FALSE),
    ('向', 3, NULL, NULL, 3, FALSE),
    ('把', 3, NULL, NULL, 3, FALSE),
    ('被', 3, NULL, NULL, 3, FALSE),
    ('就', 3, NULL, NULL, 3, FALSE),
    ('都', 3, NULL, NULL, 3, FALSE),
    ('也', 3, NULL, NULL, 3, FALSE),
    ('还', 3, NULL, NULL, 3, FALSE),
    ('又', 3, NULL, NULL, 3, FALSE),
    ('才', 3, NULL, NULL, 3, FALSE),
    ('只', 3, NULL, NULL, 3, FALSE),
    ('更', 3, NULL, NULL, 3, FALSE),
    ('最', 3, NULL, NULL, 3, FALSE),
    ('很', 3, NULL, NULL, 3, FALSE),
    ('再', 3, NULL, NULL, 3, FALSE),
    ('已', 3, NULL, NULL, 3, FALSE),
    -- 说: the segmenter emits it standalone (就是说 -> 就是/说), and a
    -- bare "say" token indexes nothing searchable
    ('说', 3, NULL, NULL, 3, FALSE),
    -- connectives: conjunctions / transition adverbs
    ('但是', 3, NULL, NULL, 3, FALSE),
    ('可是', 3, NULL, NULL, 3, FALSE),
    ('不过', 3, NULL, NULL, 3, FALSE),
    ('然而', 3, NULL, NULL, 3, FALSE),
    ('虽然', 3, NULL, NULL, 3, FALSE),
    ('尽管', 3, NULL, NULL, 3, FALSE),
    ('即使', 3, NULL, NULL, 3, FALSE),
    ('如果', 3, NULL, NULL, 3, FALSE),
    ('假如', 3, NULL, NULL, 3, FALSE),
    ('无论', 3, NULL, NULL, 3, FALSE),
    ('不管', 3, NULL, NULL, 3, FALSE),
    ('因为', 3, NULL, NULL, 3, FALSE),
    ('由于', 3, NULL, NULL, 3, FALSE),
    ('所以', 3, NULL, NULL, 3, FALSE),
    ('因此', 3, NULL, NULL, 3, FALSE),
    ('于是', 3, NULL, NULL, 3, FALSE),
    ('而且', 3, NULL, NULL, 3, FALSE),
    ('并且', 3, NULL, NULL, 3, FALSE),
    ('或者', 3, NULL, NULL, 3, FALSE),
    ('以及', 3, NULL, NULL, 3, FALSE),
    ('还有', 3, NULL, NULL, 3, FALSE),
    ('还是', 3, NULL, NULL, 3, FALSE),
    -- prepositions
    ('对于', 3, NULL, NULL, 3, FALSE),
    ('关于', 3, NULL, NULL, 3, FALSE),
    ('至于', 3, NULL, NULL, 3, FALSE),
    ('除了', 3, NULL, NULL, 3, FALSE),
    ('通过', 3, NULL, NULL, 3, FALSE),
    ('根据', 3, NULL, NULL, 3, FALSE),
    ('按照', 3, NULL, NULL, 3, FALSE),
    ('随着', 3, NULL, NULL, 3, FALSE),
    -- discourse markers / degree & modal adverbs / dummy verbs
    ('其实', 3, NULL, NULL, 3, FALSE),
    ('就是说', 3, NULL, NULL, 3, FALSE),
    ('就是', 3, NULL, NULL, 3, FALSE),
    ('只是', 3, NULL, NULL, 3, FALSE),
    ('只有', 3, NULL, NULL, 3, FALSE),
    ('只要', 3, NULL, NULL, 3, FALSE),
    ('似乎', 3, NULL, NULL, 3, FALSE),
    ('好像', 3, NULL, NULL, 3, FALSE),
    ('大概', 3, NULL, NULL, 3, FALSE),
    ('大约', 3, NULL, NULL, 3, FALSE),
    ('可能', 3, NULL, NULL, 3, FALSE),
    ('应该', 3, NULL, NULL, 3, FALSE),
    ('可以', 3, NULL, NULL, 3, FALSE),
    ('当然', 3, NULL, NULL, 3, FALSE),
    ('确实', 3, NULL, NULL, 3, FALSE),
    ('非常', 3, NULL, NULL, 3, FALSE),
    ('十分', 3, NULL, NULL, 3, FALSE),
    ('比较', 3, NULL, NULL, 3, FALSE),
    ('已经', 3, NULL, NULL, 3, FALSE),
    ('进行', 3, NULL, NULL, 3, FALSE),
    ('一些', 3, NULL, NULL, 3, FALSE),
    ('一下', 3, NULL, NULL, 3, FALSE),
    ('目前', 3, NULL, NULL, 3, FALSE),
    ('现在', 3, NULL, NULL, 3, FALSE),
    ('同时', 3, NULL, NULL, 3, FALSE),
    -- pronouns
    ('这个', 3, NULL, NULL, 3, FALSE),
    ('这些', 3, NULL, NULL, 3, FALSE),
    ('这样', 3, NULL, NULL, 3, FALSE),
    ('那样', 3, NULL, NULL, 3, FALSE),
    ('我们', 3, NULL, NULL, 3, FALSE),
    ('他们', 3, NULL, NULL, 3, FALSE),
    ('自己', 3, NULL, NULL, 3, FALSE),
    ('什么', 3, NULL, NULL, 3, FALSE),
    ('怎么', 3, NULL, NULL, 3, FALSE),
    ('如何', 3, NULL, NULL, 3, FALSE);

-- English skip words (language=en explicitly — the column DEFAULT is zh).
-- Matched case-insensitively against the whitespace-split passthrough
-- tokens of unsegmented English (search_indexing): The/THE hit 'the'.
-- Single-char words whose UPPERCASE form is content-bearing here are NOT
-- seeded: 'a' would eat the A of A/股 (the segmenter splits A股, and an
-- uppercase standalone A in this corpus is the A-share/A50 sense, never
-- an English article) — same for 'i'. The articles' index noise is
-- cheaper than losing A股 keyword reconstruction.
INSERT INTO static.keyword_maps (keyword, category_type, industry_id, category_code, parts_of_speech, is_negation, language) VALUES
    -- articles / determiners ('a' deliberately absent — the A of A股)
    ('an', 3, NULL, NULL, 3, FALSE, 1),
    ('the', 3, NULL, NULL, 3, FALSE, 1),
    ('this', 3, NULL, NULL, 3, FALSE, 1),
    ('that', 3, NULL, NULL, 3, FALSE, 1),
    ('these', 3, NULL, NULL, 3, FALSE, 1),
    ('those', 3, NULL, NULL, 3, FALSE, 1),
    -- pronouns ('us' absent — US the market; 'i' absent — see the note
    -- above: single-char en words stay case-ambiguous here)
    ('me', 3, NULL, NULL, 3, FALSE, 1),
    ('my', 3, NULL, NULL, 3, FALSE, 1),
    ('we', 3, NULL, NULL, 3, FALSE, 1),
    ('our', 3, NULL, NULL, 3, FALSE, 1),
    ('you', 3, NULL, NULL, 3, FALSE, 1),
    ('your', 3, NULL, NULL, 3, FALSE, 1),
    ('he', 3, NULL, NULL, 3, FALSE, 1),
    ('him', 3, NULL, NULL, 3, FALSE, 1),
    ('his', 3, NULL, NULL, 3, FALSE, 1),
    ('she', 3, NULL, NULL, 3, FALSE, 1),
    ('her', 3, NULL, NULL, 3, FALSE, 1),
    ('they', 3, NULL, NULL, 3, FALSE, 1),
    ('them', 3, NULL, NULL, 3, FALSE, 1),
    ('their', 3, NULL, NULL, 3, FALSE, 1),
    ('it', 3, NULL, NULL, 3, FALSE, 1),
    ('its', 3, NULL, NULL, 3, FALSE, 1),
    -- prepositions ('without' absent — negator)
    ('of', 3, NULL, NULL, 3, FALSE, 1),
    ('to', 3, NULL, NULL, 3, FALSE, 1),
    ('in', 3, NULL, NULL, 3, FALSE, 1),
    ('on', 3, NULL, NULL, 3, FALSE, 1),
    ('at', 3, NULL, NULL, 3, FALSE, 1),
    ('by', 3, NULL, NULL, 3, FALSE, 1),
    ('for', 3, NULL, NULL, 3, FALSE, 1),
    ('with', 3, NULL, NULL, 3, FALSE, 1),
    ('from', 3, NULL, NULL, 3, FALSE, 1),
    ('as', 3, NULL, NULL, 3, FALSE, 1),
    ('about', 3, NULL, NULL, 3, FALSE, 1),
    ('after', 3, NULL, NULL, 3, FALSE, 1),
    ('before', 3, NULL, NULL, 3, FALSE, 1),
    ('between', 3, NULL, NULL, 3, FALSE, 1),
    ('during', 3, NULL, NULL, 3, FALSE, 1),
    ('through', 3, NULL, NULL, 3, FALSE, 1),
    ('into', 3, NULL, NULL, 3, FALSE, 1),
    ('onto', 3, NULL, NULL, 3, FALSE, 1),
    ('over', 3, NULL, NULL, 3, FALSE, 1),
    ('under', 3, NULL, NULL, 3, FALSE, 1),
    ('upon', 3, NULL, NULL, 3, FALSE, 1),
    ('within', 3, NULL, NULL, 3, FALSE, 1),
    -- conjunctions / connective adverbs
    ('and', 3, NULL, NULL, 3, FALSE, 1),
    ('or', 3, NULL, NULL, 3, FALSE, 1),
    ('but', 3, NULL, NULL, 3, FALSE, 1),
    ('if', 3, NULL, NULL, 3, FALSE, 1),
    ('then', 3, NULL, NULL, 3, FALSE, 1),
    ('than', 3, NULL, NULL, 3, FALSE, 1),
    ('so', 3, NULL, NULL, 3, FALSE, 1),
    ('because', 3, NULL, NULL, 3, FALSE, 1),
    ('while', 3, NULL, NULL, 3, FALSE, 1),
    ('although', 3, NULL, NULL, 3, FALSE, 1),
    ('though', 3, NULL, NULL, 3, FALSE, 1),
    ('however', 3, NULL, NULL, 3, FALSE, 1),
    ('therefore', 3, NULL, NULL, 3, FALSE, 1),
    ('thus', 3, NULL, NULL, 3, FALSE, 1),
    ('also', 3, NULL, NULL, 3, FALSE, 1),
    ('moreover', 3, NULL, NULL, 3, FALSE, 1),
    ('furthermore', 3, NULL, NULL, 3, FALSE, 1),
    ('yet', 3, NULL, NULL, 3, FALSE, 1),
    -- copulas / auxiliaries / modals
    ('is', 3, NULL, NULL, 3, FALSE, 1),
    ('are', 3, NULL, NULL, 3, FALSE, 1),
    ('was', 3, NULL, NULL, 3, FALSE, 1),
    ('were', 3, NULL, NULL, 3, FALSE, 1),
    ('be', 3, NULL, NULL, 3, FALSE, 1),
    ('been', 3, NULL, NULL, 3, FALSE, 1),
    ('being', 3, NULL, NULL, 3, FALSE, 1),
    ('am', 3, NULL, NULL, 3, FALSE, 1),
    ('do', 3, NULL, NULL, 3, FALSE, 1),
    ('does', 3, NULL, NULL, 3, FALSE, 1),
    ('did', 3, NULL, NULL, 3, FALSE, 1),
    ('have', 3, NULL, NULL, 3, FALSE, 1),
    ('has', 3, NULL, NULL, 3, FALSE, 1),
    ('had', 3, NULL, NULL, 3, FALSE, 1),
    ('will', 3, NULL, NULL, 3, FALSE, 1),
    ('would', 3, NULL, NULL, 3, FALSE, 1),
    ('shall', 3, NULL, NULL, 3, FALSE, 1),
    ('should', 3, NULL, NULL, 3, FALSE, 1),
    ('can', 3, NULL, NULL, 3, FALSE, 1),
    ('could', 3, NULL, NULL, 3, FALSE, 1),
    ('may', 3, NULL, NULL, 3, FALSE, 1),
    ('might', 3, NULL, NULL, 3, FALSE, 1),
    ('must', 3, NULL, NULL, 3, FALSE, 1),
    -- wh-words (the 什么/怎么/如何 counterparts)
    ('what', 3, NULL, NULL, 3, FALSE, 1),
    ('how', 3, NULL, NULL, 3, FALSE, 1),
    ('why', 3, NULL, NULL, 3, FALSE, 1),
    ('when', 3, NULL, NULL, 3, FALSE, 1),
    ('where', 3, NULL, NULL, 3, FALSE, 1),
    ('who', 3, NULL, NULL, 3, FALSE, 1),
    ('whom', 3, NULL, NULL, 3, FALSE, 1),
    ('whose', 3, NULL, NULL, 3, FALSE, 1),
    ('which', 3, NULL, NULL, 3, FALSE, 1),
    -- degree adverbs / quantifiers
    ('very', 3, NULL, NULL, 3, FALSE, 1),
    ('quite', 3, NULL, NULL, 3, FALSE, 1),
    ('rather', 3, NULL, NULL, 3, FALSE, 1),
    ('just', 3, NULL, NULL, 3, FALSE, 1),
    ('only', 3, NULL, NULL, 3, FALSE, 1),
    ('even', 3, NULL, NULL, 3, FALSE, 1),
    ('again', 3, NULL, NULL, 3, FALSE, 1),
    ('almost', 3, NULL, NULL, 3, FALSE, 1),
    ('nearly', 3, NULL, NULL, 3, FALSE, 1),
    ('more', 3, NULL, NULL, 3, FALSE, 1),
    ('most', 3, NULL, NULL, 3, FALSE, 1),
    ('much', 3, NULL, NULL, 3, FALSE, 1),
    ('many', 3, NULL, NULL, 3, FALSE, 1),
    ('some', 3, NULL, NULL, 3, FALSE, 1),
    ('any', 3, NULL, NULL, 3, FALSE, 1);
COMMIT;
