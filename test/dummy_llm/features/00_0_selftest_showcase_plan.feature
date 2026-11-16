Feature: selftest_showcase_plan — the ``[this is test]`` MAIN
  chain's plan face. This file sorts FIRST (00_0_ < 00_router_)
  so the showcase plan wins over every other plan face whatever
  the caller typed after the prefix; the step_4 parent faces stay
  in 00_selftest_showcase.feature, AFTER 00_router_chain's child
  faces (the fork child's inherited conversation embeds the
  parent's [step_4] and must match ``@fork-child-subspawn``
  first). The goal's 【全场景自检】 marker keys the plan.

  @plan-selftest-showcase
  Scenario: the showcase planner proposes the seven-scenario plan
    Given the system contains "planning node"
    And the conversation contains "全场景自检"
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
       "steps": ["把指标脚本计算路由给脚本完成",
                 "汇总脚本回交的量化证据"]},
      {"title": "继承分叉续算再派子节点",
       "steps": ["fork 一个子节点继承本对话上下文完成脚本续算",
                 "子节点再派子节点完成细分量化计算",
                 "汇总两层子节点回交的量化证据"]},
      {"title": "脚本子节点计算与失败调查",
       "steps": ["派脚本子节点计算排列量化表",
                 "子节点失败时派调查员调查失败原因",
                 "按调查建议取得量化证据"]},
      {"title": "风险提示与结论校验",
       "steps": ["读取风险口径的基础表",
                 "校验各步结论的一致性"]},
      {"title": "综合结论整理",
       "steps": ["汇总前序各步的发现与分歧",
                 "给出综合结论"]}
    ]}
    """
