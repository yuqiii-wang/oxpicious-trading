-- Market crowdedness — the (hot_year, date) pairs already present in
-- analysis.market_crowdedness_year for one sec_type: the incremental
-- write's post-compute safety filter anti-joins them (except the open
-- year's rows, whose variant is always re-upserted — its set
-- re-derives nightly). date arrives as epoch float for the
-- datetime64[us] frame boundary.
-- args:
--   sec_type: value smallint — the static.label_dim sec_type code
    SELECT DISTINCT hot_year,
           extract(epoch from date)::float8 AS date
    FROM analysis.market_crowdedness_year
    WHERE sec_type = {{sec_type}}::smallint
