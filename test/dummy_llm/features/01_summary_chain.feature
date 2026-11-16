Feature: summary_chain — the ai-summary langgraph chain's canned turns
  The dataset for one REAL run_thread walk (plan → custom → result).
  Given = the structural input the conversation carries; When = the
  discriminating trigger; Then = the reply (DocString) — text, or a
  tool call whose DocString is the JSON arguments. File order is match
  order — specific scenarios first, the catch-all findings last.

  Stage discrimination: each step's own directive carries its node
  bracket ([step_1] / [step_2] — never quoted by any other stage);
  the review seats key on their seat tags; the two OUTPUT nodes on
  the doc-kind phrases their directives persist under. The kg turns
  ask at FULL detail so the REAL jev relevance gate judges a live
  traversal every run (a lean hotpath replay would ship ungated);
  the db turn follows whichever face the gate produced — a confident
  reject ships the synonym nudge ([Jev relevance gate …]), a
  below-floor read ships the full render (KG search …) — so each step
  carries BOTH db faces.

  THE SUPERVISOR WALKS (probe_dummy_langgraph_supervisor.py) — the
  failure nodes orchestrated by the supervisor, walked E2E: the
  ignore walk (忽略量价失败) fails step 2 on a STRUCTURAL key error
  (a non-transient flake face) whose repair ask replies
  `Action: ignore-step` — the row settles skipped wearing the
  supervisor's note and the run resumes PAST it to the answer; the
  no-action walk (容忍量价失败) fails step 2 on a missing table, the
  ask replies `Action: no-action`, the row stays failed and the
  doomed tail settles skipped. The verdict faces key on the failed
  node's ERROR text (the manifest's error line — a needle neither
  the base walk's ReadTimeout flake nor any other conversation
  carries); the flake faces key on the walk's goal marker so the
  standing step-volume-flake (transient, armed for the base walks)
  never fires in these walks. THE RATE-LIMIT WALKS (限流重试) fail
  step 2 on a zhipu 1302 速率限制 — transient AND rate-limited per
  the real `supervisor.repair` classifiers — whose ask replies
  `Action: retry-step`: the generative walk asserts the throttled
  reopen + the resumed hop, the jev fast-path walk the SAME failure
  decided by the injected stub engine's confident resume read (no
  generative ask, the [System One verdict] review row persisted). THE
  CAP-CLASS WALK (步数耗尽重试, 2026-11-15) fails step 2 by BURNING its
  whole conversation budget (every turn decodes label codes — the
  run-532c6a78 waste shape) so the loop's cap raise records real work
  (8 spent tool calls); the ask replies `Action: retry-step` (the
  cap-class face keyed on the manifest's step-limit line), the lane
  re-opens the row with a RAISED step budget (base 8 + bump 16 = 24 —
  the resumed face pins the exact `conversation budget: 24 steps` line
  the resume block renders), and the resumed pass completes.

  @plan-ignore-volume
  Scenario: the ignore walk's planner proposes the dispensable-step plan
    Given the system contains "planning node"
    And the conversation contains "忽略量价失败"
    When the planner is asked for the plan proposal
    Then the reply is
    """
    {"steps": [
      {"title": "拉取沪深300日线并计算均线趋势",
       "steps": ["用 kg_search 定位均线研究的数据表",
                 "用 db_run_query 读取 v_index_daily 的收盘序列",
                 "计算 MA5/MA20 均线与多头排列状态",
                 "给出趋势结论并标记 [CRITICAL] 数据窗口"]},
      {"title": "量价关系观察",
       "steps": ["用 kg_search 定位量价研究的数据表",
                 "对比近20日成交额的放量缩量节奏",
                 "总结量价背离信号"]}
    ]}
    """

  @plan-noaction-volume
  Scenario: the no-action walk's planner proposes the doomed-tail plan
    Given the system contains "planning node"
    And the conversation contains "容忍量价失败"
    When the planner is asked for the plan proposal
    Then the reply is
    """
    {"steps": [
      {"title": "拉取沪深300日线并计算均线趋势",
       "steps": ["用 kg_search 定位均线研究的数据表",
                 "用 db_run_query 读取 v_index_daily 的收盘序列",
                 "计算 MA5/MA20 均线与多头排列状态",
                 "给出趋势结论并标记 [CRITICAL] 数据窗口"]},
      {"title": "量价关系观察",
       "steps": ["用 kg_search 定位量价研究的数据表",
                 "对比近20日成交额的放量缩量节奏",
                 "总结量价背离信号"]},
      {"title": "汇总两步发现并给出综合结论",
       "steps": ["汇总前两步的发现与分歧",
                 "给出综合结论"]}
    ]}
    """

  @plan-proposal
  Scenario: the planner proposes the whole plan in one batch
    Given the system contains "planning node"
    When the planner is asked for the plan proposal
    Then the reply is
    """
    {"steps": [
      {"title": "拉取沪深300日线并计算均线趋势",
       "steps": ["用 kg_search 定位均线研究的数据表",
                 "用 db_run_query 读取 v_index_daily 的收盘序列",
                 "计算 MA5/MA20 均线与多头排列状态",
                 "给出趋势结论并标记 [CRITICAL] 数据窗口"]},
      {"title": "量价关系观察",
       "steps": ["用 kg_search 定位量价研究的数据表",
                 "对比近20日成交额的放量缩量节奏",
                 "总结量价背离信号"]}
    ]}
    """

  @supervisor-verdict-ignore
  Scenario: the supervisor ignores a dispensable failed step
    Given the system contains "supervisor"
    And the conversation contains "trading_amount"
    When the supervisor judges the failed step
    Then the reply is
    """
    诊断：量价步骤死于其聚合口径缺 trading_amount 列 — 结构性缺口，
    非致命；其工作可由趋势步与综合视角覆盖，恢复重跑收益有限。
    Plan/topology: unchanged
    Overall: no-action
    Action: ignore-step
    """

  @supervisor-verdict-noaction
  Scenario: the supervisor leaves a fatal failed step alone
    Given the system contains "supervisor"
    And the conversation contains "UndefinedTable"
    When the supervisor judges the failed step
    Then the reply is
    """
    诊断：量价步骤读取不存在的表 v_volume_daily — 输入口径错误，
    重试无法清障，忽略会留下缺口。
    Plan/topology: update-recommended — 收窄该步的 directive，先
    确认表存在再读取
    Overall: plan-update-recommended
    Action: no-action
    """

  @supervisor-verdict-cap-retry
  Scenario: the supervisor retries a step-cap death with recorded real work
    Given the system contains "supervisor"
    And the conversation contains "repair protocol"
    And the conversation contains "step limit"
    When the supervisor judges the failed step
    Then the reply is
    """
    诊断：该步骤死于其对话步数上限——错误文本记录了真实工作量
    (spent 8 step(s), 8 tool call(s))，非退化空转；步数预算上调后
    重跑一次可清障。
    Action: retry-step
    """

  @supervisor-verdict-retry
  Scenario: the supervisor's repair ask returns a retry verdict
    Given the system contains "supervisor"
    And the conversation contains "repair protocol"
    When the supervisor judges the failed step
    Then the reply is
    """
    诊断：该步骤死于一次瞬时读取超时，重试预算未动用，恢复该节点可清障。
    Action: retry-step
    """

  @supervisor-tail
  Scenario: any other supervisor ask observes without acting
    Given the system contains "supervisor"
    When the supervisor observes the run
    Then the reply is
    """
    观察：链路状态与计划一致，无需拓扑调整。
    Action: no-action
    """

  @step-volume-key-error
  Scenario: the ignore walk's volume step fails on a structural key error
    Given the conversation contains "[step_2]"
    And the conversation contains "忽略量价失败"
    And the system does not contain "supervisor"
    And tools are declared
    And the flake is armed once
    When the step's first attempt is served
    Then the reply is
    """
    KeyError: 'trading_amount' — 量价聚合缺列，结构性失败 (deliberate
    flake, scenario step-volume-key-error)
    """

  @step-volume-table-error
  Scenario: the no-action walk's volume step fails on a missing table
    Given the conversation contains "[step_2]"
    And the conversation contains "容忍量价失败"
    And the system does not contain "supervisor"
    And tools are declared
    And the flake is armed once
    When the step's first attempt is served
    Then the reply is
    """
    psycopg.errors.UndefinedTable: relation "v_volume_daily" does not
    exist (deliberate flake, scenario step-volume-table-error)
    """

  @step-volume-rate-limit
  Scenario: the rate-limit walks' volume step dies on a 1302 rate limit
    Given the conversation contains "[step_2]"
    And the conversation contains "限流重试"
    And the system does not contain "supervisor"
    And tools are declared
    And the flake is armed once
    When the step's first attempt is served
    Then the reply is
    """
    zhipu 1302: 速率限制 rate limit exceeded, please retry later
    (deliberate flake, scenario step-volume-rate-limit)
    """

  @step-volume-cap-burn
  Scenario: the cap walk's volume step burns its whole step budget
    Given the conversation contains "[step_2]"
    And the conversation contains "步数耗尽重试"
    And the conversation does not contain "conversation budget"
    And the system does not contain "supervisor"
    And tools are declared
    When the step decodes label codes instead of working its goal
    Then the reply calls "db_run_query" with
    """
    {"sql": "SELECT domain, code, label FROM static.label_dim WHERE domain = 'agent_context_kind' ORDER BY code"}
    """

  @step-volume-cap-resumed
  Scenario: the cap walk's volume step completes under the raised budget
    Given the conversation contains "[step_2]"
    And the conversation contains "步数耗尽重试"
    And the conversation contains "conversation budget: 24 steps"
    And the system does not contain "supervisor"
    When the step answers with its finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-volume-cap-resumed)
    近20日成交额先放量后缩量，量价配合中性偏弱，未见明显量价背离；
    本结论在步数预算上调后的重跑中给出。
    """

  @step-volume-flake
  Scenario: the volume step's first attempt dies on a transient timeout
    Given the conversation contains "[step_2]"
    And the conversation does not contain "限流重试"
    And the system does not contain "supervisor"
    And tools are declared
    And the flake is armed once
    When the step's first attempt is served
    Then the reply is
    """
    ReadTimeout: ReadTimeoutError — the read operation timed out while
    fetching daily bars (deliberate flake, scenario step-volume-flake)
    """

  @step-trend-kg
  Scenario: the trend step locates its tables through kg_search first
    Given the conversation contains "[step_1]"
    And the conversation does not contain "KG search"
    And the conversation does not contain "[Jev relevance gate"
    And the system does not contain "supervisor"
    And tools are declared
    When the step asks the knowledge graph for the 均线 tables
    Then the reply calls "kg_search" with
    """
    {"query": "均线 排列 多头排列", "detail": "full"}
    """

  @step-trend-db-gate
  Scenario: the trend step reads its bars after the gate's reject nudge
    Given the conversation contains "[step_1]"
    And the conversation contains "[Jev relevance gate"
    And the conversation does not contain "query_rows"
    And the system does not contain "supervisor"
    And tools are declared
    When the step reads the daily bars
    Then the reply calls "db_run_query" with
    """
    {"sql": "SELECT date, close FROM v_index_daily WHERE code='000300' ORDER BY date DESC LIMIT 260"}
    """

  @step-trend-db
  Scenario: the trend step reads its bars after an ungated kg render
    Given the conversation contains "[step_1]"
    And the conversation contains "KG search"
    And the conversation does not contain "query_rows"
    And the system does not contain "supervisor"
    And tools are declared
    When the step reads the daily bars
    Then the reply calls "db_run_query" with
    """
    {"sql": "SELECT date, close FROM v_index_daily WHERE code='000300' ORDER BY date DESC LIMIT 260"}
    """

  @step-trend-finding
  Scenario: the trend step settles its goal with a finding
    Given the conversation contains "[step_1]"
    And the conversation contains "query_rows"
    And the system does not contain "supervisor"
    When the step answers with its finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-trend-finding)
    MA5 上穿 MA20，短期均线呈多头排列；样本覆盖最近260个交易日。
    [CRITICAL] 数据窗口截至最近交易日，均线状态为短多。
    """

  @step-volume-kg
  Scenario: the volume step locates its tables through kg_search first
    Given the conversation contains "[step_2]"
    And the conversation does not contain "KG search"
    And the conversation does not contain "[Jev relevance gate"
    And the system does not contain "supervisor"
    And tools are declared
    When the step asks the knowledge graph for the 量价 tables
    Then the reply calls "kg_search" with
    """
    {"query": "放量 缩量 量价背离", "detail": "full"}
    """

  @step-volume-db-gate
  Scenario: the volume step reads its amounts after the gate's nudge
    Given the conversation contains "[step_2]"
    And the conversation contains "[Jev relevance gate"
    And the conversation does not contain "query_rows"
    And the system does not contain "supervisor"
    And tools are declared
    When the step reads the trading amounts
    Then the reply calls "db_run_query" with
    """
    {"sql": "SELECT date, trading_amount FROM v_index_daily WHERE code='000300' ORDER BY date DESC LIMIT 40"}
    """

  @step-volume-db
  Scenario: the volume step reads its amounts after an ungated render
    Given the conversation contains "[step_2]"
    And the conversation contains "KG search"
    And the conversation does not contain "query_rows"
    And the system does not contain "supervisor"
    And tools are declared
    When the step reads the trading amounts
    Then the reply calls "db_run_query" with
    """
    {"sql": "SELECT date, trading_amount FROM v_index_daily WHERE code='000300' ORDER BY date DESC LIMIT 40"}
    """

  @step-volume-finding
  Scenario: the volume step settles its goal with a finding
    Given the conversation contains "[step_2]"
    And the conversation contains "query_rows"
    And the system does not contain "supervisor"
    When the step answers with its finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-volume-finding)
    近20日成交额先放量后缩量，量价配合中性偏弱，未见明显量价背离。
    """

  @output-custom
  Scenario: the custom graph's OUTPUT node writes the handoff synthesis
    Given the conversation contains "output node"
    And the conversation contains "graph-output context row"
    And the conversation does not contain "final-result context row"
    When the custom output is asked for the synthesis
    Then the reply is
    """
    DUMMY CUSTOM SYNTHESIS (rule=output-custom)
    发现互证：均线多头排列（趋势步骤）与量能走弱（量价步骤）并存，
    综合视角为条件性偏多，量能能否再度放大是观察点。
    """

  @output-final
  Scenario: the result graph's OUTPUT node writes the final answer
    Given the conversation contains "output node"
    And the conversation contains "final-result context row"
    And the conversation does not contain "graph-output context row"
    When the result output is asked for the run's answer
    Then the reply is
    """
    DUMMY FINAL ANSWER (rule=output-final)
    综合结论：沪深300趋势中性偏多（均线多头排列），量能中性偏弱是主要约束；
    未来一个月 indicative 区间震荡略偏上行，置信度中等；主要风险为缩量反弹失败。
    """

  @review-return-estimate
  Scenario: the return_estimate seat answers through its return lens
    Given the conversation contains "return_estimate"
    And the system does not contain "output node"
    When the reviewer is asked for its lens
    Then the reply is
    """
    DUMMY RETURN REVIEW (rule=review-return-estimate)
    预期收益：趋势中性偏多，未来一个月的期望区间为窄幅震荡略偏上行，置信度中等。
    """

  @review-risk-analysis
  Scenario: the risk_analysis seat answers through its risk lens
    Given the conversation contains "risk_analysis"
    And the system does not contain "output node"
    When the reviewer is asked for its lens
    Then the reply is
    """
    DUMMY RISK REVIEW (rule=review-risk-analysis)
    主要风险：若成交额持续缩量，趋势结论的可靠性下降（概率×影响：中×中）。
    """

  @review-optimistic
  Scenario: the optimistic_review seat steelmans the bull case
    Given the conversation contains "optimistic_review"
    And the system does not contain "output node"
    When the reviewer is asked for its lens
    Then the reply is
    """
    DUMMY BULL REVIEW (rule=review-optimistic)
    最强多头情形：量能再度放大确认多头排列延续，指数站上短期均线组并走高。
    """

  @review-pessimistic
  Scenario: the pessimistic_review seat steelmans the bear case
    Given the conversation contains "pessimistic_review"
    And the system does not contain "output node"
    When the reviewer is asked for its lens
    Then the reply is
    """
    DUMMY BEAR REVIEW (rule=review-pessimistic)
    最强空头情形：缩量反弹失败，均线再度粘合后向下发散，指数回踩前期平台。
    """

  @step-finding
  Scenario: an unmarked tool-less turn answers with a generic finding
    Given no tools are declared
    When the conversation is answered without tool calls
    Then the reply is
    """
    DUMMY FINDING (rule=step-finding)
    通用结论：按现有发现作答。
    """

  @step-finding-tools
  Scenario: an unmarked tool-carrying turn answers with its finding
    Given tools are declared
    When the conversation is answered with its finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-finding-tools)
    数据读取完成，发现见结论字段。
    """
