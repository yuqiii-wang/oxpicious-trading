-- Industry-attributions runner force-mode date axis — the source
-- industry-grain dates to materialize in date-batched INSERT..SELECT
-- phases (full recompute; the INCREMENTAL INSERT variants run in BOTH
-- modes, so force mode batches over this axis instead of one
-- all-at-once statement).
-- args: none
SELECT DISTINCT date FROM stats.cross_stats WHERE sec_type = 'industry' ORDER BY date
