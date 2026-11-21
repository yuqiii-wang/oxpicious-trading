-- Oracle — the codes list for the Signals Oracle page (codes that HAVE
-- decisions in analysis.oracle_signals for the sec_type — the
-- pipeline's run scope; index for now = the 10 study indices), with
-- each code's latest identity display name.
-- Serves GET /api/analysis/oracle/codes
-- args:
--   sec_type: value smallint — LABEL_CODE.sec_type[secType]
    SELECT DISTINCT ON (v.code) v.code, ii.name
    FROM analysis.oracle_signals v
    JOIN stats.index_identity ii
      ON ii.code = v.code AND ii.date = v.date
    WHERE v.sec_type = {{sec_type}}
    ORDER BY v.code, v.date DESC
