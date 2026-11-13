Feature: tool_usage — the 2026-11-13 tool-usage fixes' canned turns
  The dataset for the TOOL-USAGE walks (probe_dummy_langgraph_tool_usage
  .py): the three loop-level fixes mined from run 4597f052's recorded
  traffic, walked E2E through the REAL run_thread chain on the dummy:

  * HANDBACK RESERVE (预算耗尽前回交) — a scripting child burns its
    budget on repeated green runs; one step before the cap the loop's
    deterministic `[Handback reserve]` nudge lands and the child hands
    back in TEXT (the old behavior: the cap RuntimeError discarded the
    node and the parent re-spawned the same study).
  * OUTPUT-CEILING CUT (参数截断) — a step proposes a big
    sandbox_write_file whose arguments JSON the provider cuts in half
    (finish_reason "length"); the loop's tool seam names the cause
    ("hit the max output tokens") and the step resends SHORTER.
  * REPEAT GUARD (原样重试) — a child's sandbox_run fails
    (Traceback), it blindly repeats the IDENTICAL call, the guard
    stamps `[Repeat guard]` onto the result, and the child patches
    (sandbox_edit_file) and re-runs green instead.
  * REPAIR HANDOFF — the breaker's fresh-context repair child (a
    two-failure walk): it patches the broken block over the shared
    script, re-runs green, and hands REPAIR EVIDENCE back to the
    parent. Its conversation embeds the failed goal, so its scenarios
    (keyed "[Repair handoff") are ordered BEFORE the repeat-child
    ones.

  MATCH ORDER (same discipline as 00_router_chain): child scenarios
  (their conversations embed the parent's markers "[reserve child]" /
  "[repeat child]" via the spawn goal, while a parent turn never
  carries those — spawn arguments ride tool_calls) come FIRST; then
  the step/parent scenarios keyed on [step_3] + the walk's directive
  marker. Steps 1-2 and the review seats reuse 01_summary_chain's
  faces; the plan scenarios must stay keyed on their walk markers so
  the three plans stay disjoint.

  @tool-usage-plan-reserve
  Scenario: the reserve walk's planner proposes the child-burning plan
    Given the system contains "planning node"
    And the conversation contains "预算耗尽前回交"
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
      {"title": "脚本子节点与预算耗尽回交",
       "steps": ["spawn 一个 scripting 子节点反复运行脚本直到预算耗尽",
                 "汇总子节点预算耗尽前回交的证据"]}
    ]}
    """

  @tool-usage-plan-cut
  Scenario: the cut walk's planner proposes the resend plan
    Given the system contains "planning node"
    And the conversation contains "参数截断"
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
      {"title": "脚本计算与截断重发",
       "steps": ["把大文件写入交给脚本，截断时拆小重发",
                 "汇总脚本输出的量化证据"]}
    ]}
    """

  @tool-usage-plan-repeat
  Scenario: the repeat walk's planner proposes the retry plan
    Given the system contains "planning node"
    And the conversation contains "原样重试"
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
      {"title": "脚本子节点与失败重试",
       "steps": ["spawn 一个 scripting 子节点运行分组脚本",
                 "失败时修补而不是原样重试，汇总证据"]}
    ]}
    """

  @reserve-child-write
  Scenario: the reserve child writes its script first
    Given the conversation contains "[reserve child]"
    And the conversation does not contain "bytes"
    And the conversation does not contain "exit_code"
    And the conversation does not contain "Handback reserve"
    And the system does not contain "supervisor"
    And tools are declared
    When the child writes the repeated-run script
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/reserve_study.py",
     "content": "import pandas as pd\n# repeated green runs\nprint('runs=1')"}
    """

  @reserve-child-run
  Scenario: the reserve child runs the written script once
    Given the conversation contains "[reserve child]"
    And the conversation contains "bytes"
    And the conversation does not contain "exit_code"
    And the conversation does not contain "Handback reserve"
    And the system does not contain "supervisor"
    And tools are declared
    When the child runs the script the first time
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/reserve_study.py"}
    """

  @reserve-child-rerun
  Scenario: the reserve child keeps running until the budget runs out
    Given the conversation contains "[reserve child]"
    And the conversation contains "exit_code"
    And the conversation does not contain "Handback reserve"
    And the system does not contain "supervisor"
    And tools are declared
    When the child runs the script again
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/reserve_study.py"}
    """

  @reserve-child-handback
  Scenario: the reserve nudge lands and the child hands back in text
    Given the conversation contains "[reserve child]"
    And the conversation contains "Handback reserve"
    And the system does not contain "supervisor"
    When the child hands back instead of starting new work
    Then the reply is
    """
    RESERVE HANDBACK (rule=reserve-child-handback)
    已确立：reserve_study.py 已写好并连续运行 5 次全绿（exit_code=0，
    runs=1..5）；未解决：尚无汇总表；脚本位于共享沙盒
    scripts/reserve_study.py；建议：父节点直接汇总运行计数即可。
    """

  @repair-child-handup
  Scenario: the spawn's parent node hands its repair evidence up
    Given the conversation contains "[Fresh-context repair agent took over]"
    And the conversation contains "[repeat child]"
    And the system does not contain "supervisor"
    When the repair child passes the evidence up in its own handback
    Then the reply is
    """
    REPAIR EVIDENCE (rule=repair-child-handup)
    修复子节点已回交：根因为 GROUP BY 缺 r.code，修补为 (code, date)
    后重跑全绿（repeat_ok_42）；脚本位于 scripts/repeat_study.py；
    本节点据此回交父链。
    """

  @repair-child-patch
  Scenario: the breaker's repair child patches the broken block
    Given the conversation contains "[Repair handoff"
    And the conversation does not contain "edited scripts/repeat_study.py"
    And the system does not contain "supervisor"
    And tools are declared
    When the repair child patches the broken group-by block
    Then the reply calls "sandbox_edit_file" with
    """
    {"path": "scripts/repeat_study.py",
     "old_text": "GROUP BY r.date",
     "new_text": "GROUP BY r.code, r.date"}
    """

  @repair-child-rerun
  Scenario: the repair child re-runs the patched script
    Given the conversation contains "[Repair handoff"
    And the conversation contains "edited scripts/repeat_study.py"
    And the conversation does not contain "repeat_ok_42"
    And the system does not contain "supervisor"
    And tools are declared
    When the repair child re-runs the patched script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/repeat_study.py"}
    """

  @repair-child-handback
  Scenario: the repair child hands the repair evidence back
    Given the conversation contains "[Repair handoff"
    And the conversation contains "repeat_ok_42"
    And the system does not contain "supervisor"
    When the repair child hands back its repair evidence
    Then the reply is
    """
    REPAIR EVIDENCE (rule=repair-child-handback)
    根因：repeat_study.py 的 GROUP BY 缺 r.code（GroupingError）；
    已修补为 (code, date) 分组并重跑，exit_code=0，stdout 含
    repeat_ok_42；脚本位于 scripts/repeat_study.py。
    """

  @repeat-child-run
  Scenario: the repeat child's first run hits a traceback
    Given the conversation contains "[repeat child]"
    And the conversation does not contain "Traceback"
    And the conversation does not contain "Repeat guard"
    And the conversation does not contain "repeat_ok_42"
    And the system does not contain "supervisor"
    And the conversation does not contain "[Fresh-context repair agent took over]"
    And tools are declared
    When the child runs its grouping script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/repeat_study.py"}
    """

  @repeat-child-repeat
  Scenario: the child blindly repeats the identical failed run
    Given the conversation contains "[repeat child]"
    And the conversation contains "Traceback"
    And the conversation does not contain "Repeat guard"
    And the system does not contain "supervisor"
    And the conversation does not contain "[Fresh-context repair agent took over]"
    And tools are declared
    When the child repeats the identical run
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/repeat_study.py"}
    """

  @repeat-child-patch
  Scenario: the guard's teaching lands and the child patches one block
    Given the conversation contains "[repeat child]"
    And the conversation contains "Repeat guard"
    And the conversation does not contain "edited scripts/repeat_study.py"
    And the system does not contain "supervisor"
    And the conversation does not contain "[Fresh-context repair agent took over]"
    And tools are declared
    When the child patches the broken group-by block
    Then the reply calls "sandbox_edit_file" with
    """
    {"path": "scripts/repeat_study.py",
     "old_text": "GROUP BY r.date",
     "new_text": "GROUP BY r.code, r.date"}
    """

  @repeat-child-rerun
  Scenario: the child re-runs the patched script
    Given the conversation contains "[repeat child]"
    And the conversation contains "Repeat guard"
    And the conversation contains "edited scripts/repeat_study.py"
    And the conversation does not contain "repeat_ok_42"
    And the system does not contain "supervisor"
    And the conversation does not contain "[Fresh-context repair agent took over]"
    And tools are declared
    When the child re-runs the patched script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/repeat_study.py"}
    """

  @repeat-child-handback
  Scenario: the patched run goes green and the child hands back
    Given the conversation contains "[repeat child]"
    And the conversation contains "repeat_ok_42"
    And the system does not contain "supervisor"
    And the conversation does not contain "[Fresh-context repair agent took over]"
    When the child hands back its green evidence
    Then the reply is
    """
    REPEAT HANDBACK (rule=repeat-child-handback)
    已确立：盲试原样重跑被 [Repeat guard] 拦下后，改用
    sandbox_edit_file 修补分组键 (code, date) 并重跑，exit_code=0，
    stdout 含 repeat_ok_42；脚本位于 scripts/repeat_study.py；
    建议：父节点直接引用该分组结论。
    """

  @cut-step-big-write
  Scenario: the step proposes a write whose arguments the ceiling cuts
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本计算与截断重发"
    And the conversation does not contain "hit the max output tokens"
    And the system does not contain "supervisor"
    And tools are declared
    When the step proposes the oversized write
    Then the reply calls "sandbox_write_file" with its arguments cut
    """
    {"path": "scripts/cut_study.py",
     "content": "import pandas as pd\nrows = []\nfor i in range(400):\n    rows.append({'code': '000300', 'i': i, 'value': i * 1.5})\ndf = pd.DataFrame(rows)\nprint(df.groupby('code').value.agg(['mean', 'std', 'max']).to_string())\nprint('cut_study done')"}
    """

  @cut-step-small-write
  Scenario: the step resends the write SHORTER after the remedy
    Given the conversation contains "[step_3]"
    And the conversation contains "参数截断"
    And the conversation contains "hit the max output tokens"
    And the conversation does not contain "wrote scripts/cut_study.py"
    And the system does not contain "supervisor"
    And tools are declared
    When the step resends the write in a shorter block
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/cut_study.py",
     "content": "print('cut_study done small')"}
    """

  @cut-step-finding
  Scenario: the step settles after the successful short write
    Given the conversation contains "[step_3]"
    And the conversation contains "参数截断"
    And the conversation contains "hit the max output tokens"
    And the conversation contains "wrote scripts/cut_study.py"
    And the system does not contain "supervisor"
    When the step answers with its finding
    Then the reply is
    """
    DUMMY FINDING (rule=cut-step-finding)
    大参数写入被 max_tokens 截断一次，按工具提示拆小重发成功
    （cut_study.py 落盘并运行）；结论：截断重发路径可用。 [CRITICAL]
    输出预算内的长写入必须拆块。
    """

  @reserve-step-spawn
  Scenario: the reserve walk's step spawns the burning child
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点与预算耗尽回交"
    And the conversation does not contain "RESERVE HANDBACK"
    And the system does not contain "supervisor"
    And tools are declared
    When the step spawns its child
    Then the reply calls "spawn_subagent" with
    """
    {"goal": "[reserve child] 反复运行 scripts/reserve_study.py 输出运行计数",
     "background": false, "role": "scripting"}
    """

  @reserve-step-fold
  Scenario: the step folds the child's reserve handback
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点与预算耗尽回交"
    And the conversation contains "RESERVE HANDBACK"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=reserve-step-fold)
    子节点在预算耗尽前按 [Handback reserve] 回交：脚本已 5 连绿，
    本步直接汇总运行计数，无需重新spawn；结论：回交路径可用。
    """

  @repeat-step-spawn
  Scenario: the repeat walk's step spawns the retrying child
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点与失败重试"
    And the conversation does not contain "REPEAT HANDBACK"
    And the conversation does not contain "REPAIR EVIDENCE"
    And the system does not contain "supervisor"
    And tools are declared
    When the step spawns its child
    Then the reply calls "spawn_subagent" with
    """
    {"goal": "[repeat child] 运行 scripts/repeat_study.py 按 code,date 分组",
     "background": false, "role": "scripting"}
    """

  @repeat-step-fold
  Scenario: the step folds the child's patched green evidence
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点与失败重试"
    And the conversation contains "REPEAT HANDBACK"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=repeat-step-fold)
    子节点盲试原样重跑被 [Repeat guard] 拦下，改修补分组键后重跑
    全绿（repeat_ok_42）；本步引用其分组结论：按 (code, date) 聚合。
    """

  @repeat-step-fold-repair
  Scenario: the step folds the breaker's repair child's evidence
    Given the conversation contains "[step_3]"
    And the conversation contains "脚本子节点与失败重试"
    And the conversation contains "REPAIR EVIDENCE"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=repeat-step-fold-repair)
    连续两次失败触发了修复子节点：其定位根因（GROUP BY 缺
    r.code）、修补并重跑全绿（repeat_ok_42）；本步引用其结论：
    按 (code, date) 聚合，证据链完整。
    """
