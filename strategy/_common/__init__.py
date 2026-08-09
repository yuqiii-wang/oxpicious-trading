"""Internal shared utilities for strategy backtest + risk pipelines.

Anything strategy-agnostic that would be reused by a future strategy
(mean-reversion, momentum, etc.) lives here:

  • constants.py — sec_types, batch size, default buy_notional, basic-stats table map
  • db.py        — async DB connection (re-export of _common.build_commons)
  • fetch.py     — discover available codes in analysis.mov_ave_spreads_detail
                   + fetch trade_decision rows for a (seq_id, code)
  • upsert.py    — strategy_seq + trade_decision upsert (resolve_seq_no,
                   insert_strategy_seq, assign_decision_no, insert_decisions)
  • runner.py    — the batched fetch→backtest→upsert loop used by __main__
                   scripts across all sec_types

Strategy-specific signal generation, portfolio engines, and SQL JOINs stay
in their own package (e.g. strategy.ma_spread_trading).
"""
