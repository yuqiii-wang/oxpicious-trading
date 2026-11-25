-- ============================================================================
--  static.label_dim seed — MONITORING / INFRASTRUCTURE vocabularies
--
--  The operational monitors' closed vocabularies: the external request
--  audit's resource family, request host and LLM provider, the DB
--  health sampler's instance identity + WAL shipping state, the
--  secret registry's credential type + service tier, and the
--  maintainer backup audit's batch mode + outcome, and the per-table
--  workload audit's write-heavy verdict. Owning
--  tables: monitor.external_resource_audit (ext_resource /
--  ext_http_source / ext_llm_provider — sub_type's domain is selected
--  by resource_type), monitor.db_health (db_instance /
--  db_replication_state), static.secret_config (secret_type — the
--  provider domain is selected by it: ext_llm_provider when
--  llm / jev / tokenization, ext_http_source when web_search;
--  secret_sub_type — the credential service tier, ONE shared domain
--  across all types; input_modality / output_modality — the
--  llm_modality profile a credential's model accepts / produces, ONE
--  shared domain across both columns), maintenance.src_data_backup (backup_mode /
--  backup_status), monitor.db_table_audit.write_flag +
--  monitor.db_table_alert.severity (db_write_flag),
--  monitor.non_daily_task_state.state (non_daily_state),
--  monitor.caution_log (caution_source / caution_severity),
--  monitor.orchestration_task_state.state (orchestration_plan_state —
--  the arrangement verdict due / not_due of ONE orchestration-tier
--  service, written by the monitor area's OrchestrationArranger) and
--  .basis (orchestration_plan_basis — WHAT that verdict rests on: the
--  freq-calendar rule, or the data pre-check's reading of the missing
--  / downloaded sessions behind the nightly chain's run / skip),
--  monitor.service_command.command (service_command — the downward
--  mailbox command, Phase 4 of the 2026-11-21 control unification
--  plan), monitor.service_heartbeat.progress_phase /
--  progress_state (progress_phase / progress_state — the child-
--  reported phase machine + run state at its safe checkpoints).
-- ============================================================================

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- ext_resource: monitor.external_resource_audit.resource_type — the
    -- external resource family (0 downloads HTTP proxy, 1 llm providers)
    ('ext_resource',     0, 'http', 0),
    ('ext_resource',     1, 'llm',  1),
    -- ext_http_source: sub_type when resource_type = 0 — the request
    -- host family (suffix-matched from the URL by
    -- _common/external_resource_audit.py); 99 = unrecognized host
    ('ext_http_source',  0, 'sse',        0),
    ('ext_http_source',  1, 'szse',       1),
    ('ext_http_source',  2, 'bse',        2),
    ('ext_http_source',  3, 'cffex',      3),
    ('ext_http_source',  4, 'csindex',    4),
    ('ext_http_source',  5, 'cnindex',    5),
    ('ext_http_source',  6, 'chinabond',  6),
    ('ext_http_source',  7, 'shibor',     7),
    ('ext_http_source',  8, 'huatai',     8),
    ('ext_http_source',  9, 'eastmoney',  9),
    ('ext_http_source', 10, 'cninfo',    10),
    ('ext_http_source', 11, 'gov',       11),
    ('ext_http_source', 12, 'pboc',      12),
    ('ext_http_source', 13, 'zhihu',     13),
    ('ext_http_source', 14, 'sina',      14),
    -- zhipu joins as an HTTP source for static.secret_config's
    -- web_search rows (the BigModel web_search endpoint) — the audit
    -- itself records zhipu traffic as ext_llm_provider.
    ('ext_http_source', 15, 'zhipu',     15),
    -- E Fund fund-site family (downloads.etf.efunds.*): the efundcf.com.cn
    -- detail pages and the www/api.efunds.com.cn fundlist/assetinfo backends.
    ('ext_http_source', 16, 'efunds',    16),
    -- SGE 上海黄金交易所 (downloads.macro.gold_silver): the sge.com.cn
    -- daily spot bars backend.
    ('ext_http_source', 17, 'sge',       17),
    ('ext_http_source', 99, 'other',     99),
    -- ext_llm_provider: sub_type when resource_type = 1 — the provider
    -- name (RetryingJsonClient's owning BaseLlmProvider.name); 99 fallback
    ('ext_llm_provider',  0, 'zhipu',  0),
    -- dummy = the offline test double (test/dummy_langgraph at the
    -- project root — canned replies; the simulated endpoint lives in
    -- server_cluster.llm): the always-present llm secret_config row's
    -- provider label (secrets/01_seed_secret_config.sql), never an
    -- audited live provider.
    ('ext_llm_provider',  1, 'dummy',  1),
    -- local = the resident local-model host's REAL chat model
    -- (server_cluster.llm — the cluster's llm area, migrated from
    -- llm_agents.local_model_host 2026-11 — OpenAI-compatible
    -- /v1/chat/completions over the manager-loaded jev models, e.g.
    -- onejev-1.6-4b): the local secret_config row's provider label.
    -- Registry-only like dummy — the audit records the transport's own
    -- name (ZhipuChatProvider.name = zhipu).
    ('ext_llm_provider',  2, 'local',  2),
    ('ext_llm_provider', 99, 'other', 99),
    -- secret_type: static.secret_config.secret_type — the credential
    -- family a secret row serves. Its PROVIDER column's domain is
    -- selected by this one: ext_llm_provider when llm / jev /
    -- tokenization, ext_http_source when web_search (the audit's
    -- selected-domain pattern). jev = the local-model host's Jev /
    -- System One decision-model credentials (onejev-1.6-4b, the
    -- standard /v1/systemone contract); tokenization = its StructBERT
    -- word-segmenter credential (word-segmenter-zh,
    -- POST /v1/tokenization/segment) — both retyped out of llm
    -- 2026-11-11: they are the resident host's model-serving
    -- credentials, NOT chat-LLM ones (the deep agent's chat config
    -- reads ONLY llm rows).
    ('secret_type', 0, 'llm',  0),
    ('secret_type', 1, 'web_search', 1),
    ('secret_type', 2, 'jev', 2),
    ('secret_type', 3, 'tokenization', 3),
    -- 4 ocr = the resident host's image-transcription credential
    -- (paddleocr-vl-1.6, POST /v1/ocr) — a model-serving credential
    -- like jev/tokenization, not a chat-LLM one (2026-11-22; the
    -- news image lane's Pseudo Imagea records it in the registry)
    ('secret_type', 4, 'ocr', 4),
    -- secret_sub_type: static.secret_config.sub_type — the credential
    -- SERVICE TIER, one shared domain across both secret_types (0
    -- general = the standard tier — glm flash chat models, the
    -- offline dummy, generic content search; 1 pro = the paid-pro
    -- tier — the zhipu search_pro web-search engine)
    ('secret_sub_type', 0, 'general', 0),
    ('secret_sub_type', 1, 'pro', 1),
    -- db_instance: monitor.db_health.instance — the Postgres instance a
    -- health sample came from (docker-compose: db + db-replica)
    ('db_instance', 0, 'primary', 0),
    ('db_instance', 1, 'replica', 1),
    -- db_replication_state: monitor.db_health.replication_state — the WAL
    -- shipping state AS SEEN FROM THE SAMPLED INSTANCE (primary: the
    -- standby's pg_stat_replication.state; replica:
    -- pg_stat_wal_receiver.status). Code order is semantic: streaming is
    -- the only healthy state; 99 none = no standby attached (primary) /
    -- receiver absent (replica)
    ('db_replication_state',  0, 'streaming',  0),
    ('db_replication_state',  1, 'catchup',    1),
    ('db_replication_state',  2, 'startup',    2),
    ('db_replication_state',  3, 'backup',     3),
    ('db_replication_state',  4, 'stopping',   4),
    ('db_replication_state',  5, 'connecting', 5),
    ('db_replication_state',  6, 'stopped',    6),
    ('db_replication_state', 99, 'none',      99);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- backup_mode: maintenance.src_data_backup.mode — the robocopy
    -- batch's scope (code order is semantic: incremental only exists
    -- after a full)
    ('backup_mode', 0, 'full',         0),
    ('backup_mode', 1, 'incremental',  1),
    -- backup_status: maintenance.src_data_backup.status — the batch
    -- outcome, from robocopy's exit code bits (0-3 ok, 4-7 mismatches
    -- present, 8+ copy failures / fatal). Code order is semantic:
    -- best -> worst
    ('backup_status', 0, 'ok',      0),
    ('backup_status', 1, 'partial', 1),
    ('backup_status', 2, 'failed',  2),
    -- db_write_flag: monitor.db_table_audit.write_flag +
    -- monitor.db_table_alert.severity — the per-table write-heavy verdict
    -- of the db_table_audit sampler ((ins+upd+del rows)/s over the sample
    -- window vs the fixed amber/red thresholds). Code order is semantic:
    -- best -> worst (mirrors backup_status).
    ('db_write_flag', 0, 'ok',    0),
    ('db_write_flag', 1, 'amber', 1),
    ('db_write_flag', 2, 'red',   2);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- non_daily_state: monitor.non_daily_task_state.state — the
    -- identity-grounded completeness verdict of ONE non-daily task
    -- (monthly/quarterly/semiannual tiers) checked by the monitor
    -- area's NonDailyOps; the maintainer's Saturday backfill re-runs
    -- code 2 rows weekly until they pass. Code order is semantic:
    -- unknown -> complete -> missing -> the check itself failed
    ('non_daily_state', 0, 'unknown',       0),
    ('non_daily_state', 1, 'complete',      1),
    ('non_daily_state', 2, 'missing',       2),
    ('non_daily_state', 3, 'check_failed',  3);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- caution_source: monitor.caution_log.source — where a caution row
    -- came from: a running service hitting an error, an AI run flagging
    -- something suspicious, or a pending bug noted by hand
    -- (python -m _common.caution_log, the dev-skill rule)
    ('caution_source', 0, 'service', 0),
    ('caution_source', 1, 'ai',      1),
    ('caution_source', 2, 'manual',  2),
    -- caution_severity: monitor.caution_log.severity_level — how bad the
    -- caution is. Code order is semantic, best -> worst (service errors
    -- default to 2, AI suspicions to 1, AI [CRITICAL] findings are 3)
    ('caution_severity', 0, 'info',     0),
    ('caution_severity', 1, 'warning',  1),
    ('caution_severity', 2, 'error',    2),
    ('caution_severity', 3, 'critical', 3);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- orchestration_plan_state: monitor.orchestration_task_state.state —
    -- the ARRANGEMENT verdict of ONE orchestration-tier service, computed
    -- by the monitor area's OrchestrationArranger from the service_config
    -- freq window vs the last successful exit (the same rule as the UI
    -- run guard's FREQ_PERIOD_MS), or — for a service with a declared
    -- data pre-check — from what that pre-check found (see
    -- orchestration_plan_basis): due = run it in the arranged pass;
    -- not_due = the freq window holds (or once + a success ever) or the
    -- pre-check found no work — the nightly chain skips the step instead
    -- of spawning a no-op. Code order is semantic: unknown -> due ->
    -- not_due.
    ('orchestration_plan_state', 0, 'unknown',  0),
    ('orchestration_plan_state', 1, 'due',      1),
    ('orchestration_plan_state', 2, 'not_due',  2);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- orchestration_plan_basis: monitor.orchestration_task_state.basis —
    -- the EVIDENCE behind the arrangement verdict. calendar = the
    -- service declares no data pre-check, so the freq-calendar rule
    -- decided (every cadence tier, a no-window freq, an unconfigured
    -- step). The rest are the nightly chain's data pre-check over the
    -- latest closed sessions: data_present (every expected session is
    -- in the service's data table — nothing to do),
    -- db_missing (a session is absent from that table and no source-csv
    -- stage applies — the service runs to produce it), csv_present
    -- (absent from the table but its source csv is downloaded — a
    -- download skips, its build loads), csv_missing (absent and not
    -- downloaded either — a download runs, a build waits for it),
    -- check_failed (the pre-check itself errored — the service runs
    -- fail-open, its own guards decide). csv_present / csv_missing flip
    -- the verdict by ROLE (fetch / load), so state stays the verdict
    -- and basis the reading.
    ('orchestration_plan_basis', 0, 'calendar',     0),
    ('orchestration_plan_basis', 1, 'data_present', 1),
    ('orchestration_plan_basis', 2, 'db_missing',   2),
    ('orchestration_plan_basis', 3, 'csv_present',  3),
    ('orchestration_plan_basis', 4, 'csv_missing',  4),
    ('orchestration_plan_basis', 5, 'check_failed', 5);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- service_command: monitor.service_command.command — the downward
    -- mailbox command a running child claims+consumes from its
    -- heartbeat thread (control unification plan Phase 4, 2026-11-21;
    -- SIGTERM stays the backstop). Code order = delivery order in the
    -- fleet's command surface.
    ('service_command', 0, 'flush_now', 0),
    ('service_command', 1, 'pause',     1),
    ('service_command', 2, 'resume',    2),
    -- progress_phase: monitor.service_heartbeat.progress_phase — the
    -- child-REPORTED work phase at its last safe checkpoint (the
    -- three-phase machine per run: what the DB frontier cannot see
    -- about in-flight buffers). NULL = never reported.
    ('progress_phase', 0, 'downloading', 0),
    ('progress_phase', 1, 'reading',     1),
    ('progress_phase', 2, 'writing',     2),
    -- progress_state: monitor.service_heartbeat.progress_state — the
    -- child-REPORTED run state at its last safe checkpoint; a FRESH
    -- beat with paused is a DELIBERATE pause, not a wedged runtime
    -- (the hung detector's legal state). Code order = lifecycle:
    -- running -> pausing -> paused -> done. NULL = never reported.
    ('progress_state', 0, 'running', 0),
    ('progress_state', 1, 'pausing', 1),
    ('progress_state', 2, 'paused',  2),
    ('progress_state', 3, 'done',    3);

INSERT INTO static.label_dim (domain, code, label, sort_order) VALUES
    -- llm_modality: static.secret_config.input_modality /
    -- output_modality — the MODALITY PROFILE a credential's model
    -- accepts (input) / produces (output), ONE shared domain across
    -- both columns (classifies the profile, not the provider — the
    -- secret_sub_type pattern). 0 text = plain text in/out; 1
    -- text+vision = the model also accepts images (the VLM case —
    -- glm-5.3-flash); 2 text+image-gen = it also GENERATES images;
    -- 3 audio = audio in/out; 99 other. DEFAULT 0 — an unspecified
    -- modality is plain text.
    ('llm_modality', 0,  'text',           0),
    ('llm_modality', 1,  'text+vision',    1),
    ('llm_modality', 2,  'text+image-gen', 2),
    ('llm_modality', 3,  'audio',          3),
    ('llm_modality', 99, 'other',          99);
