"""llm_agents.test — OFFLINE test doubles for the llm_agents harness.

Test-support subpackage: fakes that stand in for the live providers so
langgraph and deep-agent runs execute deterministically with ZERO
provider spend. The dependency direction is one-way — production code
never imports this package; tests, temp_scripts probes and the deep
agent CLI's ``--provider dummy`` pull from here.
"""
