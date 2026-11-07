-- Options IV Skew Stats — per-expiry implied-volatility skew metrics from
-- analysis.options_iv_skew_stats, averaged per (date, expiry month): AVG
-- across CALL/PUT rows (pair metrics are duplicated per option_type;
-- smile_skewness differs per type, so the mean is the group-level value).
-- MAX(expiry_date) gives the latest exact expiry in the month group (for
-- the frontend shade boundaries / expiry marks). Serves
-- GET /api/options/iv-skew.
-- args:
--   where_clause: fragment — the scope WHERE conditions joined " AND "
--     (starts with the fixed "underlying_code = $1")
    SELECT
      date,
      underlying_code,
      DATE_TRUNC('month', expiry_date) AS expiry_month,
      MAX(expiry_date) AS expiry_date,
      AVG(atm_iv) AS atm_iv,
      AVG(iv_call25) AS iv_call25,
      AVG(iv_put25) AS iv_put25,
      AVG(risk_reversal_25d) AS risk_reversal_25d,
      AVG(put_skew_25d) AS put_skew_25d,
      AVG(call_skew_25d) AS call_skew_25d,
      AVG(iv_call10) AS iv_call10,
      AVG(iv_put10) AS iv_put10,
      AVG(risk_reversal_10d) AS risk_reversal_10d,
      AVG(smile_skewness) AS smile_skewness,
      AVG(rr25_ma5) AS rr25_ma5,
      AVG(rr25_ma20) AS rr25_ma20,
      AVG(rr25_ma60) AS rr25_ma60,
      AVG(corr_rr25_ma5_vs_spot_ma5) AS corr_rr25_ma5_vs_spot_ma5,
      AVG(corr_rr25_ma20_vs_spot_ma20) AS corr_rr25_ma20_vs_spot_ma20,
      AVG(corr_rr25_ma60_vs_spot_ma60) AS corr_rr25_ma60_vs_spot_ma60
    FROM analysis.options_iv_skew_stats
    WHERE {{where_clause}}
    GROUP BY date, underlying_code, DATE_TRUNC('month', expiry_date)
    ORDER BY date ASC, DATE_TRUNC('month', expiry_date) ASC
