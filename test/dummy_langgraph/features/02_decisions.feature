Feature: decisions — System One choice expectations per decision input
  For the SAME decision input the harness feeds the resident jev model,
  the feature asserts the choice output. Given = the typed decision
  facts (the JSON DocString — the REAL ResumeFacts / SpawnFacts
  contract); When = the template that reads them; Then = the expected
  RAW argmax choice (yes/no — the template's post-interpret vocabulary,
  retry-step/no-action, is the CALLER's mapping) at its confidence
  floor. Expectations are the MEASURED reads of the resident onejev
  model (deterministic, zero spend; 2026-11-12): the throttle-class
  retry — the repair lane's canonical case — reads a confident YES;
  plain operational reads sit near 0.5 and read NO, which is exactly
  why the repair fast path is affirm-only. The stub engine answers
  from these same expectations offline.

  @resume-rate-limit-throttle
  Scenario: a rate-limited step with budget left is resumed
    Given the resume facts
    """
    {"error_text": "zhipu 1302: 速率限制 rate limit exceeded, please retry later",
     "node_title": "读取沪深300日线数据",
     "rate_limited": true, "attempts": 0, "max_node_retries": 3,
     "repairs_used": 0, "max_repairs_per_run": 2}
    """
    When the resume_judgment template asks whether to resume
    Then the choice is "yes" with confidence at least 0.6

  @resume-timeout-deferred
  Scenario: a plain read timeout is deferred to the generative lane
    Given the resume facts
    """
    {"error_text": "ReadTimeout: The read operation timed out",
     "node_title": "读取沪深300日线数据",
     "rate_limited": false, "attempts": 0, "max_node_retries": 3,
     "repairs_used": 0, "max_repairs_per_run": 2}
    """
    When the resume_judgment template asks whether to resume
    Then the choice is "no" with confidence at least 0.5

  @resume-budget-spent
  Scenario: a node whose retries are all spent is not resumed
    Given the resume facts
    """
    {"error_text": "RuntimeError: hit the 6-step limit",
     "node_title": "读取沪深300日线数据",
     "rate_limited": false, "attempts": 3, "max_node_retries": 3,
     "repairs_used": 2, "max_repairs_per_run": 2}
    """
    When the resume_judgment template asks whether to resume
    Then the choice is "no" with confidence at least 0.5

  @route-spawn-continuity
  Scenario: a query step that continues the inference chain is not spawned
    Given the route_spawn facts
    """
    {"node_role": "", "depth": 0, "steps_used": 4, "max_steps": 24,
     "response_head": "表结构已确认，继续取相邻交易日的成交额差值，与前日的放量幅度对齐后比较",
     "pending_tools": "db_run_query,sandbox_run", "sandbox_calls": 1,
     "fail_streak": 0, "syntax_streak": 0, "spawns_used": 0,
     "max_spawns": 3}
    """
    When the route_spawn template asks whether to spawn
    Then the choice is "no" with confidence at least 0.5

  @route-spawn-scripting-task
  Scenario: a self-contained indicator-script task is spawned to a scripting sub node
    Given the route_spawn facts
    """
    {"node_role": "", "depth": 0, "steps_used": 6, "max_steps": 24,
     "response_head": "现在写一个脚本：取不少于250个日线，计算5/10/20/60/120/250日均线，输出排列、间距与金叉死叉的量化表",
     "pending_tools": "sandbox_write_file,sandbox_run",
     "sandbox_calls": 2, "fail_streak": 0, "syntax_streak": 0,
     "spawns_used": 0, "max_spawns": 3}
    """
    When the route_spawn template asks whether to spawn
    Then the choice is "yes" with confidence at least 0.5

  @route-spawn-single-call
  Scenario: one proposed sandbox call early in a biz step is spawned
    Given the route_spawn facts
    """
    {"node_role": "", "depth": 0, "steps_used": 3, "max_steps": 24,
     "response_head": "写一个脚本：取250日收盘并计算MA5/MA20的排列与间距",
     "pending_tools": "sandbox_run", "sandbox_calls": 1,
     "fail_streak": 0, "syntax_streak": 0, "spawns_used": 0,
     "max_spawns": 3}
    """
    When the route_spawn template asks whether to spawn
    Then the choice is "yes" with confidence at least 0.5

  @route-fork-drifted
  Scenario: a context gradually chasing side details away from the node purpose forks
    Given the route_fork facts
    """
    {"node_role": "", "depth": 0, "goal_head": "计算沪深300的MA5/MA20排列状态并给出趋势结论",
     "steps_used": 14, "max_steps": 24,
     "response_head": "顺手再看看创业板的成交额节奏，并把昨天算过的聚合换个口径重算一遍",
     "pending_tools": "sandbox_run", "sandbox_calls": 1,
     "fail_streak": 0, "syntax_streak": 0,
     "spawns_used": 0, "max_spawns": 1}
    """
    When the route_fork template asks whether to fork
    Then the choice is "yes" with confidence at least 0.5

  @route-fork-on-purpose
  Scenario: a hard but on-purpose line of work is not drift — no fork
    Given the route_fork facts
    """
    {"node_role": "", "depth": 0, "goal_head": "计算沪深300的MA5/MA20排列状态并给出趋势结论",
     "steps_used": 8, "max_steps": 24,
     "response_head": "均线口径与前日对齐后，逐段核对最近五个交易日的排列翻转点",
     "pending_tools": "db_run_query", "sandbox_calls": 0,
     "fail_streak": 0, "syntax_streak": 0,
     "spawns_used": 0, "max_spawns": 1}
    """
    When the route_fork template asks whether to fork
    Then the choice is "no" with confidence at least 0.5

  @fork-point-predrift
  Scenario: the fork-point read picks the earliest on-purpose checkpoint
    Given the route_fork_point facts
    """
    {"goal_head": "计算沪深300的MA5/MA20排列状态并给出趋势结论",
     "candidates": [
       {"label": "turn_2",
        "head": "已确认表结构与数据窗口，MA5/MA20 口径对齐，可以开算"},
       {"label": "turn_6",
        "head": "排列表已出；开始顺手看创业板成交额，偏离原目标"},
       {"label": "turn_9",
        "head": "把已完成的聚合换口径重算，继续在支线上消耗预算"}],
     "steps_used": 14, "max_steps": 24}
    """
    When the route_fork_point template asks which context to fork from
    Then the choice is "turn_2" with confidence at least 0.5

  @fork-point-least-drifted
  Scenario: with every checkpoint drifted the model still prefers the earliest over now
    Given the route_fork_point facts
    """
    {"goal_head": "计算沪深300的MA5/MA20排列状态并给出趋势结论",
     "candidates": [
       {"label": "turn_3",
        "head": "第一轮查询就偏到行业资金流，未回到均线目标"},
       {"label": "turn_7",
        "head": "继续在资金流支线上加查询，仍未回到均线"}],
     "steps_used": 9, "max_steps": 24}
    """
    When the route_fork_point template asks which context to fork from
    Then the choice is "turn_3" with confidence at least 0.4

  @route-join-budget-messy
  Scenario: a late scripting node with repeated failures joins back
    Given the route_join facts
    """
    {"node_role": "scripting", "steps_used": 20, "max_steps": 24,
     "response_head": "还差最后一个聚合输出，我再改一版跑",
     "pending_tools": "sandbox_edit_file,sandbox_run", "sandbox_calls": 2,
     "fail_streak": 3, "syntax_streak": 1}
    """
    When the route_join template asks whether to stop and join
    Then the choice is "yes" with confidence at least 0.5

  @route-join-recoverable
  Scenario: a first failed patch in a scripting node is not a join
    Given the route_join facts
    """
    {"node_role": "scripting", "steps_used": 5, "max_steps": 24,
     "response_head": "group by 列名写错了，改为按 code,date 分组后重跑",
     "pending_tools": "sandbox_edit_file,sandbox_run",
     "sandbox_calls": 2, "fail_streak": 1, "syntax_streak": 1}
    """
    When the route_join template asks whether to stop and join
    Then the choice is "no" with confidence at least 0.5

  @route-join-messy
  Scenario: a repeated-failure scripting node near its budget joins back
    Given the route_join facts
    """
    {"node_role": "scripting", "steps_used": 9, "max_steps": 12,
     "response_head": "第三次修改 group by 仍然报错，我再改一版重跑",
     "pending_tools": "sandbox_edit_file,sandbox_run",
     "sandbox_calls": 2, "fail_streak": 3, "syntax_streak": 2}
    """
    When the route_join template asks whether to stop and join
    Then the choice is "yes" with confidence at least 0.5

  @failure-probe-spawn
  Scenario: a failed scripting child gets an investigator spawn
    Given the probe facts
    """
    {"failed_role": "scripting",
     "failed_goal": "计算六条均线的排列与间距并输出量化表",
     "error_text": "RuntimeError: hit the 6-step limit",
     "probes_used": 0, "max_probes": 1}
    """
    When the failure_probe template asks whether to spawn an investigator
    Then the choice is "yes" with confidence at least 0.5

  @spawn-streak-declined
  Scenario: a syntax-error streak still declines the repair spawn
    Given the spawn facts
    """
    {"syntax_streak": 3, "threshold": 2, "spawns_used": 0,
     "max_spawns": 1, "script_path": "study.py",
     "fail_lines": "SyntaxError: invalid syntax"}
    """
    When the node_spawn template asks whether to spawn
    Then the choice is "no" with confidence at least 0.5

  @spawn-streak-at-threshold
  Scenario: a mechanical grouping-error streak exactly at the threshold still reads no
    Given the spawn facts
    """
    {"syntax_streak": 2, "threshold": 2, "spawns_used": 0,
     "max_spawns": 1, "script_path": "scripts/ma_child.py",
     "fail_lines": "psycopg.errors.GroupingError: column \"r.date\" must appear in the GROUP BY clause\npsycopg.errors.GroupingError: column \"r.date\" must appear in the GROUP BY clause"}
    """
    When the node_spawn template asks whether to spawn
    Then the choice is "no" with confidence at least 0.4

  @spawn-streak-clear
  Scenario: no failure streak means no repair spawn
    Given the spawn facts
    """
    {"syntax_streak": 0, "threshold": 2, "spawns_used": 0,
     "max_spawns": 1, "script_path": "study.py",
     "fail_lines": ""}
    """
    When the node_spawn template asks whether to spawn
    Then the choice is "no" with confidence at least 0.5
