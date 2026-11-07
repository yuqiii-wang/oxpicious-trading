-- Industry-attributions step (analysis.market_beta internal pipeline)
-- guard — bail out early if the stats.cross_stats INDUSTRY grain (built
-- by builds.cross_stats from the pair rows) is empty; it feeds the
-- broad-market INSERT.
-- args: none
    SELECT COUNT(*) AS n
    FROM stats.cross_stats
    WHERE sec_type = 'industry'
