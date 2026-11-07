-- ============================================================================
--  AI prompts seed — skills / biz_guide, GENERAL: the cross-cutting
--  business guides (a synthesis approach, the volume vocabulary) that
--  every run may lean on. One of the static/ai_prompts/ seed files —
--  DDL + the shared contract: 06_ai_prompts.sql.
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, ordinal) VALUES
    ('cross-item-synthesis',
     'biz_guide',
     1,
     'Cross-item synthesis',
     1,
     'cross-item-synthesis: cross-reference findings across the '
     'selected items — call out agreements, conflicts and the '
     'combined market picture in the closing section.',
     'Cross-reference findings across the selected items and close with the combined market picture, not per-item summaries.',
     'Close with the combined picture, not per-item summaries only.',
     4),
    ('volume-price-guide',
     'biz_guide',
     1,
     'Volume & participation guide',
     1,
     'volume-price-guide: volume means 成交额 trading_amount (yuan) '
     'unless the ask names 交易量 trading_shares. The participation '
     'gauge is the trading-amount MARKET SHARE vs its own MA — never '
     'an absolute yuan threshold. House vocabulary, each carrying its '
     'trade meaning: 放量 = share-gap above its own MA (interest '
     'surging — the move has followers), 缩量 = below (willingness '
     'shrinking), 量价背离 = a price move the participation does not '
     'confirm (credibility discounted — fade-prone), 放量下跌 = '
     'capitulation (sell pressure releasing — washout), 缩量下跌 = a '
     'no-panic grind (阴跌 risk — no washout yet), 缩量弱反弹 = a '
     'fade-prone rebound (no fuel), 放量确认 / 放量收复 MA20 = the '
     'required confirmation for trend-repair calls, 天量 / 地量 = '
     'the ~10y percentile tails (climax / exhaustion of interest). '
     'Judge every volume claim against the security''s OWN MA or '
     'percentile, never a fixed level.',
     'The house 放量/缩量 volume vocabulary and the participation gauge (trading-amount market share vs its own MA).',
     'The cross-cutting volume vocabulary every OHLCV study needs.',
     14)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
