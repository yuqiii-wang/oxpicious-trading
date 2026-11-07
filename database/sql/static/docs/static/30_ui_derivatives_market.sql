-- ============================================================================
--  static.docs seed — derivatives & market pages: futures, options
--  panels, ETF margin, margin trends, debt.
-- ============================================================================

DELETE FROM static.docs WHERE doc_id LIKE 'ui.futures.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.options.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.etf-margin.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.margin.%';
DELETE FROM static.docs WHERE doc_id LIKE 'ui.debt.%';

INSERT INTO static.docs (doc_id, title, intro, series, notes, ordinal) VALUES
(
  'ui.futures.price-curves',
  NULL,
  'Futures price curves for {{product}} ({{name}}): one settlement-price line per contract plus the underlying spot line for index futures. The contract fan at any date is the TERM STRUCTURE — ascending with maturity = contango (the carry cost of longs), inverted = backwardation (near tension, hedging pressure); the fan compressing into the spot line is convergence at work.',
  '[
    {"key": "spot", "name": "{{name}}", "unit": "index pts", "description": "underlying (spot) daily close — the convergence anchor every contract ages toward"},
    {"key": "expiryDots", "name": "__expiry_dots__", "description": "each live contract''s expiry date — the convergence endpoint"}
  ]'::jsonb,
  '[
    {"key": "contracts", "text": "Contracts are distinguished by maturity; matured contracts stop at their expiry."}
  ]'::jsonb,
  0
),
(
  'ui.futures.corr',
  NULL,
  '20-day rolling correlation vs the underlying (corr_price_vs_underlying), one line per contract — {{nActive}} active · {{nMatured}} matured, y ∈ [−1, 1]. ρ pinned near 1 = the contract is pure beta on the underlying (basis/hedge trades viable); ρ ebbing toward 0 = decoupling from spot — rare, and it voids basis-convergence premises while it lasts.',
  NULL,
  '[
    {"key": "lockstep", "text": "+1 = the contract moves in lockstep with the underlying; 0 = no linear relation."}
  ]'::jsonb,
  0
),
(
  'ui.futures.basis',
  NULL,
  'Basis convergence (contrarian): per-contract signed gap (futures − spot) in bps, collapsing to 0 as each contract ages toward expiry. A fat positive basis = carry-rich longs (leveraged demand); a negative basis = hedging pressure / pessimism. The decay into expiry is the tradable convergence — a rapid premium decay before expiry signals leveraged-long liquidation.',
  '[
    {"key": "zeroLine", "name": "__zero_line__", "unit": "bps", "description": "the convergence target — futures meets spot (gap = 0)"}
  ]'::jsonb,
  NULL, 0
),
(
  'ui.options.greeks',
  NULL,
  'Per-expiry Greek profile vs moneyness m = K/S (calls solid, puts dashed, one group per expiry month). Units: {{units}}.{{#positioning}} The vertical line is the OI-weighted mean moneyness of the combined CALL+PUT book, labeled with the chain positioning metric {{tag}} ({{formula}}). The tilt flags the crowded side of the chain — the side dealer hedging amplifies and that unwinds fastest.{{/positioning}}{{^positioning}} Theta/rho have no positioning metric — no metric line.{{/positioning}}',
  NULL, NULL, 0
),
(
  'ui.options.options-trend',
  NULL,
  'Three synchronized option-flow charts for {{underlying}}: (1) Expiry OI Bands — per-expiry OI by strike vs the spot line (where exposure concentrates: the band walls are the strike zones spot grinds against — dealer hedging makes heavy OI magnet-like); (2) P/C OI ratio = put OI / call OI of the selected cohorts with MA5/MA20 — ratio >1 = hedging tilt (defensive lean), <1 = upside chase, 1 = neutral; (3) call vs put total OI (mil contracts) — the market''s engaged derivatives exposure.',
  '[
    {"key": "spot", "name": "Spot", "unit": "元", "description": "underlying daily closing price"},
    {"key": "oiBands", "name": "OI Bands", "description": "per-expiry open-interest bands by strike over time — the OI walls spot trades against"},
    {"key": "pcRatio", "name": "P/C OI Ratio", "unit": "ratio", "description": "put OI / call OI of the selected cohorts; >1 defensive, <1 chasing"},
    {"key": "pcMa5", "name": "MA5", "unit": "ratio", "description": "5-day mean of the P/C ratio"},
    {"key": "pcMa20", "name": "MA20", "unit": "ratio", "description": "20-day mean of the P/C ratio — the positioning trend"},
    {"key": "callOi", "name": "Call OI", "unit": "mil contracts", "description": "daily total call open interest"},
    {"key": "putOi", "name": "Put OI", "unit": "mil contracts", "description": "daily total put open interest"}
  ]'::jsonb,
  '[
    {"key": "cohortAgg", "text": "Charts 2 and 3 aggregate ONLY the selected expiry cohorts; lines break on dates where the selection has no data."},
    {"key": "latestDate", "text": "Latest data date: {{lastDate}}."}
  ]'::jsonb,
  0
),
(
  'ui.options.vol-index',
  NULL,
  'CBOE-VIX analog for the selected underlying: 100·√(30d constant-maturity model-free variance) from the OTM-strip replication on settlement prices (CBOE method, r = 2%) — the smile LEVEL. A swell = fear bid (hedges chased, they are rich here); a trough = complacency (hedges cheap — the VRP window opens); both extremes revert. The chart below correlates it with spot: rolling Pearson ρ of the daily spot log-return vs the daily vol-index log-change over 20/60/255 sessions (the CBOE VIX-vs-spot ''fear gauge'' convention — returns vs changes, never the trending levels; the 5-session stride samples are linearly interpolated to daily for display and signal reads). ρ toward -1 = spot down days bid vol (classic leverage effect); the deep-fear bar is -0.75 on the 20-session window — crisis-tail rare, high attention; ρ rising toward 0 = vol decoupling from direction.{{#inSignal}} IN-SIGNAL mode: the options_vix family''s buy/sell events mark the vol curve; the corr chart carries the -0.75 deep-fear and -0.5 unwind reference bars.{{/inSignal}}',
  '[
    {"key": "vix", "name": "{{label}} VIX", "unit": "vol pts", "description": "daily 30-day model-free implied-volatility index (the smile LEVEL)"},
    {"key": "spot", "name": "{{spotName}}", "unit": "price", "description": "underlying close on the right-hand axis (context overlay, v_etf_margin / v_index_daily)"},
    {"key": "corr", "name": "Corr 20d/60d/255d", "unit": "ρ", "description": "rolling Pearson corr of daily spot log-return vs vol-index log-change (options_vol_index_vs_spot_price_corr)"}
  ]'::jsonb,
  '[
    {"key": "settle", "text": "SZSE ETF legs inherit the known ETF settle-data condition; CFFEX index legs carry the deep history."}
  ]'::jsonb,
  0
),
(
  'ui.options.vol-smile',
  NULL,
  'IV (%) vs moneyness m = K/S per expiry month — the smile snapshot. Per-expiry OI-weighted smile skewness γ₁ = μ₃/σ³ (IV weighted by raw OI count floored at 1 — premium never weights). A fat negative γ₁ = the put wing is bid (crash-hedge demand — fear priced in the wings); a stretched positive = call chase (upside speculation); crowded wings on either side snap back as the hedging flow mean-reverts.',
  NULL,
  '[
    {"key": "skewReadout", "text": "Per-expiry OI-wtd skewness (3rd moment): {{skewTags}}"}
  ]'::jsonb,
  0
),
(
  'ui.options.skew',
  NULL,
  'Spot vs Skew Price = S × (1 + (metric − neutral) × 10%). Metric: {{metric}}. Contracts vote with raw OI (zero OI = no vote); greeks from Black-76 IV off settlements — premium is the value, never the vote (skew_type={{mode}}, neutral on the spot curve). Skew price riding above spot = positioning tailwind; the bottom panel''s per-expiry convergence (skew − spot collapsing to 0 at expiry) is the contrarian companion — converging gaps into expiry mean the positioning is spent.',
  '[
    {"key": "spot", "name": "Underlying Spot", "unit": "{{unit}}", "description": "underlying daily closing price (the neutral anchor)"},
    {"key": "meanSkew", "name": "{{meanSeriesName}}", "unit": "{{unit}}", "description": "mean skew-adjusted price — spot rebased by the positioning metric"},
    {"key": "gap", "name": "Skew price vs spot gap (%)", "unit": "%", "description": "per-expiry convergence chart: skew price minus spot, collapsing to 0 at expiry"}
  ]'::jsonb,
  '[
    {"key": "shadeBands", "text": "Shade bands: the spot↔skew gap per active expiry set."},
    {"key": "bandsLayer", "text": "Bands layer (analysis.options_bands v2 — OI-mass corridor): the gray shade = strikes enclosing the central 80% of pooled chain OI (P10..P90) — the options-bounded region; markers = fresh corridor crosses vs the prior session (EXIT_LOW below the line — validated snap-back-up: close <= P10 = 90% up over the next 5 sessions, mean +4.5%; EXIT_HIGH above — 89% down; REENTER = a fresh re-cross back inside). AMPLIFIED exits (edge_sig) fire when the exchange had just extended the strike grid the same way (>= 2 new strikes listed in the trailing 40 sessions; e.g. 000300 2025-08-28 EXIT_HIGH with 6 fresh higher strikes) — the breach is into freshly-listed price territory, not routine corridor noise. The WALL SHADES mark each side''s chain-dominant OI wall zone (analysis.options_walls, wall_type=''zone''): band = the zone''s strike range, center line = its OI-weighted strike; CALL walls act as resistance/cap, PUT walls as support/floor; lifecycle (ACTIVE > ERODED > BREACHED). Spot''s OI-mass position (<=25% of OI below spot) is the UP lean zone, strongest when every expiry''s moneyness skew sits above spot (mean 10-session +1.57%)."},
    {"key": "lineWidth", "text": "Per-expiry line WIDTH encodes the expiry''s absolute open interest (peak daily calls+puts contracts, sqrt-scaled); the plotted values remain ratio-based. The pipeline OI stats per group (analysis.options_oi_stats): OI change vs 5/20 sessions (Δ5d/Δ20d, + = positions added / − = closed or rolled) and the trailing-20-session max (current OI at that max = a 20-session OI high)."}
  ]'::jsonb,
  0
),
(
  'ui.options.skew.intro.oi-moneyness',
  NULL,
  'Spot vs Skew-Adjusted Price = S × E[M], E[M] = Σ(OI·K/S) / Σ(OI) per expiry — where open interest sits vs spot. Contracts vote with raw OI lots (floored at 1) — never premium/notional, no IV: a 500-lot deep-OTM strike outvotes a 100-lot ATM one (skew_type=oi_moneyness). E[M] > 1 = OI parked overhead (the supply zone spot must chew through), < 1 = support beneath; the bottom panel''s OI convergence (skew price − spot, collapsing to 0 at expiry) is the contrarian companion — gaps converging into expiry mean the positioning is spent.',
  NULL, NULL, 0
),
(
  'ui.options.skew.metric.greek-delta',
  NULL,
  'Delta-wtd Put/Call Ratio dpcr = Σ OI·|Δ| (puts) / Σ OI·|Δ| (all) per expiry — where DIRECTIONAL exposure sits (delta-weighted refinement of the put/call ratio; 0.5 = balanced book, >0.5 put-tilted/bearish, <0.5 call-tilted/bullish; OI is two-sided — exposure concentration, not signed bets)',
  NULL, NULL, 0
),
(
  'ui.options.skew.metric.greek-gamma',
  NULL,
  'Gamma Balance = (Σ OI·Γ calls − Σ OI·Γ puts)/(Σ OI·Γ all) per expiry — GEX-style dealer-sign convention: >0 call OI dominates where gamma lives (long-gamma regime, vol suppression/pin), <0 put OI dominates (short-gamma regime, moves amplify)',
  NULL, NULL, 0
),
(
  'ui.options.skew.metric.greek-vega',
  NULL,
  'Vega Wing Balance = (Σ OI·ν calls − Σ OI·ν puts)/(Σ OI·ν wings) on the 0<|Δ|<0.5 OTM wings per expiry — the open-interest mirror of the 25Δ risk reversal: >0 upside vol demand, <0 downside (crash-hedge) vol demand',
  NULL, NULL, 0
),
(
  'ui.options.spot-skew',
  NULL,
  'Smile skew vs time in delta space — strikes drift with spot, so full smiles overplot; the tracked scalar is spot-centered. Today: RR{{delta}} = IV({{delta}}Δ call) − IV({{delta}}Δ put) per real expiry, vol pts — positive = call wing rich, negative = put wing rich; the line is ATM IV. Nearest-|δ| contract per expiry, no OI weighting; IVs Black-76 off settlements (OI-weighted positioning skews live in the shared skew panel). History: three grids on a shared time axis — spot / ATM IV (the LEVEL, VIX analog) / RR (the TILT, SKEW analog), with the ±2σ(20d) extreme band. The trade read: fade ±2σ skew extremes — they historically mean-revert; RR rising on a falling level = fear premium, quick to bleed.',
  '[
    {"key": "rrToday", "name": "RR{{delta}} (C−P)", "unit": "vol pts", "description": "risk reversal per real expiry: IV({{delta}}Δ OTM call) − IV({{delta}}Δ OTM put)"},
    {"key": "atmToday", "name": "ATM IV", "unit": "%", "description": "at-the-money IV per expiry (line)"},
    {"key": "spot", "name": "Spot", "description": "underlying price (top grid)"},
    {"key": "atmHist", "name": "ATM IV", "unit": "%", "description": "smile LEVEL per date (middle grid — the VIX analog)"},
    {"key": "rrFront", "name": "RR{{delta}} front", "unit": "vol pts", "description": "front-expiry risk reversal — the smile TILT, SKEW analog (solid)"},
    {"key": "rrMean", "name": "RR{{delta}} mean", "unit": "vol pts", "description": "all-expiry mean risk reversal (dashed)"},
    {"key": "mfVol", "name": "MF Vol 30d", "unit": "vol pts", "description": "30-day model-free VIX-style index on the LEVEL grid (analysis.options_vol_index)"}
  ]'::jsonb,
  '[
    {"key": "gaps", "text": "Gaps in the RR series = no contract with |δ| near {{deltaRatio}} that day{{#deepWing}} (the deep wing is sparse){{/deepWing}}."}
  ]'::jsonb,
  0
),
(
  'ui.options.interest-wall',
  NULL,
  'Per-strike OI stacked by expiry month — calls right (+), puts left (−). Bar height = raw OI contracts per strike (not premium- or notional-weighted): walls locate where contracts sit, not dollar exposure. Reference marks: spot, the call/put wall zones, MaxPain and the OI-weighted strike. The read: spot gravitates toward MaxPain into expiry (dealer-hedging pin); a wall''s surrender (a close meaningfully past the zone — breaches hold on only ~1% of days, rare and high-attention) fuels the move beyond it, while walls repel approach.',
  NULL,
  '[
    {"key": "dominance", "text": "Wall dominance: only the dominant wall is drawn when one side''s wall OI is >33% larger; comparable walls (within 33%) draw both — a balanced market."}
  ]'::jsonb,
  0
),
(
  'ui.etf-margin.panel',
  NULL,
  'Single-ETF daily chart of {{code}} {{name}} ({{#isBond}}bond ETF — rebased close line{{/isBond}}{{^isBond}}equity ETF — adjusted OHLC, so dividends/splits draw no fake gaps{{/isBond}}): MA20/60/120 anchors (the monthly→half-year MA ladder){{#divEst}}; the Div Est line is the cum-dividend fair-value curve anchored on each ex-date''s post-drop close, regrowing exponentially to the next cum level by the next ex-date{{/divEst}}; RZ (融资) margin balance = the stock of leveraged longs and RQ (融券) = short interest, both as scaled scores; turnover bars (亿元). RZ swelling into rising price = leveraged longs crowding (the fuel that unwinds hardest); RQ swelling = shorts building (squeeze risk the other way) — the balance inflections against price are the read. Div Est hugging the candles = dividend-accrual-like drift; a widening gap = pure market move.',
  '[
    {"key": "bondClose", "name": "{{priceName}}", "unit": "{{unit}}", "description": "adjusted close rebased to % change from the first valid close"},
    {"key": "ohlc", "name": "{{priceName}}", "unit": "{{unit}}", "description": "adjusted OHLC candles, uniformly rebased in percentage mode"},
    {"key": "ma20", "name": "MA20", "unit": "{{unit}}", "description": "20-day mean of the rebased close — the monthly anchor"},
    {"key": "ma60", "name": "MA60", "unit": "{{unit}}", "description": "60-day mean of the rebased close — the quarterly swing anchor"},
    {"key": "ma120", "name": "MA120", "unit": "{{unit}}", "description": "120-day mean of the rebased close — the half-year regime line"},
    {"key": "divEst", "name": "Div Est", "unit": "{{unit}}", "description": "dividend price estimation curve (analysis.dividend_est): cum-dividend fair-value line anchored on each ex-date''s post-drop close, growing exponentially to the next cum-dividend level (close(next ex) + next D) on the record date; open tail accrues at the last dividend yield / frequency"},
    {"key": "rz", "name": "cash borrow balance", "description": "RZ 融资 balance — the stock of leveraged longs (the plotted value is a scaled score; quote balances in raw yuan)"},
    {"key": "rq", "name": "sec borrow balance", "description": "RQ 融券 balance — short interest (the plotted value is a scaled score; quote balances in raw yuan)"},
    {"key": "amount", "name": "Amount", "unit": "亿元", "description": "daily trading turnover (成交金额) — the participation behind the move"}
  ]'::jsonb,
  '[
    {"key": "rebase", "text": "Percentage mode rebases price series to % change from the first valid close; Amount and margin scores are not rebased."},
    {"key": "scores", "text": "Margin series are plotted as shifted/clipped scores, not raw yuan — quote balances from their raw values."},
    {"key": "divEstAdjusted", "text": "Div Est is drawn in adjusted price space (raw curve × adj factor) so it overlays the adjusted candles; its RAW CNY value and the segment''s next ex-date / next dividend ride alongside."}
  ]'::jsonb,
  0
),
(
  'ui.margin.trends',
  NULL,
  'Margin trend composite: the industry''s aggregate RONGZI margin line (RZ balance outstanding = the stock of leveraged longs, or RZ daily buy = the flow of fresh leveraged demand), over daily return bars intensity-coded by margin share (RZ-buy / turnover tercile) with a trailing-20d return-vol line. Leverage chasing into rising returns = a crowded trend (fragile under forced unwinds); balance falling while returns rise = a deleveraging rally (weaker foundation); balance rising into flat/falling prices = pent-up leverage.',
  NULL,
  '[
    {"key": "attribution", "text": "Industry attribution aggregates member stock+ETF margin; exchange attribution is the official market-wide total."}
  ]'::jsonb,
  0
),
(
  'ui.margin.hyped-drained',
  NULL,
  'Hyped vs Drained by market regime: mean |daily return| % on Drained (bottom RZ-buy-share-of-turnover tercile — no margin chase) vs Hyped (top tercile — margin chase) days within each market regime (calm ~71% of days / hot / panic / quiet from the code''s own EWMA vol & amount z-scores), each labeled with its amplification ×ratio. Hyped > Drained means leveraged money amplifies moves in that regime — the crowd that unwinds into the next swing.',
  NULL,
  '[
    {"key": "regimeRows", "text": "Regime split needs stats.market_regimes rows for the code; codes without them degrade to the empty state."}
  ]'::jsonb,
  0
),
(
  'ui.debt.oml',
  NULL,
  'PBoC medium-term liquidity ops: the cumulative net balance (outright repo + MLF) against daily injections/withdrawals. A steepening balance = a liquidity tailwind for risk assets (the funding tide rising); an ebbing one = the tide receding; injection bursts clustered around quarter ends/holidays are seasonal smoothing, not a stance change.',
  '[
    {"key": "cum", "name": "Cumulative balance", "unit": "亿元", "description": "cumulative net outright repo + MLF balance — the medium-term liquidity tide"},
    {"key": "outrightIn", "name": "Outright injection", "unit": "亿元", "description": "daily outright repo auction buys (start quantity)"},
    {"key": "mlfIn", "name": "MLF injection", "unit": "亿元", "description": "daily MLF lending (start quantity) — the medium-term policy facility"},
    {"key": "outrightOut", "name": "Outright withdrawal", "unit": "亿元", "description": "daily outright repo maturities/settlements (end quantity)"},
    {"key": "mlfOut", "name": "MLF withdrawal", "unit": "亿元", "description": "daily MLF maturities (end quantity)"}
  ]'::jsonb,
  NULL, 0
),
(
  'ui.debt.omo-7d',
  NULL,
  'PBoC daily OMO: the 7-day reverse-repo policy rate against the repo lifecycle (injections, withdrawals, cumulative net balance). The 7d rate is THE policy anchor — a cut lowers the whole funding curve and ordinarily cheers risk assets; net injection streaks show the PBoC smoothing intraweek liquidity, and a balance ebbing while the rate holds = neutral-to-draining.',
  '[
    {"key": "rate", "name": "OMO 7D rev-repo rate (%)", "unit": "%", "description": "the 7-day reverse-repo auction rate — the policy rate anchor"},
    {"key": "repoIn", "name": "Repo start (injection)", "unit": "亿元", "description": "daily 7-day reverse-repo lending volume"},
    {"key": "repoOut", "name": "Repo end (withdrawal)", "unit": "亿元", "description": "daily reverse-repo maturities (absolute value)"},
    {"key": "cum", "name": "Cumulative balance", "unit": "亿元", "description": "cumulative net reverse-repo balance — the short-term liquidity tide"}
  ]'::jsonb,
  NULL, 0
),
(
  'ui.debt.omo-1d',
  NULL,
  'PBoC debt panel in OVERNIGHT mode: the overnight reverse-repo policy rate (隔夜逆回购) against the 7-day reverse-repo lifecycle — injections, withdrawals, cumulative net balance. The overnight variety (tool since 2026-06-29) is a fixed-rate, quantity-tender operation priced 25bp above the 7-day OMO rate — a ceiling-style corridor tool deployed around funding pinch points (quarter ends, holidays); its use alone signals the funding floor is being defended.',
  '[
    {"key": "rate", "name": "Overnight OMO rev-repo rate (%)", "unit": "%", "description": "overnight reverse-repo fixed rate = 7d OMO rate + 25bp (holds flat between operations)"},
    {"key": "repoIn", "name": "Repo start (injection)", "unit": "亿元", "description": "daily 7-day reverse-repo lending volume"},
    {"key": "repoOut", "name": "Repo end (withdrawal)", "unit": "亿元", "description": "daily reverse-repo maturities (absolute value)"},
    {"key": "cum", "name": "Cumulative balance", "unit": "亿元", "description": "cumulative net reverse-repo balance — the short-term liquidity tide"}
  ]'::jsonb,
  '[
    {"key": "publishSince", "text": "Overnight ops publish since 2026-06-29 — earlier dates are blank."}
  ]'::jsonb,
  0
),
(
  'ui.debt.shibor',
  NULL,
  'SHIBOR (Shanghai Interbank Offered Rate) fixings across tenors: overnight, 1-week, 1-month, 3-month, 6-month and 1-year. Short tenors (O/N, 1W) spike when interbank funding tightens; the 3M–1Y curve reflects the market''s expected path of policy rates. Compare with the OMO panel — persistent O/N spikes above the OMO rate signal liquidity stress.',
  '[
    {"key": "on", "name": "O/N", "unit": "%", "description": "overnight SHIBOR fixing"},
    {"key": "w1", "name": "1W", "unit": "%", "description": "1-week SHIBOR fixing"},
    {"key": "m1", "name": "1M", "unit": "%", "description": "1-month SHIBOR fixing"},
    {"key": "m3", "name": "3M", "unit": "%", "description": "3-month SHIBOR fixing"},
    {"key": "m6", "name": "6M", "unit": "%", "description": "6-month SHIBOR fixing"},
    {"key": "y1", "name": "1Y", "unit": "%", "description": "1-year SHIBOR fixing"}
  ]'::jsonb,
  NULL, 0
),
(
  'ui.debt.cgb',
  NULL,
  'China Treasury (ChinaBond) government bond yields for the 1Y, 5Y, 10Y and 30Y tenors. The 1Y end follows funding/policy expectations (moves with OMO/SHIBOR); the 10Y is the benchmark long rate priced off growth and inflation expectations; 30Y duration sentiment. A flattening 1Y→10Y spread anticipates easing; steepening anticipates tightening or reflation.',
  '[
    {"key": "y1", "name": "1Y", "unit": "%", "description": "1-year CGB yield"},
    {"key": "y5", "name": "5Y", "unit": "%", "description": "5-year CGB yield"},
    {"key": "y10", "name": "10Y", "unit": "%", "description": "10-year CGB yield (the benchmark long rate)"},
    {"key": "y30", "name": "30Y", "unit": "%", "description": "30-year CGB yield (duration sentiment)"}
  ]'::jsonb,
  NULL, 0
),
(
  'ui.debt.lpr',
  NULL,
  'PBoC Loan Prime Rate (LPR) — the monthly lending benchmark announced on the 20th of each month (next business day if a holiday), flat between announcements. The 1Y LPR anchors corporate short-term lending costs; the 5Y+ LPR anchors mortgages — its cuts are the property-sector lever. An LPR cut with the OMO rate unchanged = targeted easing to credit, not the funding curve.',
  '[
    {"key": "y1", "name": "1Y LPR", "unit": "%", "description": "1-year LPR — the corporate short-term lending benchmark (monthly step)"},
    {"key": "y5", "name": "5Y+ LPR", "unit": "%", "description": "5-year-plus LPR — the mortgage benchmark"}
  ]'::jsonb,
  '[
    {"key": "step", "text": "Step lines: the rate holds flat between monthly announcements."}
  ]'::jsonb,
  0
);
