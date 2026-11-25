-- Oracle pipeline (analysis.oracle_signals) — the target's CURRENT
-- decision rows for one sec_type: the incremental write's diff side.
-- The engine re-decides every code's full evaluable grid each run;
-- this read returns what the table holds now (one row per
-- (sec_type, code, date)) so the write can upsert exactly the
-- missing/changed decisions and delete exactly the rows the engine
-- no longer decides. Dates come back as python datetime.date (the
-- comparison's shape — no epoch convention here; that is for frames).
-- args: sec_type: int — static.label_dim 'sec_type' SMALLINT code
    SELECT code, date, action, window_days, confidence,
           ext_date, ext_offset, ext_price
    FROM analysis.oracle_signals
    WHERE sec_type = {{sec_type}}
