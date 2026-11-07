-- ============================================================================
--  AI prompts seed — prompt_type 2 plan: the generation-plan prompts
--  (steps the agent lays out and follows before writing). Two sub
--  types (the picker nests by prompt_sub_type):
--    • goal — the STUDY-SCOPE rows (study-codes / study-codes-related
--      / study-market-codes / study-market-whole): mutually exclusive
--      scope directives, exactly one governing how wide a run studies
--      (the UI picks them as one radio choice; none picked = the
--      runner's default_scope_plan_key by the run's scope —
--      study-codes when codes are named, study-market-codes on an All
--      Market run).
--    • orchestration — the plan-SHAPE rows (naive-verify-plan,
--      general-plan), multi-pick checkboxes.
--  One of the
--  static/ai_prompts/ seed files — DDL + the shared contract:
--  06_ai_prompts.sql.
--
--  Idempotent — re-running refreshes the seeded texts in place. The
--  version column stays at its seeded 1 and is deliberately NOT in the
--  ON CONFLICT SET: a re-run never resets a manually bumped version
--  (the UI edit path bumps it). The scoped DELETE below migrates the
--  pre-split '' sub_type rows once (the PK is
--  (prompt_key, prompt_sub_type), so the INSERT alone would orphan
--  them); on every later re-run it matches nothing.
-- ============================================================================

DELETE FROM llm.ai_prompts
WHERE prompt_type = 2
  AND prompt_sub_type = ''
  AND prompt_key IN ('naive-verify-plan', 'general-plan', 'study-codes',
                     'study-codes-related', 'study-market-codes',
                     'study-market-whole');

INSERT INTO llm.ai_prompts (prompt_key, prompt_sub_type, prompt_type, title, version, instruction, preview, note, ordinal) VALUES
    ('naive-verify-plan',
     'orchestration',
     2,
     'Naive verify plan',
     1,
     'naive-verify-plan: before writing, lay out a naive plan of checks '
     'and follow it step by step — (1) split the selected items into '
     'their research claims (the narrative readings each section will '
     'make) and their technical stats (indicator levels, recent changes, '
     'percentile rarities); (2) for every research claim, re-run the '
     'supporting aggregate query and confirm the cited number; (3) for '
     'every technical stat, confirm the reading against its own trailing '
     'history before using it; (4) state each step''s pass/fail in the '
     'working notes, drop or re-ground any claim that fails its check, '
     'and only then write the final document.',
     'Lay out naive verification steps and re-check every research claim and technical stat against the data before writing.',
     'Plan-then-verify: naive steps that re-check research claims and technical stats before writing.',
     10),
    ('general-plan',
     'orchestration',
     2,
     'General plan — thin skeleton, sub-plan sections',
     1,
     $txt$general-plan: the main plan stays THIN AND GENERAL - it is an entry skeleton, not the study itself. Lay out exactly: (1) ONE scope-entry step that anchors the subject (identify it, its latest trading date, and the boundaries of what the run must cover); (2) one delegated SUB-PLAN section fork per coherent group of analysis items - each fork goal opens with 'First plan your own sub-steps: call update_plan with add_nodes under your fork, then work and tick them yourself as you go', then names the section's STUDY PROMPTS by their exact prompt_key values from the prompt catalog (a named key auto-loads its full instruction in that sub-plan's context) and lists the section's item keys; (3) at most ONE unmarked inline section-weighing synthesis step when the sub-plan sections' findings must be weighed together before the run hands off to its result stage. Do NOT propose review-wave steps (return_estimate / risk_analysis / optimistic_review / pessimistic_review) or a run-level final answer here - the run's separate RESULT graph runs the four independent reviews and the final synthesis over whatever this plan's steps find. Do NOT enumerate per-item steps, per-metric checks, or data operations in the main plan - the granular business planning happens inside each sub-plan node, under its own smaller plan of smaller goals: every node serves ONE goal (main-path steps the grander per-aspect goals of the run, each sub-plan node a smaller goal cut from its section's goal), and a node ticked completed marks that goal finished - size every goal to what one node's work can settle.$txt$,
     'Keep the main plan thin: scope entry + delegated sub-plan sections that name their study prompts + final synthesis — the granular planning happens inside each sub-plan node.',
    'The thin general plan: an entry skeleton whose sub-plan sections do the granular business planning in their own contexts.',
    11),
    ('study-codes',
     'goal',
     2,
     'Study scope — the named codes',
     1,
     $txt$study-codes: the STUDY SCOPE is the run's named code subjects themselves ({{code}}) - the family DEFAULT when the ask names codes. Every step works on the named codes' own readings across the run's analysis items - trend, momentum, volume, valuation, signals, positioning, whatever the items cover: each metric query filters on the named codes, and with several codes named each step compares them side by side (same metric, per code) rather than one code at a time. The scope-entry step anchors every named code (identity, latest trading date); sub-plan forks cut aspects from the item groups and inherit this scope unchanged - no market-wide sweep and no related-code ring (a one-line market backdrop in the scope entry is the most; for market context as a subject use study-market-whole, for correlated companions study-codes-related). FAMILY RULE: exactly one study-scope prompt governs a run (study-codes | study-codes-related | study-market-codes | study-market-whole) - when several render here, follow the one whose applicability matches this run's scope and ignore the rest.$txt$,
     'Default scope when the run names codes: study the named codes themselves only - their own readings, compared side by side; no market sweep, no related ring.',
     'Scope plan prompt — the runner auto-loads this default when the run names codes and picks no study-scope member; picking it explicitly forces it.',
     12),
    ('study-codes-related',
     'goal',
     2,
     'Study scope — named codes + related ring',
     1,
     $txt$study-codes-related: the STUDY SCOPE is the named codes ({{code}}) PLUS their related-code ring - the named codes first, worked exactly as study-codes works them, then the ring: (1) codes that move AGAINST the subject (negatively correlated - hedges and pairs) and (2) the broad market codes the subject reads against (the benchmark index / market aggregate). Pick the ring from DATA, never by assumption: correlate trailing daily returns over a stated window (about a year) between each named code and its candidate codes in the same universe, keep the clearly negative names and the dominant broad-market reference, and REPORT the measured correlations used - the ring is evidence, not decoration. The comparison steps then read subject vs ring: relative strength, divergence, hedge value, beta to the broad market. FAMILY RULE: exactly one study-scope prompt governs a run (study-codes | study-codes-related | study-market-codes | study-market-whole) - when several render here, follow the one whose applicability matches this run's scope and ignore the rest.$txt$,
     'Study the named codes plus their related ring: negatively correlated codes and the broad market references, picked by measured return correlation and reported as evidence.',
     'Scope plan prompt — never auto-loaded; pick it explicitly when the ask wants the correlated / broad-market context around the codes.',
     13),
    ('study-market-codes',
     'goal',
     2,
     'Study scope — whole market to find the codes',
     1,
     $txt$study-market-codes: the run has NO code tag (an All Market scope - {{name}} reads All Market, every metric query spans the whole market with NO subject filter) and the goal is to FIND the desired codes - the family DEFAULT for such runs. Shape the study as a FUNNEL: the early steps sweep the whole market at the aggregate level (breadth and ranking across the run's analysis items - which industries and indices lead, lag, crowd, decay), the middle steps shortlist the codes that best answer the ask (the top of those rankings on the ask's own criteria), the later steps deepen ONLY on the shortlist (each shortlisted code's full reading across the items), and the final answer NAMES the desired codes with the evidence that surfaced them. Keep the screening steps aggregate (rankings, breadth, counts) so the step budget goes to the shortlist, not to the sweep. FAMILY RULE: exactly one study-scope prompt governs a run (study-codes | study-codes-related | study-market-codes | study-market-whole) - when several render here, follow the one whose applicability matches this run's scope and ignore the rest.$txt$,
     'Default scope when the run has no code tag: sweep the whole market, shortlist the codes that best answer the ask, deepen only the shortlist - the answer names the desired codes.',
     'Scope plan prompt — the runner auto-loads this default when the run has no code tag (All Market) and picks no study-scope member; picking it explicitly forces it.',
     14),
    ('study-market-whole',
     'goal',
     2,
     'Study scope — the whole market and its noticeable industries',
     1,
     $txt$study-market-whole: the STUDY SCOPE is the whole market AS A WHOLE - the deliverable is the market's own reading plus its industry map, NOT a pick list of codes. Aggregate first: the market-level state across the run's analysis items (overall trend, breadth, crowdedness, flows, risk posture - whatever the items cover, read at market level with no subject filter), then the NOTICEABLE industries: the outliers the aggregate surfaces - clearly leading and lagging industries, unusual volume or momentum, rotation between them - each named with the metric evidence that makes it noticeable, and the strongest industry narratives stated plainly. Sub-plan forks split naturally per aspect (the aggregate state vs the industry map vs the run's item families) or per industry group when the map is wide. FAMILY RULE: exactly one study-scope prompt governs a run (study-codes | study-codes-related | study-market-codes | study-market-whole) - when several render here, follow the one whose applicability matches this run's scope and ignore the rest.$txt$,
     'Study the whole market as a whole: the market-level state across the items plus the noticeable industries the aggregate surfaces - a market reading and industry map, not a code pick list.',
     'Scope plan prompt — never auto-loaded; pick it explicitly when the ask wants the market-level reading and industry map rather than code picks.',
     15)
ON CONFLICT (prompt_key, prompt_sub_type) DO UPDATE
    SET prompt_type = EXCLUDED.prompt_type,
        title       = EXCLUDED.title,
        instruction = EXCLUDED.instruction,
        preview     = EXCLUDED.preview,
        note        = EXCLUDED.note,
        ordinal     = EXCLUDED.ordinal,
        is_active   = true,
        updated_at  = now();
