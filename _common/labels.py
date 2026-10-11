"""Closed-vocabulary label <-> SMALLINT code maps (the label-code contract).

stats.label_dim (database/sql/stats/21_label_dim.sql) is the DB-side
source of truth for the label-code domains; this module is the Python
mirror read/written by the pipelines. temp_scripts/check_label_dim.py
asserts the two stay identical and prints the TypeScript mirror for
data_viz/api (services/label-codes.ts).

The codes exist so label columns NEVER ride cudf.pandas frames as
strings (the cudaErrorIllegalAddress class of 26.08: string host
transfer after sort/merge/concat). Inside a frame a converted label is
an int16 code end-to-end; decode to strings ONLY at the API boundary
(TS decodes) or for display/logging — never mid-compute.

CONVERTED domains (target columns SMALLINT, 0-based; codes live in the
DB — nullable exceptions noted):
  regime      calm=0 hot=1 panic=2 quiet=3
              (== builds.market_regimes.config.REGIMES tuple order, which
              is also lexicographic — ORDER BY code == ORDER BY label)
  side        top=0 bottom=1 flat=2 (flat: the price-vs-amt registry only)
  action      buy=0 sell=1
  weighting   equal=0 amt=1
  rank_side   HYPE=0 DRAIN=1
  pool_size   small=0 mid=1 large=2 all=3
              (semantic order — differs from the old alphabetical TEXT
              order; API sites order via decode, not ORDER BY code)
  px_speed    sharp_dn=0 slow_dn=1 flat=2 slow_up=3 sharp_up=4
              (signed semantic order)
  vol_state   shrink=0 normal=1 heavy=2
  board       MAIN=0 STAR=1 GEM=2 BSE=3
              (NULLable in stats.stock_identity — the source row may
              carry no board)
  sec_owner_type  fund_manager=0 broker=1 asset_manager=2 insurance=3
                  bank=4 company=5 other=6 (NULLable in stats.sec_owners)
  contract_type   index=0 bond=1 (futures)
  sec_type   etf=0 index=1 stock=2
             (converted across the analysis schema — the mov_ave* family
             AND pe / pe_codes / pe_pct / pe_pct_streaks / dividends /
             dividend_est / dividend_deliveries / margin_tech_stats /
             margin_changes, and monitor.src_data_audit. OTHER sec_type
             columns — stats.*, analysis_signals.*, live.* — KEEP TEXT
             (API-facing identity + filter columns); there the dict is
             the frame-only internal code, re-materialized at those write
             boundaries.)
  option_type    CALL=0 PUT=1 (stats.options_terms, analysis.options_* —
                 expiry_identity / skewness_stats / oi_stats /
                 iv_skew_stats / walls)
  ext_resource   http=0 llm=1 (monitor.external_resource_audit.
                 resource_type — the external request audit's family)
  ext_http_source  sse=0 szse=1 bse=2 cffex=3 csindex=4 cnindex=5
                 chinabond=6 shibor=7 huatai=8 eastmoney=9 cninfo=10
                 gov=11 pboc=12 zhihu=13 other=99
                 (external_resource_audit.sub_type when resource_type=0 —
                 the request host family, suffix-matched from the URL)
  ext_llm_provider zhipu=0 other=99 (external_resource_audit.sub_type
                 when resource_type=1 — the provider name)

PENDING domains (vocabulary registered in label_dim and the mirrors, but
the target columns are STILL TEXT and the writers still emit strings —
do NOT use these codes in frames/SQL until the table + writer are
converted):
  skew_type   oi_moneyness=0 iv_smile=1 greek_delta=2 greek_gamma=3
              greek_vega=4 (options skew; live.options_intraday too)
  attribution_type equal=0 trading_amt=1 (industry attributions)
  trade_side  BUY=0 SELL=1 (strategy.trade_decision)

NOT converted (deliberate — see stats.label_dim's header): industry_id /
sector_id / exchange (API-facing identity + filter columns; exchange
additionally blocked by a partition-key table), signal_type /
signal_sub_type (open family identifiers), scale_method
(reserved vocabulary), and the text.* LLM-layer enums.

industry_id STAYS TEXT in the DB (API-facing identity and filter
column). For frame-only traffic use the INTERNAL codes below: attach
once as an int16 column, compute, then re-materialize the string at the
write boundary from the value you attached it from (a loop constant —
no per-row decode ever).
"""
from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
#  Label-code domains — mirror of stats.label_dim (check_label_dim.py gates).
#  CONVERTED columns store these codes in the DB; the PENDING three
#  (skew_type / attribution_type / trade_side) are vocabulary
#  only — their columns are still TEXT until table + writers are converted.
# ---------------------------------------------------------------------------

REGIME_CODE: dict[str, int] = {"calm": 0, "hot": 1, "panic": 2, "quiet": 3,
                         "all": 4}  # 4 = the POOLED population (signal_curves only)
SIDE_CODE: dict[str, int] = {"top": 0, "bottom": 1, "flat": 2}
ACTION_CODE: dict[str, int] = {"buy": 0, "sell": 1}
# the detection family (analysis_signals.signal_thresholds /
# history_signals / signal_curves — converted 2026-10-10; registry
# order = pipeline order)
SIGNAL_TYPE_CODE: dict[str, int] = {"mov_rsi": 0, "bollinger": 1}
WEIGHTING_CODE: dict[str, int] = {"equal": 0, "amt": 1}
RANK_SIDE_CODE: dict[str, int] = {"HYPE": 0, "DRAIN": 1}
POOL_SIZE_CODE: dict[str, int] = {"small": 0, "mid": 1, "large": 2, "all": 3}
PX_SPEED_CODE: dict[str, int] = {
    "sharp_dn": 0, "slow_dn": 1, "flat": 2, "slow_up": 3, "sharp_up": 4,
}
VOL_STATE_CODE: dict[str, int] = {"shrink": 0, "normal": 1, "heavy": 2}
BOARD_CODE: dict[str, int] = {"MAIN": 0, "STAR": 1, "GEM": 2, "BSE": 3}
SEC_OWNER_TYPE_CODE: dict[str, int] = {
    "fund_manager": 0, "broker": 1, "asset_manager": 2, "insurance": 3,
    "bank": 4, "company": 5, "other": 6,
}
OPTION_TYPE_CODE: dict[str, int] = {"CALL": 0, "PUT": 1}
SKEW_TYPE_CODE: dict[str, int] = {
    "oi_moneyness": 0, "iv_smile": 1, "greek_delta": 2, "greek_gamma": 3,
    "greek_vega": 4,
}
CONTRACT_TYPE_CODE: dict[str, int] = {"index": 0, "bond": 1}
SEC_TYPE_CODE: dict[str, int] = {"etf": 0, "index": 1, "stock": 2}
ATTRIBUTION_TYPE_CODE: dict[str, int] = {"equal": 0, "trading_amt": 1}
TRADE_SIDE_CODE: dict[str, int] = {"BUY": 0, "SELL": 1}
# external request audit (monitor.external_resource_audit) — sub_type's
# domain is selected by resource_type (http -> ext_http_source,
# llm -> ext_llm_provider); 'other'=99 is the unrecognized fallback.
EXT_RESOURCE_CODE: dict[str, int] = {"http": 0, "llm": 1}
EXT_HTTP_SOURCE_CODE: dict[str, int] = {
    "sse": 0, "szse": 1, "bse": 2, "cffex": 3, "csindex": 4,
    "cnindex": 5, "chinabond": 6, "shibor": 7, "huatai": 8,
    "eastmoney": 9, "cninfo": 10, "gov": 11, "pboc": 12, "zhihu": 13,
    "other": 99,
}
EXT_LLM_PROVIDER_CODE: dict[str, int] = {"zhipu": 0, "other": 99}
# DB health sampler (monitor.db_health) — replication_state is the WAL
# shipping state as seen from the SAMPLED instance (primary: the standby's
# pg_stat_replication.state; replica: pg_stat_wal_receiver.status);
# 'none'=99 = no standby attached / receiver absent.
DB_INSTANCE_CODE: dict[str, int] = {"primary": 0, "replica": 1}
DB_REPLICATION_STATE_CODE: dict[str, int] = {
    "streaming": 0, "catchup": 1, "startup": 2, "backup": 3,
    "stopping": 4, "connecting": 5, "stopped": 6, "none": 99,
}

LABEL_MAPS: dict[str, dict[str, int]] = {
    "regime": REGIME_CODE,
    "side": SIDE_CODE,
    "action": ACTION_CODE,
    "signal_type": SIGNAL_TYPE_CODE,
    "weighting": WEIGHTING_CODE,
    "rank_side": RANK_SIDE_CODE,
    "pool_size": POOL_SIZE_CODE,
    "px_speed": PX_SPEED_CODE,
    "vol_state": VOL_STATE_CODE,
    "board": BOARD_CODE,
    "sec_owner_type": SEC_OWNER_TYPE_CODE,
    "option_type": OPTION_TYPE_CODE,
    "skew_type": SKEW_TYPE_CODE,
    "contract_type": CONTRACT_TYPE_CODE,
    "sec_type": SEC_TYPE_CODE,
    "attribution_type": ATTRIBUTION_TYPE_CODE,
    "trade_side": TRADE_SIDE_CODE,
    "ext_resource": EXT_RESOURCE_CODE,
    "ext_http_source": EXT_HTTP_SOURCE_CODE,
    "ext_llm_provider": EXT_LLM_PROVIDER_CODE,
    "db_instance": DB_INSTANCE_CODE,
    "db_replication_state": DB_REPLICATION_STATE_CODE,
}

# code -> label per domain (int keys)
CODE_MAPS: dict[str, dict[int, str]] = {
    domain: {code: label for label, code in mapping.items()}
    for domain, mapping in LABEL_MAPS.items()
}

REGIME_LABEL: dict[int, str] = CODE_MAPS["regime"]
SIDE_LABEL: dict[int, str] = CODE_MAPS["side"]
ACTION_LABEL: dict[int, str] = CODE_MAPS["action"]
SIGNAL_TYPE_LABEL: dict[int, str] = CODE_MAPS["signal_type"]
WEIGHTING_LABEL: dict[int, str] = CODE_MAPS["weighting"]
RANK_SIDE_LABEL: dict[int, str] = CODE_MAPS["rank_side"]
POOL_SIZE_LABEL: dict[int, str] = CODE_MAPS["pool_size"]
PX_SPEED_LABEL: dict[int, str] = CODE_MAPS["px_speed"]
VOL_STATE_LABEL: dict[int, str] = CODE_MAPS["vol_state"]
BOARD_LABEL: dict[int, str] = CODE_MAPS["board"]
SEC_OWNER_TYPE_LABEL: dict[int, str] = CODE_MAPS["sec_owner_type"]
OPTION_TYPE_LABEL: dict[int, str] = CODE_MAPS["option_type"]
SKEW_TYPE_LABEL: dict[int, str] = CODE_MAPS["skew_type"]
CONTRACT_TYPE_LABEL: dict[int, str] = CODE_MAPS["contract_type"]
SEC_TYPE_LABEL: dict[int, str] = CODE_MAPS["sec_type"]
ATTRIBUTION_TYPE_LABEL: dict[int, str] = CODE_MAPS["attribution_type"]
TRADE_SIDE_LABEL: dict[int, str] = CODE_MAPS["trade_side"]
EXT_RESOURCE_LABEL: dict[int, str] = CODE_MAPS["ext_resource"]
EXT_HTTP_SOURCE_LABEL: dict[int, str] = CODE_MAPS["ext_http_source"]
EXT_LLM_PROVIDER_LABEL: dict[int, str] = CODE_MAPS["ext_llm_provider"]
DB_INSTANCE_LABEL: dict[int, str] = CODE_MAPS["db_instance"]
DB_REPLICATION_STATE_LABEL: dict[int, str] = CODE_MAPS["db_replication_state"]

# Display order for responses that list a domain's vocabulary —
# pool_size deliberately NOT in code order here: API/UI lists keep the
# historical TEXT presentation order (alphabetical) so the contract the
# UI already renders is unchanged.
LABEL_ORDER: dict[str, tuple[str, ...]] = {
    "regime": tuple(REGIME_CODE),
    "side": tuple(SIDE_CODE),
    "action": tuple(ACTION_CODE),
    "signal_type": tuple(SIGNAL_TYPE_CODE),
    "weighting": tuple(WEIGHTING_CODE),
    "rank_side": tuple(RANK_SIDE_CODE),
    "pool_size": ("all", "large", "mid", "small"),
    "px_speed": ("sharp_dn", "slow_dn", "flat", "slow_up", "sharp_up"),
    "vol_state": ("shrink", "normal", "heavy"),
    "board": ("MAIN", "STAR", "GEM", "BSE"),
    "sec_owner_type": tuple(SEC_OWNER_TYPE_CODE),
    "option_type": ("CALL", "PUT"),
    "skew_type": ("oi_moneyness", "iv_smile", "greek_delta",
                  "greek_gamma", "greek_vega"),
    "contract_type": ("index", "bond"),
    "sec_type": ("etf", "index", "stock"),
    "attribution_type": ("equal", "trading_amt"),
    "trade_side": ("BUY", "SELL"),
    "ext_resource": tuple(EXT_RESOURCE_CODE),
    "ext_http_source": tuple(EXT_HTTP_SOURCE_CODE),
    "ext_llm_provider": tuple(EXT_LLM_PROVIDER_CODE),
    "db_instance": tuple(DB_INSTANCE_CODE),
    "db_replication_state": tuple(DB_REPLICATION_STATE_CODE),
}


def code_labels(domain: str) -> np.ndarray:
    """Labels of ``domain`` as an object ndarray INDEXED BY CODE.

    The decode table for :func:`decode_codes` — one allocation per call
    is fine (2-4 labels per domain).
    """
    mapping = LABEL_MAPS[domain]
    return np.asarray(mapping, dtype=object)[
        np.argsort(np.fromiter(mapping.values(), dtype=np.int64))
    ]


def decode_codes(codes, domain: str) -> np.ndarray:
    """Host int-code array -> object ndarray of labels (host numpy only).

    The write/display boundary decode. ``codes`` must already be a RAW
    host ndarray (unwrap proxies via ``host_array`` first — the repo
    boundary rule); an out-of-range code raises IndexError (fail fast —
    the only valid codes are 0..len-1, no missing sentinel exists for
    these NOT NULL columns).
    """
    return code_labels(domain)[np.asarray(codes)]

