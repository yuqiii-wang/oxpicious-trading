Feature: router_chain — the System One router's canned turns
  The dataset for the ROUTER walks (probe_dummy_langgraph_router.py):
  the 2026-11-13 design where the injected jev engine routes every LLM
  response's NEXT work — inference (continue) / spawn (a fresh
  scripting sub node takes over proposed sandbox script work) / join
  (a messy scripting node stops and hands back to its parent chain) /
  the failure probe (an AUTHORIZED investigator examines why a
  scripting child failed). Given/When = the predicates (all must
  hold); Then = the reply. File order is match order — this file is
  named 00_ so its scenarios load BEFORE 01_summary_chain's catch-alls.

  MATCH ORDER MATTERS HERE: the router-spawned children's
  conversations EMBED the parent's markers (the scripting handoff
  carries "Run goal: …[step_3]…", the probe handoff carries the
  failed goal), so the CHILD scenarios (keyed on the child-only
  markers "Scripting handoff" / "[scripting child]" / "[failing
  child]" / "Failure probe") are ordered FIRST — a parent turn never
  carries those markers (spawn arguments ride tool_calls, not message
  content), while a child turn may carry the parent's [step_N]. The
  parent scenarios come after, keyed on their [step_3..5] brackets.

  THREE walks share this dataset (each mounts everything; each walk's
  OWN goal marker picks its plan scenario — the routed/scripting step
  renders [step_3] in all three, so the parent scenarios key on
  [step_3] PLUS the walk's directive marker 证据整理 / 回交 / 失败调查;
  steps 1-2 reuse 01_summary_chain's trend/volume faces):

  * SPAWN — [step_3] proposes sandbox script writing; the router spawns;
    the scripting child (its first user message carries
    "Scripting handoff") writes → runs → hands the evidence back;
    the parent folds it. The parent's proposed calls NEVER execute
    (synthetic tool results).
  * JOIN — [step_4] spawns a scripting child (the model's spawn
    arguments carry role=scripting); the child's sandbox_run fails
    (Traceback); its edit proposal gets the join verdict → the nudge;
    the child hands back (HANDBACK) instead of patching.
  * PROBE — [step_5] spawns a scripting child whose first turn dies on
    the armed flake; the parent spawns an investigator (its goal
    carries "Failure probe") which returns the root-cause findings.

  FOUR walks share this dataset (each mounts everything; each walk's
  OWN goal marker picks its plan scenario — the routed/scripting step
  renders [step_3] in all four, so the parent scenarios key on
  [step_3] PLUS the walk's directive marker 证据整理 / 回交 /
  失败调查 / 继承上下文续算; steps 1-2 reuse 01_summary_chain's
  trend/volume faces):

  * SPAWN — [step_3] proposes sandbox script writing; the router spawns;
    the scripting child (its first user message carries
    "Scripting handoff") writes → runs → hands the evidence back;
    the parent folds it. The parent's proposed calls NEVER execute
    (synthetic tool results).
  * FORK — [step_3] proposes sandbox script writing; the spawn read
    says NO (a continuation) and the FORK read says YES; the fork
    child INHERITS the parent transcript (its goal user message
    carries the child-only marker "Fork continuation" — the E2E
    inheritance proof: those scenarios can only match because the
    parent's conversation rode in) and writes → runs → hands the
    evidence back; the parent folds the "[Fork sub node took over]"
    injection. Same intercepted-parent shape as SPAWN, and the child
    row settles with child_kind 1 (agent_child_kind fork).
  * JOIN — [step_4] spawns a scripting child (the model's spawn
    arguments carry role=scripting); the child's sandbox_run fails
    (Traceback); its edit proposal gets the join verdict → the nudge;
    the child hands back (HANDBACK) instead of patching.
  * PROBE — [step_5] spawns a scripting child whose first turn dies on
    the armed flake; the parent spawns an investigator (its goal
    carries "Failure probe") which returns the root-cause findings.

  @route-plan-spawn
  Scenario: the spawn walk's planner proposes the routed three-step plan
    Given the system contains "planning node"
    And the conversation contains "路由给子节点"
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
      {"title": "脚本计算与证据整理",
       "steps": ["把指标计算交给脚本完成",
                 "汇总脚本输出的量化证据"]}
    ]}
    """

  @route-plan-join
  Scenario: the join walk's planner proposes the handback plan
    Given the system contains "planning node"
    And the conversation contains "回交父节点"
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
      {"title": "脚本子节点计算与回交",
       "steps": ["spawn 一个 scripting 子节点执行脚本计算",
                 "汇总子节点回交的量化证据"]}
    ]}
    """

  @route-plan-probe
  Scenario: the probe walk's planner proposes the investigate plan
    Given the system contains "planning node"
    And the conversation contains "调查失败原因"
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
      {"title": "脚本子节点计算与失败调查",
       "steps": ["spawn 一个 scripting 子节点执行脚本计算",
                 "子节点失败时派调查员查明原因"]}
    ]}
    """

  @route-plan-fork
  Scenario: the fork walk's planner proposes the inherited-continuation plan
    Given the system contains "planning node"
    And the conversation contains "继承上下文续算"
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
      {"title": "脚本子节点继承上下文续算",
       "steps": ["fork 一个子节点继承本对话上下文完成脚本续算",
                 "汇总子节点回交的量化证据"]}
    ]}
    """

  @probe-investigator
  Scenario: the authorized investigator reports the root cause
    Given the conversation contains "Failure probe"
    And the conversation does not contain "[step_3]"
    And the system does not contain "supervisor"
    And tools are declared
    When the investigator answers with its findings
    Then the reply is
    """
    PROBE FINDING (rule=probe-investigator)
    根因：子节点的沙盒会话在写入脚本后丢失（会话重建未回填路径）；
    建议：父节点重发同 goal 并在 spawn 参数携带 reuse_session=true；
    值得重试：是 — 读取阶段无错。
    """

  @script-child-write
  Scenario: the scripting child writes the handed-off script first
    Given the conversation contains "Scripting handoff"
    And the conversation does not contain "bytes"
    And the system does not contain "supervisor"
    And tools are declared
    When the child writes the script into the shared sandbox
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/ma_study.py",
     "content": "import pandas as pd\n# MA5/MA20 alignment study\nprint('alignment rows')"}
    """

  @script-child-run
  Scenario: the scripting child runs the written script
    Given the conversation contains "Scripting handoff"
    And the conversation contains "bytes"
    And the conversation does not contain "exit_code"
    And the system does not contain "supervisor"
    And tools are declared
    When the child runs the script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/ma_study.py"}
    """

  @script-child-evidence
  Scenario: the scripting child hands the evidence back
    Given the conversation contains "Scripting handoff"
    And the conversation contains "exit_code"
    And the system does not contain "supervisor"
    When the child answers with its evidence handback
    Then the reply is
    """
    EVIDENCE (rule=script-child-evidence)
    MA5 4128.6 > MA20 4101.2 — 多头排列成立；MA20-MA60 间距占比 -3.9%，
    五日内收窄 0.4pct；关键证据为最近 5 个交易日的收盘与均线差值表。
    """

  @fork-child-write
  Scenario: the fork child (inheriting the parent transcript) writes the script first
    Given the conversation contains "Fork continuation"
    And the conversation does not contain "bytes"
    And the system does not contain "supervisor"
    And tools are declared
    When the fork child writes the continuation script
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/ma_study.py",
     "content": "import pandas as pd\n# MA5/MA20 alignment study\nprint('alignment rows')"}
    """

  @fork-child-run
  Scenario: the fork child runs the written script
    Given the conversation contains "Fork continuation"
    And the conversation contains "bytes"
    And the conversation does not contain "exit_code"
    And the system does not contain "supervisor"
    And tools are declared
    When the fork child runs the script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/ma_study.py"}
    """

  @fork-child-evidence
  Scenario: the fork child hands the evidence back
    Given the conversation contains "Fork continuation"
    And the conversation contains "exit_code"
    And the system does not contain "supervisor"
    When the fork child answers with its evidence handback
    Then the reply is
    """
    EVIDENCE (rule=fork-child-evidence)
    继承上下文续算完成：MA5 4128.6 > MA20 4101.2 — 多头排列成立；
    MA20-MA60 间距占比 -3.9%，五日内收窄 0.4pct；量化表已并入本回交。
    """

  @script-child-run-fail
  Scenario: the scripting child runs and hits a traceback
    Given the conversation contains "[scripting child]"
    And the conversation does not contain "Traceback"
    And the conversation does not contain "System One join"
    And the system does not contain "supervisor"
    And tools are declared
    When the child runs its script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/ma_child.py"}
    """

  @script-child-edit
  Scenario: the messy scripting child proposes one more patch
    Given the conversation contains "[scripting child]"
    And the conversation contains "Traceback"
    And the conversation does not contain "System One join"
    And the system does not contain "supervisor"
    And tools are declared
    When the child proposes another edit
    Then the reply calls "sandbox_edit_file" with
    """
    {"path": "scripts/ma_child.py", "find": "group by r", "replace": "group by code, date"}
    """

  @script-child-handback
  Scenario: the nudged scripting child writes its handback
    Given the conversation contains "[scripting child]"
    And the conversation contains "System One join"
    And the system does not contain "supervisor"
    When the child hands back instead of patching
    Then the reply is
    """
    HANDBACK (rule=script-child-handback)
    已确立：脚本读取 260 行日线成功，聚合阶段死于 group by 列名歧义；
    未解决：ma_child.py 的分组键仍错；脚本位于共享沙盒 scripts/ma_child.py；
    建议：父节点按 (code, date) 重发分组键后重跑。
    """

  @script-child-flake
  Scenario: the failing child's first turn dies on the armed flake
    Given the conversation contains "[failing child]"
    And tools are declared
    And the flake is armed once
    When the child's first turn is served
    Then the reply is
    """
    RuntimeError: sandbox session lost mid-run (deliberate flake,
    scenario script-child-flake)
    """

  @step-script-proposal
  Scenario: the routed step proposes sandbox script writing
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本计算与证据整理"
    And the conversation does not contain "Scripting sub node took over"
    And the system does not contain "supervisor"
    And tools are declared
    When the step proposes its indicator script
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/ma_study.py",
     "content": "import pandas as pd\n# MA5/MA20 alignment study\nprint('alignment rows')"}
    """

  @step-script-folded
  Scenario: the routed step folds the scripting child's evidence
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本计算与证据整理"
    And the conversation contains "Scripting sub node took over"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-script-folded)
    脚本子节点已交付：MA5/MA20 多头排列，MA20-MA60 间距占比 -3.9% 且五日内收窄，
    证据已并入本步结论。 [CRITICAL] 均线状态为短多。
    """

  @step-join-spawn
  Scenario: the join walk's step spawns a scripting child
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点计算与回交"
    And the conversation does not contain "HANDBACK"
    And the system does not contain "supervisor"
    And tools are declared
    When the step spawns its scripting child
    Then the reply calls "spawn_subagent" with
    """
    {"goal": "[scripting child] 计算均线排列量化表并输出",
     "background": false, "role": "scripting"}
    """

  @step-join-fold
  Scenario: the parent folds the scripting child's handback
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点计算与回交"
    And the conversation contains "HANDBACK"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-join-fold)
    脚本子节点按路由回交：读取成功、聚合失败（group by 歧义）；
    本步按其建议改用 (code, date) 分组键重跑后得到均线排列表，
    结论与趋势步互证：短多。
    """

  @step-probe-spawn
  Scenario: the probe walk's step spawns a scripting child
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点计算与失败调查"
    And the conversation does not contain "Failure probe"
    And the system does not contain "supervisor"
    And tools are declared
    When the step spawns its scripting child
    Then the reply calls "spawn_subagent" with
    """
    {"goal": "[failing child] 计算均线排列脚本并输出量化表",
     "background": false, "role": "scripting"}
    """

  @step-probe-fold
  Scenario: the parent folds the investigator's findings
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点计算与失败调查"
    And the conversation contains "Failure probe"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-probe-fold)
    调查员定位根因：沙盒会话丢失（写入后未回填路径）；
    本步按建议携带 reuse_session 重发脚本子节点，量化表已取得，
    结论：均线短多，与趋势步互证。
    """

  @step-fork-proposal
  Scenario: the fork walk's routed step proposes its continuation script
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点继承上下文续算"
    And the conversation does not contain "Fork sub node took over"
    And the system does not contain "supervisor"
    And tools are declared
    When the step proposes its indicator script
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/ma_study.py",
     "content": "import pandas as pd\n# MA5/MA20 alignment study\nprint('alignment rows')"}
    """

  @step-fork-fold
  Scenario: the fork walk's routed step folds the fork child's evidence
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点继承上下文续算"
    And the conversation contains "Fork sub node took over"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-fork-fold)
    继承上下文的子节点已回交：MA5/MA20 多头排列，间距占比 -3.9% 且收窄，
    证据已并入本步结论。 [CRITICAL] 均线状态为短多。
    """
