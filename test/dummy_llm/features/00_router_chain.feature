Feature: router_chain — the System One router's canned turns
  The dataset for the ROUTER walks (probe_dummy_langgraph_router.py):
  the 2026-11-13 design where the injected jev engine routes every LLM
  response's NEXT work — inference (continue) / spawn (a fresh
  scripting sub node takes over proposed sandbox script work) / join
  (a messy scripting node stops and hands back to its parent chain) /
  the failure probe (an AUTHORIZED investigator examines why a
  scripting child failed) / the drift fork (a context drifted off the
  node's purpose forks from a PRE-DRIFT checkpoint; the tail after the
  cut is abandoned as a kind-2 stub row). Given/When = the predicates
  (all must hold); Then = the reply. File order is match order — this
  file is named 00_ so its scenarios load BEFORE 01_summary_chain's
  catch-alls.

  MATCH ORDER MATTERS HERE: the router-spawned children's
  conversations EMBED the parent's markers (the scripting handoff
  carries "Run goal: …[step_3]…", the probe handoff carries the
  failed goal), so the CHILD scenarios (keyed on the child-only
  markers "Scripting handoff" / "[scripting child]" / "[failing
  child]" / "Failure probe" / "Fork continuation" / "[fork sub
  child]" / "[bg child]")
  are ordered FIRST — a parent turn never carries those markers
  (spawn arguments ride tool_calls, not message content), while a
  child turn may carry the parent's [step_N]. The parent scenarios
  come after, keyed on their [step_3..5] brackets PLUS the walk's
  directive marker (证据整理 / 回交 / 失败调查 / 继承上下文续算 /
  再派子节点 / 漂移回锚 / 后台收集).

  SEVEN walks share this dataset (each mounts everything; each walk's
  OWN goal marker picks its plan scenario — the routed/scripting step
  renders [step_3] in all of them; steps 1-2 reuse 01_summary_chain's
  trend/volume faces):

  * SPAWN — [step_3] proposes sandbox script writing; the router spawns;
    the scripting child (its first user message carries
    "Scripting handoff") writes → runs → hands the evidence back;
    the parent folds it. The parent's proposed calls NEVER execute
    (synthetic tool results).
  * FORK — [step_3] proposes sandbox script writing; the spawn read
    says NO and the DRIFT-FORK read says YES (fork-from-now: no
    fork-point read candidates carry content); the fork child
    INHERITS the parent transcript (its goal user message carries
    the child-only marker "Fork continuation" — the E2E inheritance
    proof) and writes → runs → hands the evidence back; the parent
    folds the "[Fork sub node took over]" injection. The child row
    settles with child_kind 1 (agent_child_kind fork).
  * FORK-SUB — the fork walk's TWO-GENERATION shape: the fork child
    (inheriting the transcript, its conversation carries "Fork
    continuation" AND the walk's 再派子节点 marker) spawns its OWN
    sub node; the grandchild (fresh context, depth 2 = at the spawn
    cap — it never routes and carries no spawn tool) writes → runs →
    hands the evidence back; the fork child folds it into ITS handback;
    the parent folds the "[Fork sub node took over]" injection. The
    plan tree holds TWO child generations — the fork child (child_kind
    1) under the routed step and the spawn grandchild (child_kind 0)
    under the fork child. MATCHING NOTE: the spawn ARGUMENTS ride
    tool_calls, not message content — the fork child's pre/post-spawn
    turns discriminate on the grandchild's EVIDENCE tool result (the
    post-spawn turn carries "EVIDENCE (rule=forksub-child-evidence)",
    the pre-spawn turn does not), never on the "[fork sub child]"
    args text.
  * FORK-DRIFT — the drift walk (漂移回锚): the routed step opens ON
    PURPOSE (its kg/db turns carry response heads via the SAYS Then
    form — content-carrying tool calls, the fork-point checkpoints),
    then DRIFTS (the rewrite turn names the side quest); the drift
    read affirms and the FORK-POINT read picks the earliest
    checkpoint; the child inherits the PREFIX, the abandoned tail
    materializes as the kind-2 stub row, and the parent folds the
    "pre-drift context inherited" injection.
  * JOIN — [step_4] spawns a scripting child (the model's spawn
    arguments carry role=scripting); the child's sandbox_run fails
    (Traceback); its edit proposal gets the join verdict → the nudge;
    the child hands back (HANDBACK) instead of patching.
  * PROBE — [step_5] spawns a scripting child whose first turn dies on
    the armed flake; the parent spawns an investigator (its goal
    carries "Failure probe") which returns the root-cause findings.

  SIXTH walk — BACKGROUND (后台收集): [step_3] spawns a DETACHED child
  (background: true, its goal carries the child-only marker "[bg
  child]") and tries to answer; the loop's ANSWER GATE holds the
  answer until collect_subagents gathers the parked result — the
  premature-answer face matches ONLY on the queued tool text, the
  nudge face on the gate's "uncollected" text, and the fold face on
  the gathered result's "tool call(s))" head (a plain "finished ("
  needle is UNUSABLE — the standing step system prompt itself says
  "marks that goal finished (the step completed)" in every step
  conversation).
  LEAK DISCIPLINE (learned the hard way): the background spawn's
  queued tool text ECHOES the goal into the parent's conversation
  ("… spawned in the background (goal: [bg child] …)") — the child
  marker is NOT parent-exclusive here — so every detached-child face
  carries `does not contain "spawned in the background"` (the queued
  text is append-only parent-only state) as the child/parent
  discriminator.

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

  @route-plan-fork-sub
  Scenario: the fork-sub walk's planner proposes the two-generation plan
    Given the system contains "planning node"
    And the conversation contains "再派子节点"
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
      {"title": "脚本子节点继承续算再派子节点",
       "steps": ["fork 一个子节点继承本对话上下文完成脚本续算",
                 "子节点再派子节点完成细分量化计算",
                 "汇总两层子节点回交的量化证据"]}
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

  @route-plan-fork-drift
  Scenario: the drift walk's planner proposes the re-anchoring plan
    Given the system contains "planning node"
    And the conversation contains "漂移回锚"
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
      {"title": "脚本子节点漂移回锚续算",
       "steps": ["把指标脚本计算交给脚本完成",
                 "漂移时由子节点继承上下文回锚续算",
                 "汇总子节点回交的量化证据"]}
    ]}
    """

  @probe-investigator
  Scenario: the authorized investigator reports the root cause
    Given the conversation contains "Failure probe"
    And the conversation does not contain "[step_"
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
    [CRITICAL] 均线状态为短多。
    """

  @forksub-child-write
  Scenario: the fork child's own sub node writes the delegated script first
    Given the conversation contains "[fork sub child]"
    And the conversation does not contain "Fork continuation"
    And the conversation does not contain "bytes"
    And the system does not contain "supervisor"
    And tools are declared
    When the sub node writes the delegated script
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/ma_sub.py",
     "content": "import pandas as pd\n# MA bands sub study\nprint('sub rows')"}
    """

  @forksub-child-run
  Scenario: the fork child's own sub node runs the delegated script
    Given the conversation contains "[fork sub child]"
    And the conversation does not contain "Fork continuation"
    And the conversation contains "bytes"
    And the conversation does not contain "exit_code"
    And the system does not contain "supervisor"
    And tools are declared
    When the sub node runs the script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/ma_sub.py"}
    """

  @forksub-child-evidence
  Scenario: the fork child's own sub node hands the evidence back
    Given the conversation contains "[fork sub child]"
    And the conversation does not contain "Fork continuation"
    And the conversation contains "exit_code"
    And the system does not contain "supervisor"
    When the sub node answers with its evidence handback
    Then the reply is
    """
    EVIDENCE (rule=forksub-child-evidence)
    细分量化完成：MA10/MA60 剪刀差 2.1%，近 20 日逐周收窄；
    细分量化表已写入共享沙盒 scripts/ma_sub.py。
    [CRITICAL] 再派子节点路径可用。
    """

  @fork-child-subspawn
  Scenario: the fork child spawns its own sub node for the sub computation
    Given the conversation contains "Fork continuation"
    And the conversation contains "再派子节点"
    And the conversation does not contain "EVIDENCE (rule=forksub-child-evidence)"
    And the system does not contain "supervisor"
    And tools are declared
    When the fork child spawns its own sub node
    Then the reply calls "spawn_subagent" with
    """
    {"goal": "[fork sub child] 计算均线细分量化表并输出",
     "background": false}
    """

  @fork-child-subfold
  Scenario: the fork child folds the sub node's evidence into its handback
    Given the conversation contains "Fork continuation"
    And the conversation contains "再派子节点"
    And the conversation contains "EVIDENCE (rule=forksub-child-evidence)"
    And the system does not contain "supervisor"
    When the fork child hands the folded evidence back
    Then the reply is
    """
    EVIDENCE (rule=fork-child-subfold)
    继承续算完成：本层均线多头排列（MA5 4128.6 > MA20 4101.2，间距 -3.9%）；
    再派子节点已交付细分量化（MA10/MA60 剪刀差 2.1% 收窄），两层证据并入本回交。
    [CRITICAL] 两层子节点路径均可用。
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
    [CRITICAL] 继承回锚路径可用。
    """

  @drift-step-kg
  Scenario: the drift walk's routed step opens on purpose (a checkpoint)
    Given the conversation contains "[step_3]"
    And the conversation contains "漂移回锚"
    And the conversation does not contain "KG search"
    And the conversation does not contain "[Jev relevance gate"
    And the conversation does not contain "query_rows"
    And the conversation does not contain "Fork sub node took over"
    And the system does not contain "supervisor"
    And tools are declared
    When the step asks the knowledge graph for the 均线 tables
    Then the reply says "表结构已确认，均线口径对齐，可以开算" and calls "kg_search" with
    """
    {"query": "均线 排列 多头排列", "detail": "full"}
    """

  @drift-step-db
  Scenario: the drift walk's routed step reads its bars (the second checkpoint)
    Given the conversation contains "[step_3]"
    And the conversation contains "漂移回锚"
    And the conversation contains "KG search"
    And the conversation does not contain "query_rows"
    And the conversation does not contain "Fork sub node took over"
    And the system does not contain "supervisor"
    And tools are declared
    When the step reads the daily bars
    Then the reply says "排列表已出；顺手看看创业板成交额节奏" and calls "db_run_query" with
    """
    {"sql": "SELECT date, close FROM v_index_daily WHERE code='000300' ORDER BY date DESC LIMIT 260"}
    """

  @drift-step-drifted
  Scenario: the drifted turn proposes the off-purpose rewrite
    Given the conversation contains "[step_3]"
    And the conversation contains "漂移回锚"
    And the conversation contains "query_rows"
    And the conversation does not contain "Fork sub node took over"
    And the system does not contain "supervisor"
    And tools are declared
    When the drifted step proposes the side-quest rewrite
    Then the reply says "把已完成的聚合换口径重算，再看看创业板资金流支线" and calls "sandbox_write_file" with
    """
    {"path": "scripts/ma_study.py",
     "content": "import pandas as pd\n# MA5/MA20 alignment study\nprint('alignment rows')"}
    """

  @drift-step-fold
  Scenario: the parent folds the drift fork's re-anchored evidence
    Given the conversation contains "[step_3]"
    And the conversation contains "漂移回锚"
    And the conversation contains "Fork sub node took over"
    And the conversation contains "pre-drift context inherited"
    And the system does not contain "supervisor"
    When the step answers with its re-anchored finding
    Then the reply is
    """
    DUMMY FINDING (rule=drift-step-fold)
    漂移被 jev 拦下：子节点从漂移前检查点继承上下文回锚（支线尾部已
    丢弃），完成 MA5/MA20 排列计算：多头排列成立，间距占比 -3.9% 且
    五日内收窄。 [CRITICAL] 漂移回锚路径可用。
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

  @step-forksub-proposal
  Scenario: the fork-sub walk's routed step proposes its continuation script
    Given the conversation contains "[step_3]"
    And the conversation contains "再派子节点"
    And the conversation does not contain "Fork sub node took over"
    And the system does not contain "supervisor"
    And tools are declared
    When the step proposes its indicator script
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/ma_study.py",
     "content": "import pandas as pd\n# MA5/MA20 alignment study\nprint('alignment rows')"}
    """

  @step-forksub-fold
  Scenario: the fork-sub walk's routed step folds the two-generation evidence
    Given the conversation contains "[step_3]"
    And the conversation contains "再派子节点"
    And the conversation contains "Fork sub node took over"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=step-forksub-fold)
    两层子节点已回交：继承子节点完成均线排列（短多），其再派子节点交付
    细分量化（MA10/MA60 剪刀差 2.1% 收窄），证据并入本步结论。
    [CRITICAL] fork 再派子节点路径可用。
    """

  @route-plan-bg
  Scenario: the background walk's planner proposes the detached-spawn plan
    Given the system contains "planning node"
    And the conversation contains "后台收集"
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
      {"title": "脚本子节点后台收集汇总",
       "steps": ["后台 spawn 一个 scripting 子节点执行脚本计算",
                 "用 collect_subagents 收集后台结果",
                 "汇总子节点回交的量化证据"]}
    ]}
    """

  @bg-child-write
  Scenario: the detached child writes its script first
    Given the conversation contains "[bg child]"
    And the conversation does not contain "spawned in the background"
    And the conversation does not contain "bytes"
    And the conversation does not contain "exit_code"
    And the system does not contain "supervisor"
    And tools are declared
    When the child writes the background script
    Then the reply calls "sandbox_write_file" with
    """
    {"path": "scripts/bg_study.py",
     "content": "import pandas as pd\n# MA5/MA20 alignment study\nprint('bg alignment rows')"}
    """

  @bg-child-run
  Scenario: the detached child runs the written script
    Given the conversation contains "[bg child]"
    And the conversation does not contain "spawned in the background"
    And the conversation contains "bytes"
    And the conversation does not contain "exit_code"
    And the system does not contain "supervisor"
    And tools are declared
    When the child runs the script
    Then the reply calls "sandbox_run" with
    """
    {"path": "scripts/bg_study.py"}
    """

  @bg-child-evidence
  Scenario: the detached child hands the evidence back
    Given the conversation contains "[bg child]"
    And the conversation does not contain "spawned in the background"
    And the conversation contains "exit_code"
    And the system does not contain "supervisor"
    When the child answers with its evidence handback
    Then the reply is
    """
    EVIDENCE (rule=bg-child-evidence)
    后台子节点完成：MA5 4128.6 > MA20 4101.2 — 多头排列成立，间距
    占比 -3.9% 且五日内收窄；量化表已输出。 [CRITICAL] 后台收集路径可用。
    """

  @bg-step-collect-again
  Scenario: the parent re-collects a just-finished spawn
    Given the conversation contains "[step_3]"
    And the conversation contains "后台收集"
    And the conversation contains "uncollected"
    And the conversation contains "just finished"
    And the conversation does not contain "tool call(s))"
    And the system does not contain "supervisor"
    And tools are declared
    When the parent collects the background spawns again
    Then the reply calls "collect_subagents" with
    """
    {}
    """

  @bg-step-fold
  Scenario: the parent folds the gathered background result
    Given the conversation contains "[step_3]"
    And the conversation contains "后台收集"
    And the conversation contains "tool call(s))"
    And the system does not contain "supervisor"
    When the parent answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=bg-step-fold)
    后台子节点已收集：回交证据与回答一致：MA5/MA20 多头排列，间距
    占比 -3.9% 且收窄。 [CRITICAL] 后台收集路径可用。
    """

  @bg-step-collect
  Scenario: the answer gate's nudge lands and the parent collects
    Given the conversation contains "[step_3]"
    And the conversation contains "后台收集"
    And the conversation contains "uncollected"
    And the conversation does not contain "just finished"
    And the conversation does not contain "tool call(s))"
    And the system does not contain "supervisor"
    And tools are declared
    When the parent gathers the background spawns
    Then the reply calls "collect_subagents" with
    """
    {}
    """

  @bg-step-premature
  Scenario: the parent tries to answer before gathering the detached spawn
    Given the conversation contains "[step_3]"
    And the conversation contains "后台收集"
    And the conversation contains "spawned in the background"
    And the conversation does not contain "uncollected"
    And the system does not contain "supervisor"
    When the parent attempts its premature answer
    Then the reply is
    """
    DUMMY FINDING (rule=bg-step-premature)
    脚本子节点已在后台启动，先给出阶段性结论：均线计算进行中。
    """

  @bg-step-spawn
  Scenario: the background walk's step spawns the detached child
    Given the conversation contains "[step_3]"
    And the conversation contains "后台收集"
    And the conversation does not contain "spawned in the background"
    And the system does not contain "supervisor"
    And tools are declared
    When the step spawns its detached child
    Then the reply calls "spawn_subagent" with
    """
    {"goal": "[bg child] 计算均线排列量化表并输出",
     "background": true, "role": "scripting"}
    """
