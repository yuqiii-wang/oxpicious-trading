-- ============================================================================
--  AI prompts seed — skills / biz_guide, the leverage & DERIVATIVES
--  lenses: margin-trends (borrowed money in the CASH market — a
--  leverage-flow read, not a derivatives one), futures-basis and
--  options (both derivatives positioning priced off the index).
--  The futures basis is NOT a margin read: the basis prices the
--  derivatives crowd''s posture, margin financing counts cash-market
--  borrowing — related by leverage framing only. One of the
--  static/ai_prompts/ seed files — DDL + the shared contract:
--  06_ai_prompts.sql.
--
--  Every scope-bound guide carries its bound_labels (the GIN-indexed
--  force-load keyword array matched against the run's question) and
--  its optional_labels (the GIN-indexed English search vocabulary;
--  contract: 06_ai_prompts.sql).
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it).
-- ============================================================================

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, bound_labels, optional_labels, ordinal) VALUES
    ('margin-trends-guide',
     'biz_guide',
     1,
     'Margin trends guide',
     1,
     'margin-trends-guide: the margin balance is the market''s '
     'LEVERAGED risk appetite — borrowed money marks conviction, and '
     'it must be repaid, so it amplifies both directions. This is a '
     'CASH-market read (margin financing/short-selling balances), '
     'distinct from the futures-basis and options lenses, which price '
     'derivatives positioning. Report the '
     'balance LEVEL with its change and rate of change: margin '
     'expansion into a rally confirms the conviction behind it; '
     'contraction into a decline warns the leveraged bid is '
     'capitulating and leaving; a rally WITHOUT margin expansion is '
     'spot-driven and thinner. A margin claim without the exchange '
     'split misses WHERE the money sits; the industry participation '
     'shows which themes the leverage is chasing.',
     'How to read margin financing — balance changes, index/industry participation and the exchange split.',
     'The margin lens (derivatives/margin-trends).',
     ARRAY['融资', '两融', '杠杆'],
     ARRAY['margin financing', 'margin balance', 'leverage flow', 'exchange split'],
     21),
    ('futures-basis-guide',
     'biz_guide',
     1,
     'Futures basis guide',
     1,
     'futures-basis-guide: the spot-futures basis is the derivatives '
     'crowd''s posture priced into the futures: a deep-DISCOUNT basis '
     'in a decline = fear pricing (hedgers paying up to stay short), '
     'a PREMIUM into a rally = crowded leveraged longs (a fragile '
     'bid). This is a DERIVATIVES-positioning read, not a margin '
     'read — margin financing counts cash-market borrowing, the '
     'basis prices the futures market''s own posture. Quote the '
     'basis with its own-history quintile rank — never an absolute '
     'level; mind contract rolls when comparing levels across time.',
     'How to read the futures basis — the spot-futures gap, its quintile rank and the term-structure posture.',
     'The futures lens (derivatives/futures).',
     ARRAY['期货', '基差', '贴水'],
     ARRAY['futures basis', 'premium discount', 'term structure', 'contract roll'],
     22),
    ('options-guide',
     'biz_guide',
     1,
     'Options analysis guide',
     1,
     'options-guide: the options lens reads POSITIONING and priced '
     'fear, not price. The dominant OI walls mark where the hedging '
     'crowd is committed: the OI mass sits ~3% from spot, the zone '
     'EDGE ~12% — a close meaningfully past the edge is the '
     'reversion event (the wall repels), while a raw wall breach '
     'otherwise ACCELERATES (dealer hedging amplifies the move). '
     'The VRP (implied vs realized vol) is the price of insurance — '
     'the gate that decides whether put-wall breaches trade at all. '
     'The corr20 × vol-spike interaction (vol spiking while its spot '
     'correlation is deeply negative) is the panic signal — spike '
     'alone or corr alone is baseline; the interaction is the '
     'signal. Read the IV skew as which tail the crowd is insuring.',
     'How to read the options lens — OI walls, the IV/vol index, skew and the VRP posture.',
     'The options lens (derivatives + signals options pages).',
     ARRAY['期权', '波动率', '偏度'],
     ARRAY['options', 'open interest walls', 'implied volatility', 'vol risk premium', 'iv skew'],
     23)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        bound_labels = EXCLUDED.bound_labels,
        optional_labels = EXCLUDED.optional_labels,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
