Feature: tools — the canned kg_search / db_run_query results
  The tool seam's dataset: Given/When = the call conditions (the tool
  name + needles into the serialized arguments), Then = the result
  text. The kg scenarios cover the varied trader inputs — 均线/排列,
  放量/缩量, macd/金叉, bollinger/布林带, rsi/超买超卖, momentum/动量 —
  each carrying its ``kg_entities:`` marker line (the turn predicates
  in summary_chain key the follow-up db call on it); the final kg
  scenario is the no-match render teaching the synonym loop.

  @kg-ma
  Scenario: kg_search over the moving-average vocabulary
    Given the tool is "kg_search"
    And the arguments contain "均线"
    When the knowledge graph is searched for ma tables
    Then the result is
    """
    kg_entities: ma_spread_detail, mov_ave_price_vs_amt, v_index_daily
    [table] stats.v_index_daily_ma — 指数日线均线物化视图（ma5/ma10/ma20/ma60 列）
    [table] analysis.ma_spread_detail — 长短均线间距（spread/乖离）逐日明细
    [view] v_index_daily — 指数日线行情（code, date, close, trading_amount）
    joins_on: ma_spread_detail joins v_index_daily on (code, date)
    sql views reading this: 3 templates
    """

  @kg-volume
  Scenario: kg_search over the volume/price vocabulary
    Given the tool is "kg_search"
    And the arguments contain "放量"
    When the knowledge graph is searched for volume tables
    Then the result is
    """
    kg_entities: mov_ave_trading_amt, mov_ave_price_vs_amt
    [table] analysis.mov_ave_trading_amt — 成交额均线与放量缩量状态逐日明细
    [table] analysis.mov_ave_price_vs_amt — 量价关系（价与额的均线对比、背离标记）
    joins_on: both join v_index_daily on (code, date)
    sql views reading this: 2 templates
    """

  @kg-macd
  Scenario: kg_search over the macd/cross vocabulary
    Given the tool is "kg_search"
    And the arguments contain "macd"
    When the knowledge graph is searched for macd tables
    Then the result is
    """
    kg_entities: signal_rules, history_signals
    [table] analysis_signals.signal_rules — 信号规则目录（family: macd_cross_up/dn 即金叉/死叉事件）
    [table] analysis_signals.history_signals — 信号触发历史（~24k 行，主键 (code, date, rule)）
    [table] stats.v_index_daily_macd — 日线 macd_line/signal/hist 物化列
    joins_on: history_signals joins signal_rules on (rule_id)
    sql views reading this: 4 templates
    """

  @kg-bollinger
  Scenario: kg_search over the bollinger vocabulary
    Given the tool is "kg_search"
    And the arguments contain "布林带"
    When the knowledge graph is searched for bollinger tables
    Then the result is
    """
    kg_entities: bollinger_detail, ma_spread_detail
    [table] analysis.bollinger_detail — 布林带带宽/上下轨逐日明细
    hint: 布林带带宽即 spread/乖离 家族 — 也可查 ma_spread_detail
    joins_on: bollinger_detail joins v_index_daily on (code, date)
    sql views reading this: 1 templates
    """

  @kg-rsi
  Scenario: kg_search over the rsi/overbought vocabulary
    Given the tool is "kg_search"
    And the arguments contain "超买"
    When the knowledge graph is searched for rsi tables
    Then the result is
    """
    kg_entities: mov_ave_rsi, v_index_daily_rsi
    [table] analysis.mov_ave_rsi — 相对强弱（rsi/超买超卖状态）逐日明细
    [view] v_index_daily_rsi — 指数日线 rsi 物化列
    joins_on: mov_ave_rsi joins v_index_daily on (code, date)
    sql views reading this: 2 templates
    """

  @kg-momentum
  Scenario: kg_search over the momentum vocabulary
    Given the tool is "kg_search"
    And the arguments contain "动量"
    When the knowledge graph is searched for momentum tables
    Then the result is
    """
    kg_entities: mov_ave_momentum
    [table] analysis.mov_ave_momentum — 区间涨跌幅/动量逐日明细
    joins_on: mov_ave_momentum joins v_index_daily on (code, date)
    sql views reading this: 1 templates
    """

  @kg-no-match
  Scenario: an unmatched kg query gets the synonym-loop no-match render
    Given the tool is "kg_search"
    When the knowledge graph finds nothing for the query
    Then the result is
    """
    kg_entities: (none)
    未找到匹配实体。请尝试 2-3 个同义词/换述（中英文 trader 术语均可，
    如 间距→spread→乖离、排列→alignment）后再次 kg_search。
    """

  @db-index-daily
  Scenario: a db_run_query over the daily bars returns the frame
    Given the tool is "db_run_query"
    And the arguments contain "v_index_daily"
    When the replica is queried
    Then the result is
    """
    query_rows: 260
    date,code,close,trading_amount
    2026-11-11,000300,4123.55,287_650_000_000
    2026-11-10,000300,4101.02,301_204_000_000
    2026-11-09,000300,4096.77,265_880_000_000
    (260 rows — head shown; aggregate in pandas or a follow-up small query)
    """
