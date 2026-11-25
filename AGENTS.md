# oxpicious-trading — agent instructions

Always-loaded workspace instructions. The skills under `.agents/skills/`
carry the details; this file routes to them and pins the preferences that
must apply even when a skill is not invoked.

## Skill routing — load the matching skill BEFORE that kind of work

Do not wait for the user to name a skill; invoke it via the Skill tool as
the first step of the work:

- ANY code change, DB work, script run, or verification in this repo →
  `oxpicious-dev-conventions`
- ANY dataframe / pandas / numpy / cudf code — new compute, rolling /
  groupby / shift, dtypes, DB fetch or bulk-write paths, or a run logging
  `[cudf fallback]` lines → `cudf-dtypes`
- ANY DB write / insert / upsert / purge / DDL apply → `db-sql-write`
- ANY table creation/alter with a type/kind/state/family column, or any
  label-vocabulary domain change → `db-smallint-type-codes`
- ANY authoring/extending of the DB-semantics knowledge graph (llm.kg_*
  entities/edges, seed files under
  `database/sql/llm/static/knowledge_graphs/`, a table's KG payload in
  its own DDL file's leading banner under `database/sql/`) →
  `kg-db-semantics`
- ANY chart component, ECharts option builder, or chart tooltip/colors
  under `data_viz/src` → `ui-charts`
- ANY stacked multi-chart panel with a shared time window, time slider,
  or cross-chart hover/tooltip sync under `data_viz/src` →
  `time-slider-dev`
- ANY verification of `data_viz/` work → `ui-test`
- ANY code that runs in the LLM py sandbox (sandbox_* tools,
  deep-agent compute) or any change to the sandbox env/limits/monitoring
  (`llm_agents/sandbox/`, `py-sandbox` compose service) →
  `py-sandbox-env`
- ANY change or improvement plan for the deep agent harness
  (`llm_agents/deep_agents` — core loop, planning, graph, delegation,
  prompts, server, memory / verification / eval design) →
  `deep-agent-blueprint`
- ANY langgraph / deep-agent test or probe that would call an LLM → run
  it on the DUMMY LLM double (`test/dummy_llm` at the project root —
  the request→response `DummyLlmProvider` in-process, matched request →
  the feature's canned reply; the kg/jev cucumber assertion datasets
  stay in the sibling `test/dummy_langgraph`), or HTTP-sim against the
  resident local-model host's dummy-1 endpoint (127.0.0.1:8767) with
  rate-limit error injection; see `deep-agent-blueprint`
- ANY change to the resident local-model host (`server_cluster/llm` —
  the server cluster's llm area (ex `llm_agents/local_model_host`),
  the FastAPI over the repo's local AI models by
  KIND: ``tokenization`` (StructBERT word segmenter), ``jev`` (Jev /
  System One decision models — OneJev; the standard `/v1/systemone`
  contract: noul/choice/score → calibrated probabilities), ``ocr``
  (PaddleOCR-VL image transcription — the `/v1/ocr` contract: image
  files → the extracted text), plus the OpenAI chat completions
  surface and the model load/offload management) → keep the serving
  contracts: the segmenter is host-first with an in-process fallback
  (`_common/tokenization/chinese_word_segmenter.py`), the dummy-1
  simulation preserves the former standalone dummy-llm server
  (`test.dummy_llm.server`, removed 2026-11) semantics verbatim
  (Bearer-401 code 1002, zhipu-coded 429s, POST /control)
- ANY use of the Jev decision models — code/agents asking for
  calibrated probabilities or typed decisions (noul/choice/score),
  the `/v1/systemone` contract, the deep-agent decision submodule
  (`llm_agents/deep_agents/langgraph/decision/`), or debugging
  systemone responses → `jev-systemone`
- ANY daily OHLCV / K-line study (fetching index/etf/stock bars,
  MA/RSI/MACD/momentum studies) or interpretation of the Chinese volume
  vocabulary (放量, 缩量, 量价背离, 缩量弱反弹 …) → `ohlcv`
- ANY debt / money-market work (PBoC OMO / 逆回购 / outright repo
  买断式逆回购 / MLF, SHIBOR, LPR, ChinaBond 国债 yield curve, pboc_oma
  policy notices — the `stats.debt_*` tables, `v_debt_daily`, the
  `builds/bond` pipeline, `/api/debt`, or the Debt Market page) →
  `debt-market`
- analysis_signals semantics (rule cells, history vs live, the nine
  families) → `signals-live-vs-history`
- financial/biz semantics of the mov_rsi / bollinger / ma_cross /
  options_wall / options_vix / momentum / rf_break / rebound / macd
  signal families
  (trigger meaning, parameters, sides, evidence) → `signals-biz-logic`
- ANY work on the no-action / negative-confidence tier — the
  invalidation module (`engines/no_action`), the reject tier
  (`engines/reject`), the `no_action_signals` table, per-family
  invalidation / misleading conditions, or the recent-days same-type
  fatigue filter → `signals-no-action`
- ANY change to the singleton strategy training model (Train Model =
  `strategy.factors_and_algos._optm_engine`, the losses / spaces /
  NestedTrainer), the trained `algo_configs` params shape, or plans to
  parametrize trained configs (signal cancel rules between families,
  signal weights, position-aware sizing utils, max-return goal) →
  `singleton-strategy-tuning`
- news provider/content audit (text.news_providers /
  text.news_provider_audit / text.news_content_audit), the
  `analysis.news_audit` pipeline, the 5-day audit window grid or the
  Composites News Audit page → `news-audit`

## cudf preference (short form — the `cudf-dtypes` skill is authoritative)

- Whole-frame pandas is the default: grouped rolling / shift / diff via
  the shared `_common.df_utils` helpers and elementwise `where`-chains run
  on GPU under cudf.pandas; prefer ONE whole-frame grouped pass over
  per-group compute loops.
- Plain host numpy only as ONE per-group boundary core for gather/index
  algorithms (positional gathers, sparse tables, row×day 2D grids, DB
  array-literal building) — unwrap once via `host_array`, materialize via
  the REAL DataFrame ctor, document the boundary in the module docstring.
- NEVER mid-flow hybrids: `pd.Series(host_ndarray, index=...)` and
  `Series.take(host_positions)` return garbage-length Series under
  cudf.pandas 26.08 (2026-10-02 probes). No scalar-`pd.NaT` column init
  (infers str). No `np.searchsorted` on proxy positions — use prefix-rank
  arithmetic.
- A dataframe refactor is done only when the output is bit-identical
  (per-code md5 A/B) AND the `[cudf fallback]` signature mix does not
  grow.

## Standing conventions (details in `oxpicious-dev-conventions`)

- Backend Python is class-first OOP: implement with classes — pydantic
  (`BaseModel`) for typed payloads — rather than module-level static
  functions; free functions only for tiny pure helpers and throwaway
  `temp_scripts/` scripts.
- SQL-first DB changes (`database/sql/`), then bridge to Python.
- Pipelines are incremental by default (`--force` purges + regenerates);
  reads filter on the target table's primary key.
- Run Python through WSL conda (command in the conventions skill); scope
  test runs with `--sec-type index`.
- Study scope: only the 10 named indices unless explicitly instructed.
- Throwaway scripts and outputs go in `temp_scripts/`.
- Verify changes by sending the request from the UI and checking the
  logs — never by directly testing in Python (details in the conventions
  skill).
