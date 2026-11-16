Feature: selftest_showcase — the ``[this is test]`` MAIN chain's faces
  The daemon's self-test main run is the SHOWCASE chain (2026-11-14):
  ONE graph the UI opens right after the run, holding every scenario
  kind as nodes — trend (kg/db), volume (the flake → supervisor
  verdict → RESUME, 01_summary_chain's faces), a SPAWN step
  (00_router_chain's ``@step-script-*`` + ``@script-child-*`` faces,
  reused verbatim via the exact step title) and a FORK step whose fork
  child spawns its OWN sub node (00_router_chain's
  ``@fork-child-sub*`` + ``@forksub-child-*`` faces reused; only the
  step_4 parent faces are new here). The goal carries the 【全场景自检】
  marker, which keys the plan face below; the per-step discriminators
  are the STEP TITLES (the [Run goal] block rides EVERY step
  conversation, so goal text can never discriminate a step). This file
  sorts AFTER 00_router_chain (the child faces must match the fork
  child's inherited conversation — it embeds the parent's [step_4] —
  before these parent faces) and BEFORE the catch-alls.

  (The PLAN face lives in 00_0_selftest_showcase_plan.feature — it
  must sort BEFORE 00_router_chain's plan faces; these step_4
  parent faces must sort AFTER its child faces, because the fork
  child's inherited conversation embeds the parent's [step_4].)

  @showcase-step4-proposal
  Scenario: the fork step proposes its continuation script
    Given the conversation contains "[step_4]"
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

  @showcase-step4-fold
  Scenario: the fork step folds the two-generation evidence
    Given the conversation contains "[step_4]"
    And the conversation contains "再派子节点"
    And the conversation contains "Fork sub node took over"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=showcase-step4-fold)
    继承子节点已回交两层证据：均线排列短多，其再派子节点交付细分量化
    （剪刀差收窄），全部分析节点完成。 [CRITICAL] 全场景自检链路可用。
    """

  @showcase-step5-spawn
  Scenario: the failure-investigation step spawns a scripting child
    Given the conversation contains "[step_5]"
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

  @showcase-step5-fold
  Scenario: the failure-investigation step folds the investigator's findings
    Given the conversation contains "[step_5]"
    And the conversation contains "脚本子节点计算与失败调查"
    And the conversation contains "Failure probe"
    And the system does not contain "supervisor"
    When the step answers with its folded finding
    Then the reply is
    """
    DUMMY FINDING (rule=showcase-step5-fold)
    调查员定位根因：子节点沙盒会话丢失（写入后未回填路径）；本步按建议
    携带 reuse_session 重发脚本子节点，量化表已取得，排列结论：短多。
    [CRITICAL] 失败调查路径可用。
    """

  @showcase-step6-fail
  Scenario: the risk step dies on a missing table (stays failed under no-action)
    Given the conversation contains "[step_6]"
    And the conversation contains "风险提示与结论校验"
    And the system does not contain "supervisor"
    And tools are declared
    And the flake is armed once
    When the step's first attempt is served
    Then the reply is
    """
    psycopg.errors.UndefinedTable: relation "v_risk_daily" does not
    exist (deliberate flake, scenario showcase-step6-fail)
    """
