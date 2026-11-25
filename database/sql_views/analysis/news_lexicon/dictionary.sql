-- The CFSD sentiment dictionary — static.fin_sentiment_dict as the scorer's
-- runtime vocabulary (word -> fin_dict_polarity code). Small static read,
-- fetched whole every scoring run.
-- args: (none)
SELECT word,
       polarity::int4 AS polarity
FROM static.fin_sentiment_dict
