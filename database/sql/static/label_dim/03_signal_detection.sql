-- ============================================================================
--  static.label_dim seed — SIGNAL DETECTION vocabularies
--
--  The analysis_signals detection family and its cache: which family /
--  config grid entry produced a signal, the reversal action, and the
--  intermediate-result cache's tenant. Owning tables:
--  analysis_signals.signal_thresholds / history_signals / signal_curves,
--  analysis_signals.analysis_cache, live.live_signals (action).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- action: 0 buy (reversal up), 1 sell (down) — pairs with the
    -- excess-sign convention (sell <-> positive signal_excess);
    -- 2 wait (2026-10-13 as no_action; RENAMED 2026-10-16): a rule
    -- not trading yet — signal_rules' observational outcomes (the
    -- VRP-declined twin / the sample-starved cells waiting for
    -- evidence to graduate)
    ('action',     0, 'buy',    0),
    ('action',     1, 'sell',   1),
    ('action',     2, 'wait',   2),
    -- signal_type: the analysis_signals detection family (converted
    -- 2026-10-10; registry order = pipeline order)
    ('signal_type', 0, 'mov_rsi',  0),
    ('signal_type', 1, 'bollinger', 1),
    ('signal_type', 2, 'ma_cross', 2),
    ('signal_type', 3, 'options_wall', 3),
    ('signal_type', 4, 'options_vix', 4),
    -- momentum (2026-10-15): the log-return momentum family — value
    -- axis ln(1 + momentum) of analysis.mov_ave_momentum (fixed 5/20d
    -- anchors + the vol-adaptive short/mid/long lookbacks); bars are
    -- the per-code trailing percentile quantiles (self-adaptive
    -- multi-level: the nested 1/2/3% tail carving)
    ('signal_type', 5, 'momentum', 5),
    -- rf_break (2026-10-16): the roof/floor Donchian breakout family —
    -- the close crossing the PRIOR day's donchian-method roof/floor
    -- level (analysis.mov_ave_spreads_roof_floor_estimates, method 1);
    -- a structural-bar family (no percentile tail), regime-free with a
    -- trend split (the ma_cross precedent — the 2024-09-30 / 2026-10-15
    -- studies showed the cell grid starving exactly at the extreme
    -- (top, hot) / (bottom, calm) corners where these events land)
    ('signal_type', 6, 'rf_break', 6),
    -- trend (2026-10-14, with the signal_rules redesign; rise/fall
    -- redefined 2026-10-15): the code's own trend classification in
    -- force for a rules cell — the PK member that makes rule_params
    -- per-PK (the same sub signal type measures asymmetric populations
    -- in different trends). Classified in-engine per code: top/bottom
    -- the price_vs_ma255 extremes beyond their own trailing-255d
    -- q90/q10 with matching slope; rise/fall the Wilder-RSI10
    -- RE-ENTRY episodes (fixed 50±12.5 momentum zones, <= 5-row
    -- re-entry absorb, the Kaufman net-change efficiency guard, and
    -- the DEEP-ANCHOR TURN:
    -- a deep RSI <=30/>=70 touch opens a 40-row window where the
    -- midline-50 re-cross with slope flips the trend — replaced the
    -- delayed price-vs-MA60 states); 5 unknown neither episode (RSI
    -- mid-band / warmup). 4 = the POOLED stamp of the sparse
    -- non-trend-split families' rule rows (the regime 'all'
    -- precedent) — never a day state
    ('trend', 0, 'top',    0),
    ('trend', 1, 'bottom', 1),
    ('trend', 2, 'rise',   2),
    ('trend', 3, 'fall',   3),
    ('trend', 4, 'all',    4),
    ('trend', 5, 'unknown', 5),
    -- signal_sub_type: the family config grid (converted 2026-10-11;
    -- SERIES codes 0.. = the curve cell identity — signal_curves;
    -- THRESHOLD codes = (series + 1) * 100 + width ordinal — the
    -- threshold/history rows; join series = code < 100 ? code :
    -- code // 100 - 1 (label_dim is UNIQUE(domain, label), so the
    -- SINGLE-WIDTH retest kinds use their series code for BOTH
    -- roles — one label, one code)
    ('signal_sub_type', 0, 'rsi3', 0),
    ('signal_sub_type', 1, 'rsi6', 1),
    ('signal_sub_type', 2, 'rsi10', 2),
    ('signal_sub_type', 3, 'rsi14', 3),
    ('signal_sub_type', 4, 'rsi20', 4),
    ('signal_sub_type', 5, 'rsi60', 5),
    ('signal_sub_type', 6, 'boll5', 6),
    ('signal_sub_type', 7, 'boll20', 7),
    ('signal_sub_type', 8, 'boll60', 8),
    ('signal_sub_type', 9, 'ma20_cross', 9),
    ('signal_sub_type', 10, 'ma60_cross', 10),
    ('signal_sub_type', 11, 'ma20_retest', 11),
    ('signal_sub_type', 12, 'ma60_retest', 12),
    ('signal_sub_type', 13, 'ma120_deep', 13),
    ('signal_sub_type', 14, 'ma255_deep', 14),
    ('signal_sub_type', 15, 'ma20_ecross', 15),
    ('signal_sub_type', 16, 'ma60_ecross', 16),
    ('signal_sub_type', 17, 'ma20_eretest', 17),
    ('signal_sub_type', 18, 'ma60_eretest', 18),
    ('signal_sub_type', 19, 'ma120_edeep', 19),
    ('signal_sub_type', 20, 'ma255_edeep', 20),
    ('signal_sub_type', 21, 'ma20_gap', 21),
    ('signal_sub_type', 22, 'ma60_gap', 22),
    ('signal_sub_type', 23, 'ma120_gap', 23),
    ('signal_sub_type', 24, 'ma255_gap', 24),
    ('signal_sub_type', 25, 'ma20_egap', 25),
    ('signal_sub_type', 26, 'ma60_egap', 26),
    ('signal_sub_type', 27, 'ma120_egap', 27),
    ('signal_sub_type', 28, 'ma255_egap', 28),
    ('signal_sub_type', 100, 'rsi3_1pct', 100),
    ('signal_sub_type', 101, 'rsi3_2pct', 101),
    ('signal_sub_type', 102, 'rsi3_3pct', 102),
    ('signal_sub_type', 200, 'rsi6_1pct', 200),
    ('signal_sub_type', 201, 'rsi6_2pct', 201),
    ('signal_sub_type', 202, 'rsi6_3pct', 202),
    ('signal_sub_type', 300, 'rsi10_1pct', 300),
    ('signal_sub_type', 301, 'rsi10_2pct', 301),
    ('signal_sub_type', 302, 'rsi10_3pct', 302),
    ('signal_sub_type', 400, 'rsi14_1pct', 400),
    ('signal_sub_type', 401, 'rsi14_2pct', 401),
    ('signal_sub_type', 402, 'rsi14_3pct', 402),
    ('signal_sub_type', 500, 'rsi20_1pct', 500),
    ('signal_sub_type', 501, 'rsi20_2pct', 501),
    ('signal_sub_type', 502, 'rsi20_3pct', 502),
    ('signal_sub_type', 600, 'rsi60_1pct', 600),
    ('signal_sub_type', 601, 'rsi60_2pct', 601),
    ('signal_sub_type', 602, 'rsi60_3pct', 602),
    ('signal_sub_type', 700, 'boll5_2std', 700),
    ('signal_sub_type', 701, 'boll5_2p5std', 701),
    ('signal_sub_type', 702, 'boll5_3std', 702),
    ('signal_sub_type', 800, 'boll20_2std', 800),
    ('signal_sub_type', 801, 'boll20_2p5std', 801),
    ('signal_sub_type', 802, 'boll20_3std', 802),
    ('signal_sub_type', 900, 'boll60_2std', 900),
    ('signal_sub_type', 901, 'boll60_2p5std', 901),
    ('signal_sub_type', 902, 'boll60_3std', 902),
    ('signal_sub_type', 1000, 'ma20_s11', 1000),
    ('signal_sub_type', 1001, 'ma20_s21', 1001),
    ('signal_sub_type', 1002, 'ma20_s42', 1002),
    ('signal_sub_type', 1100, 'ma60_s11', 1100),
    ('signal_sub_type', 1101, 'ma60_s21', 1101),
    ('signal_sub_type', 1102, 'ma60_s42', 1102),
    ('signal_sub_type', 1400, 'ma120_d8pct', 1400),
    ('signal_sub_type', 1401, 'ma120_d12pct', 1401),
    ('signal_sub_type', 1500, 'ma255_d8pct', 1500),
    ('signal_sub_type', 1501, 'ma255_d12pct', 1501),
    ('signal_sub_type', 1600, 'ma20_es11', 1600),
    ('signal_sub_type', 1601, 'ma20_es21', 1601),
    ('signal_sub_type', 1602, 'ma20_es42', 1602),
    ('signal_sub_type', 1700, 'ma60_es11', 1700),
    ('signal_sub_type', 1701, 'ma60_es21', 1701),
    ('signal_sub_type', 1702, 'ma60_es42', 1702),
    ('signal_sub_type', 3000, 'ma20_x1pct', 3000),
    ('signal_sub_type', 3001, 'ma20_x2pct', 3001),
    ('signal_sub_type', 3002, 'ma20_x3pct', 3002),
    ('signal_sub_type', 3100, 'ma60_x1pct', 3100),
    ('signal_sub_type', 3101, 'ma60_x2pct', 3101),
    ('signal_sub_type', 3102, 'ma60_x3pct', 3102),
    ('signal_sub_type', 3200, 'ma20_ex1pct', 3200),
    ('signal_sub_type', 3201, 'ma20_ex2pct', 3201),
    ('signal_sub_type', 3202, 'ma20_ex3pct', 3202),
    ('signal_sub_type', 3300, 'ma60_ex1pct', 3300),
    ('signal_sub_type', 3301, 'ma60_ex2pct', 3301),
    ('signal_sub_type', 3302, 'ma60_ex3pct', 3302),
    ('signal_sub_type', 2000, 'ma120_ed8pct', 2000),
    ('signal_sub_type', 2001, 'ma120_ed12pct', 2001),
    ('signal_sub_type', 2100, 'ma255_ed8pct', 2100),
    ('signal_sub_type', 2101, 'ma255_ed12pct', 2101),
    ('signal_sub_type', 2200, 'ma20_g3pct', 2200),
    ('signal_sub_type', 2201, 'ma20_g5pct', 2201),
    ('signal_sub_type', 2202, 'ma20_g8pct', 2202),
    ('signal_sub_type', 2300, 'ma60_g3pct', 2300),
    ('signal_sub_type', 2301, 'ma60_g5pct', 2301),
    ('signal_sub_type', 2302, 'ma60_g8pct', 2302),
    ('signal_sub_type', 2400, 'ma120_g3pct', 2400),
    ('signal_sub_type', 2401, 'ma120_g5pct', 2401),
    ('signal_sub_type', 2402, 'ma120_g8pct', 2402),
    ('signal_sub_type', 2500, 'ma255_g3pct', 2500),
    ('signal_sub_type', 2501, 'ma255_g5pct', 2501),
    ('signal_sub_type', 2502, 'ma255_g8pct', 2502),
    ('signal_sub_type', 2600, 'ma20_eg3pct', 2600),
    ('signal_sub_type', 2601, 'ma20_eg5pct', 2601),
    ('signal_sub_type', 2602, 'ma20_eg8pct', 2602),
    ('signal_sub_type', 2700, 'ma60_eg3pct', 2700),
    ('signal_sub_type', 2701, 'ma60_eg5pct', 2701),
    ('signal_sub_type', 2702, 'ma60_eg8pct', 2702),
    ('signal_sub_type', 2800, 'ma120_eg3pct', 2800),
    ('signal_sub_type', 2801, 'ma120_eg5pct', 2801),
    ('signal_sub_type', 2802, 'ma120_eg8pct', 2802),
    ('signal_sub_type', 2900, 'ma255_eg3pct', 2900),
    ('signal_sub_type', 2901, 'ma255_eg5pct', 2901),
    ('signal_sub_type', 2902, 'ma255_eg8pct', 2902),
    -- options_wall (2026-10-12): the OI-wall breach family — SERIES
    -- 40/41 (ow_put the put-wall BUY series, ow_call the call-wall
    -- SELL series; thresholds only, no curve rows — the per-code
    -- breach cells sit far under the curve emit floor), THRESHOLD
    -- codes (series + 1) * 100 + width ordinal at 4100/4200/4201
    -- (0.25% / 1% breach bars). The 2026-10-13 hit-and-bounce
    -- additions at 4102/4103/4202: ow_put_center_vrp BUY (the close
    -- within 1% above the put zone CENTER, VRP-gated; _rej the
    -- VRP-fail twin) / ow_call_approach_rally SELL (the close within
    -- 1.5% below the call cap on an up-day)
    ('signal_sub_type', 40, 'ow_put', 40),
    ('signal_sub_type', 41, 'ow_call', 41),
    ('signal_sub_type', 4100, 'ow_put_025pct', 4100),
    ('signal_sub_type', 4101, 'ow_put_025pct_rej', 4101),
    ('signal_sub_type', 4102, 'ow_put_center_vrp', 4102),
    ('signal_sub_type', 4103, 'ow_put_center_vrp_rej', 4103),
    ('signal_sub_type', 4200, 'ow_call_025pct', 4200),
    ('signal_sub_type', 4201, 'ow_call_100pct', 4201),
    ('signal_sub_type', 4202, 'ow_call_approach_rally', 4202),
    -- options_vix (2026-10-13): the 30d model-free vol-index fear-gauge
    -- family — SERIES 42/43 (ovix_buy the BUY series, ovix_sell the
    -- SELL/exit series; thresholds only, no curve rows — the per-code
    -- event cells sit far under the curve emit floor), THRESHOLD codes
    -- (series + 1) * 100 + ordinal at 4300..4304/4400. Value space =
    -- corr_20d (the rolling vol-vs-spot corr): ovix_spike BUY (corr
    -- < -0.8 & 5d vol slope > +10%), ovix_spike_hivol BUY (spike &
    -- vol z252 > 1), ovix_rollover BUY (corr < -0.8 & the vol peak
    -- confirmed rolling over), ovix_fear_unwind SELL (corr crosses up
    -- through -0.5 after a deep-fear episode — the exit companion).
    -- The 2026-10-14 populate additions (temp_scripts/
    -- study_ovix_populate.py): ovix_deep_hivol BUY (corr < -0.8 &
    -- vol z252 > 0.5, no spike required — the established-fear-regime
    -- set, the study's set B softened so the thin 2023/2024 windows
    -- clear the pooled gate) and ovix_spike_wide BUY (the panic spike
    -- with the fear bar widened to corr < -0.75)
    ('signal_sub_type', 42, 'ovix_buy', 42),
    ('signal_sub_type', 43, 'ovix_sell', 43),
    ('signal_sub_type', 4300, 'ovix_spike', 4300),
    ('signal_sub_type', 4301, 'ovix_spike_hivol', 4301),
    ('signal_sub_type', 4302, 'ovix_rollover', 4302),
    ('signal_sub_type', 4303, 'ovix_deep_hivol', 4303),
    ('signal_sub_type', 4304, 'ovix_spike_wide', 4304),
    -- ovix_panic_peak BUY (2026-10-15, the vol30 value space): vol
    -- escalates >= 5% past its prior 120-session max while still
    -- spiking (slope5 > +10%) AND spot below its 5-sessions-ago close
    -- — the panic-PEAK escalation leg (a fresh episode at the crisis's
    -- new extreme, not its onset; the downside leg separates downside
    -- fear from upside euphoria)
    ('signal_sub_type', 4305, 'ovix_panic_peak', 4305),
    ('signal_sub_type', 4400, 'ovix_fear_unwind', 4400),
    -- momentum (2026-10-15): the log-return momentum family — SERIES
    -- 50..54 the five lookback axes (mom5/mom20 the FIXED 5/20d
    -- anchors, momshort/mommid/momlong the VOL-ADAPTIVE lookbacks of
    -- analysis.mov_ave_momentum), THRESHOLD codes (series + 1) * 100 +
    -- tail ordinal at 5100..5502 — the nested 1/2/3% tail carve of
    -- the axis's own trailing-10y log-return quantile. Value space =
    -- ln(1 + momentum) (log-return); bars per-code adaptive
    -- percentile quantiles (self-adaptive threshold levels)
    ('signal_sub_type', 50, 'mom5', 50),
    ('signal_sub_type', 51, 'mom20', 51),
    ('signal_sub_type', 52, 'momshort', 52),
    ('signal_sub_type', 53, 'mommid', 53),
    ('signal_sub_type', 54, 'momlong', 54),
    ('signal_sub_type', 5100, 'mom5_1pct', 5100),
    ('signal_sub_type', 5101, 'mom5_2pct', 5101),
    ('signal_sub_type', 5102, 'mom5_3pct', 5102),
    ('signal_sub_type', 5200, 'mom20_1pct', 5200),
    ('signal_sub_type', 5201, 'mom20_2pct', 5201),
    ('signal_sub_type', 5202, 'mom20_3pct', 5202),
    ('signal_sub_type', 5300, 'momshort_1pct', 5300),
    ('signal_sub_type', 5301, 'momshort_2pct', 5301),
    ('signal_sub_type', 5302, 'momshort_3pct', 5302),
    ('signal_sub_type', 5400, 'mommid_1pct', 5400),
    ('signal_sub_type', 5401, 'mommid_2pct', 5401),
    ('signal_sub_type', 5402, 'mommid_3pct', 5402),
    ('signal_sub_type', 5500, 'momlong_1pct', 5500),
    ('signal_sub_type', 5501, 'momlong_2pct', 5501),
    ('signal_sub_type', 5502, 'momlong_3pct', 5502),
    -- rf_break (2026-10-16, THRESHOLD block 5600-5699): the close
    -- beyond the PRIOR day's roof/floor level (the turtle breakout on
    -- the roof_floor_estimates store) per window x method. Roof
    -- = BUY (the significant-high continuation), floor = SELL (the
    -- breakdown exit); each day claims at its DEEPEST breached window
    -- (widest-first — donchian windows nest: the 500d max high
    -- contains the 255d's). Plain labels = the donchian method (the
    -- window's max intraday high / min low, method 1); _med_ = the
    -- median consensus of the five estimate methods per (date,
    -- period) (method 5)
    ('signal_sub_type', 5600, 'rf60_roof', 5600),
    ('signal_sub_type', 5601, 'rf120_roof', 5601),
    ('signal_sub_type', 5602, 'rf255_roof', 5602),
    ('signal_sub_type', 5603, 'rf500_roof', 5603),
    ('signal_sub_type', 5610, 'rf60_floor', 5610),
    ('signal_sub_type', 5611, 'rf120_floor', 5611),
    ('signal_sub_type', 5612, 'rf255_floor', 5612),
    ('signal_sub_type', 5613, 'rf500_floor', 5613),
    ('signal_sub_type', 5620, 'rf60_med_roof', 5620),
    ('signal_sub_type', 5621, 'rf120_med_roof', 5621),
    ('signal_sub_type', 5622, 'rf255_med_roof', 5622),
    ('signal_sub_type', 5623, 'rf500_med_roof', 5623),
    ('signal_sub_type', 5630, 'rf60_med_floor', 5630),
    ('signal_sub_type', 5631, 'rf120_med_floor', 5631),
    ('signal_sub_type', 5632, 'rf255_med_floor', 5632),
    ('signal_sub_type', 5633, 'rf500_med_floor', 5633),
    -- options_vix addition (2026-10-16): ovix_atr_break BUY — the
    -- volatility-EXPANSION breakout: the spot day-return in close-to-
    -- close ATR units beyond the k bar while the vol index slope5
    -- fires (the 2026-10-15 grind-down class: fast vol repricing from
    -- a low base where the corr (-0.80) and vol-ATH (+5%) legs both
    -- stay unmet; k self-adaptive per cell from {2, 2.5, 3})
    ('signal_sub_type', 4306, 'ovix_atr_break', 4306),
    -- ma_cross breadth additions (2026-10-16, block 5700+): the
    -- market-level breadth rules over the sec_type's own universe —
    -- the Zweig breadth thrust (the 10d EMA of adv/(adv+dec) from
    -- below 0.40 to above 0.615 within 10 sessions, BUY) and the 9:1
    -- up/down days (adv >= 9x dec BUY / dec >= 9x adv SELL, exit
    -- timing); stamped per code (each code's cell measures its own
    -- forward blend of the same market day)
    ('signal_sub_type', 5700, 'ma_breadth_thrust', 5700),
    ('signal_sub_type', 5701, 'ma_9to1_up', 5701),
    ('signal_sub_type', 5702, 'ma_9to1_dn', 5702),
    -- analysis_cache_kind: analysis_signals.analysis_cache.kind — the
    -- generic intermediate-result cache's tenant (which analysis owns
    -- the row's cache jsonb)
    ('analysis_cache_kind', 0, 'boost_derivation', 0);
