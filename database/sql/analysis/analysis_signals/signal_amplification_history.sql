-- ============================================================================
--  Table: analysis_signals.signal_amplification_history
--
--  SIGNAL AMPLIFICATION HISTORY (2026-11-18; the 2026-11-20 two-level
--  amplifier axis) — the ORIGINAL RECORD KEEPER of the
--  signal-amplification pair: one row per amplifier DAY (code,
--  sec_type, amplifier_type, sub_type, date) with the day's anatomy
--  facts and, per BOUND signal family, the amplification applied that
--  day (base_bp depth-scaled → amplify_bp) and whether a bound-family
--  signal actually fired that day.
--
--  THE TWO-LEVEL AMPLIFIER AXIS (2026-11-20):
--    amplifier_type  the PARENT TYPE ('signal_amplifier_type' domain):
--                   0 ohlcv (K-line day anatomy) / 1 margin (融资融券
--                   margin-balance level)
--    sub_type        the specific pattern under the parent — decodes
--                   via 'signal_amplifier' when ohlcv,
--                   'signal_amplifier_margin' when margin (the
--                   external_resource_audit resource_type/sub_type
--                   precedent)
--
--  The owning binding config lives in signal_amplification_rules —
--  one row per (code, sec_type, signal_type) whose rule_params JSONB
--  carries the amplifiers / bars / evidence; a history row's
--  `bindings` array restates THAT DAY's per-family results
--  (denormalized per event, the history_signals convention).
--
--  Every amplifier day is recorded whether or not a bound family
--  fired — the anatomy fact (e.g. a flush-washout day, a margin-balance
--  low) is the record; bindings[].fired links the day to the bound
--  families' own history_signals events.
--
--  FACT COLUMNS (the common anatomy; the amplifier-specific extras
--  ride amplifier_params):
--    gap_pct        the overnight gap (ohlcv_ext.prev_close_gap) — populated
--                   for the gap-driven amplifiers, NULL otherwise
--    wick_pct       the amplifier's RELEVANT wick (lw for the flush side,
--                   uw for the spike side), NULL for non-wick amplifiers
--    amt_ratio      trading_amount / its trailing 20-row MA (the 放量/缩量
--                   gauge) — populated for every amplifier
--    close_pos      (close - low) / (high - low) — populated for every amplifier
--    margin_vs_bar  the MARGIN amplifiers' qualifying fact: rz_balance
--                   vs the sub type's own trailing-252d quantile bar
--                   ((balance - bar) / bar — negative below the q10 low
--                   bar, positive above the q90 high bar); NULL for the
--                   ohlcv amplifiers
--    depth          the amplifier's depth term in its own normalizer units
--                   (the confidence scale input)
--
--  Populated by python -m analysis.analysis_signals.signal_amplification
--  (incremental past the table's max date; --force deletes the
--  sec_type's rows and re-emits).
-- ============================================================================
-- kg entity (ns0 db_semantics_tables reads the payload below):
-- description: The signal amplification event history — one row per amplifier day (the two-level amplifier axis: ohlcv K-line anatomy types or margin 融资余额 level types, each with its own sub types) with the day's anatomy facts (overnight gap, wick, volume ratio, close position, margin vs bar, depth) and the per-bound-family amplification applied (base bp depth-scaled to amplify bp, whether a bound family signal fired that day).
-- tags: signal amplification, 信号放大, history, 历史, amplifier day, 放大日, amplifier type, 放大器类型, sub type, 子类型, ohlcv anatomy, K线形态, margin, 融资融券, 融资余额, gap, 跳空, wick, 影线, volume, 量能, binding, 绑定

DROP TABLE IF EXISTS analysis_signals.signal_amplification_history CASCADE;

CREATE TABLE analysis_signals.signal_amplification_history (
    code            TEXT          NOT NULL,  -- ticker (index "000001" / etf "510050" / stock)
    sec_type        SMALLINT      NOT NULL,  -- label code (static.label_dim 'sec_type'): 0=etf 1=index 2=stock
    amplifier_type  SMALLINT      NOT NULL,  -- the PARENT TYPE, label code (static.label_dim 'signal_amplifier_type'): 0=ohlcv 1=margin
    sub_type        SMALLINT      NOT NULL,  -- the specific pattern under the parent: static.label_dim 'signal_amplifier' code when amplifier_type=0 (0=flush_washout .. 5=dry_rebound), static.label_dim 'signal_amplifier_margin' code when amplifier_type=1 (0=margin_washout 1=margin_crowding)
    date            DATE          NOT NULL,  -- the amplifier day (the anatomy fact's own day)

    gap_pct         NUMERIC(10,6),           -- the day's overnight gap (ohlcv_ext.prev_close_gap, signed fraction) — populated for the gap-driven amplifiers
    wick_pct        NUMERIC(10,6),           -- the amplifier's relevant wick (lw flush side / uw spike side, signed fraction) — NULL for non-wick amplifiers
    amt_ratio       NUMERIC(10,6),           -- trading_amount / its trailing 20-row MA (the 放量/缩量 gauge)
    close_pos       NUMERIC(10,6),           -- (close - low) / (high - low) — the close location in range
    margin_vs_bar   NUMERIC(10,6),           -- the MARGIN amplifiers' qualifying fact: (rz_balance - its sub type's trailing-252d quantile bar) / bar — negative below the q10 low bar (margin_washout), positive above the q90 high bar (margin_crowding); NULL for the ohlcv amplifiers
    depth           NUMERIC(10,6),           -- the amplifier's depth term in its own normalizer units (the confidence scale input)

    bindings        JSONB         NOT NULL,  -- per BOUND signal family: [{"signal_type": <label code>, "action": 0|1, "base_bp": 150, "amplify_bp": 240, "fired": true, "fired_n": 1}, ...] — amplify_bp = ROUND(base_bp * (1 + depth_factor * min(depth, depth_cap))) capped at |cap_bp|; fired = a bound-family history_signals event (standalone tier, confidence >= 0) exists on this (code, signal_type, date)
    amplifier_params JSONB        NOT NULL,  -- the day's record: the amplifier's bar_def constants + the full anatomy facts (per-amplifier extras beyond the common columns)

    created_at      TIMESTAMP     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_signal_amplification_history PRIMARY KEY (code, sec_type, amplifier_type, sub_type, date),
    CONSTRAINT chk_signal_amplification_history_sec_type CHECK (sec_type BETWEEN 0 AND 2)
) PARTITION BY HASH (code);

SELECT public.create_hash_partitions('analysis_signals', 'signal_amplification_history', 16);

-- Date-first lookup (UI / "amplifier days on day X for sec_type Y").
CREATE INDEX IF NOT EXISTS idx_signal_amplification_history_date
    ON analysis_signals.signal_amplification_history (sec_type, amplifier_type, sub_type, date);

COMMENT ON TABLE  analysis_signals.signal_amplification_history IS 'Signal AMPLIFICATION HISTORY — the ORIGINAL RECORD KEEPER: one row per amplifier day (code, sec_type, amplifier_type, sub_type, date — the two-level amplifier axis: amplifier_type the PARENT (signal_amplifier_type: 0 ohlcv / 1 margin), sub_type the specific pattern (signal_amplifier when ohlcv, signal_amplifier_margin when margin)) with the day''s anatomy facts (gap_pct / wick_pct / amt_ratio / close_pos / margin_vs_bar / depth) and the per-bound-family amplification applied that day (bindings JSONB: base_bp depth-scaled to amplify_bp, fired = a bound family''s history_signals event exists that day). Every amplifier day is recorded whether or not a bound family fired. The binding config lives in signal_amplification_rules. Populated by python -m analysis.analysis_signals.signal_amplification; --force deletes the sec_type''s rows and re-emits.';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.amplifier_type IS 'The amplifier PARENT TYPE as its static.label_dim (''signal_amplifier_type'') domain SMALLINT code: 0 ohlcv (the K-line day anatomy amplifiers — gap/wick/volume shapes), 1 margin (the 融资融券 margin-balance level amplifiers — rz_balance vs its own trailing quantile bars; indices read the analysis.margin_index_series weighted aggregate).';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.sub_type IS 'The specific pattern under the parent type — decodes via static.label_dim ''signal_amplifier'' when amplifier_type = 0 (ohlcv): 0 flush_washout (探底洗出 — lower-wick top-decile flush + heavy volume; BUY reversion), 1 gap_reclaim (低开收复 — down open reclaimed by the close; BUY reversion), 2 breakout_confirm (放量突破确认 — close above the prior high + heavy volume + strong close; BUY continuation), 3 spike_blowoff (冲高回落放量 — upper-wick top-decile spike faded into a weak close + heavy volume; SELL exit), 4 exhaustion_gap (高开低走竭尽 — up gap + red body; SELL exit), 5 dry_rebound (缩量弱反弹 — up close on shrinking volume; BUY DISCOUNT, negative bp); via static.label_dim ''signal_amplifier_margin'' when amplifier_type = 1 (margin): 0 margin_washout (融资余额低位 — rz_balance at/below its trailing-252d q10 bar; leverage washed out, low risk + lower price; BUY reversion), 1 margin_crowding (融资余额高位 — rz_balance at/above its trailing-252d q90 bar; leverage crowding/fragility; SELL exit).';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.gap_pct IS 'The day''s overnight gap (ohlcv_ext.prev_close_gap, signed fraction: open / prev_close - 1). Populated for the gap-driven amplifiers (gap_reclaim, exhaustion_gap); NULL for the others.';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.wick_pct IS 'The amplifier''s RELEVANT wick in return units: lw (lower wick) for flush_washout, uw (upper wick) for spike_blowoff; NULL for the non-wick amplifiers.';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.amt_ratio IS 'trading_amount / its trailing 20-row MA (ohlcv_ext.amt_ratio_20) — the per-code participation gauge (> 1 放量 expanding, < 1 缩量 shrinking). Populated for every amplifier.';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.close_pos IS '(close - low) / (high - low) (ohlcv_ext.close_pos) — the close location in the day''s range [0, 1]. Populated for every amplifier.';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.margin_vs_bar IS 'The MARGIN amplifiers'' qualifying fact: (rz_balance - the sub type''s own trailing-252d quantile bar) / bar — the signed fraction of the margin balance vs its bar: negative at/below the q10 low bar (margin_washout fires), positive at/above the q90 high bar (margin_crowding fires). The raw balance and both bars ride amplifier_params.facts. NULL for the ohlcv amplifiers.';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.depth IS 'The amplifier''s depth term in its own normalizer units — the input to the confidence scale base_bp * (1 + depth_factor * min(depth, depth_cap)) (the CellGate convention). Semantics per sub type (e.g. flush_washout: the lower wick in its trailing-q90 units; margin_washout: the balance''s fraction BELOW its q10 bar; margin_crowding: the fraction ABOVE its q90 bar).';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.bindings IS 'The day''s per-BOUND-FAMILY amplification results — one entry per signal family the code''s config binds this amplifier to: {signal_type (static.label_dim ''signal_type'' code), action (the side amplified: 0 buy / 1 sell), base_bp, amplify_bp (the depth-scaled, capped strength applied that day; NEGATIVE for a discount amplifier e.g. dry_rebound), fired (a bound-family history_signals event — standalone tier, confidence >= 0 — exists on this code/date), fired_n (the count of those events)}.';
COMMENT ON COLUMN analysis_signals.signal_amplification_history.amplifier_params IS 'The day''s record: the amplifier''s constant bar_def (the bars that qualified the day) + the full anatomy facts including the amplifier-specific extras beyond the common columns (e.g. close_vs_prev_high for breakout_confirm; margin_balance/margin_bar_low/margin_bar_high for the margin amplifiers). Engine-generated.';
