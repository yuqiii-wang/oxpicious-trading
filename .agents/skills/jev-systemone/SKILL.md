# Skill: jev-systemone

---
name: jev-systemone
description: How to call and study the Jev decision models (System One) —
the resident local-model host's `POST /v1/systemone` contract
(`onejev-1.6-4b`, calibrated probabilities in one forward), the
validated HTTP client method, the deep-agent decision submodule that
consumes it, and the serving contracts any change to
`server_cluster/llm` jev code must keep. Use whenever code or an agent
needs a calibrated probability / typed decision (yes-no, option pick,
score level), anything mentions jev, System One, systemone, or
JevDecisionEngine, or a systemone response/error needs debugging.
---

# jev-systemone — the System One decision contract

## What jev is

The resident local-model host (`server_cluster/llm`, `127.0.0.1:8767`,
always-on under the stream watchdog) serves **OneJev1.6-4B** as
`onejev-1.6-4b` — a Jev decision model: it answers TYPED QUESTIONS
about a state with CALIBRATED PROBABILITIES from ONE forward pass of
the decision prompt (last-position logits restricted to the answer's
token set). It is the System One counterparty of the generative
System Two LLMs: ~0.1s, local GPU, zero provider spend, deterministic
(same request → byte-identical floats; no sampling anywhere).

The host serves the OTHER models by DIFFERENT requests — this is not
a generic LLM server: `/v1/tokenization/segment` → the segmenter,
`/v1/systemone` → jev, `/v1/chat/completions` → dummy-1 (the langgraph
simulation) plus jev's secondary prose surface. Do not build decision
logic on the chat surface; the decision contract is `/v1/systemone`.

## The contract

```jsonc
// POST /v1/systemone  (Bearer lmh-dummy)
{"state": {"task": "...", "ticker": "000001", "rsi14": 71.2},
 "questions": {
   "overbought": {"type": "noul",
                  "instructions": "RSI at 71 means overbought"},
   "action": {"type": "choice", "instructions": "Which action?",
              "criteria": {"add": "add to the position",
                           "hold": "keep unchanged",
                           "trim": "reduce", "exit": "close"}},
   "conviction": {"type": "score", "instructions": "Evidence strength?",
                  "criteria": ["none", "weak", "moderate", "strong"]}}}
// → 200  (the host validated this BEFORE returning it)
{"model": "onejev-1.6-4b",
 "answers": {
   "overbought": {"noul": 0.566},
   "action": {"probabilities": {"add": 0.485, "hold": 0.13,
                                 "trim": 0.11, "exit": 0.277}},
   "conviction": {"score": 1.643,
                  "level_probabilities": {"none": 0.209, "weak": 0.174,
                    "moderate": 0.382, "strong": 0.235}}}}
```

Question types — `noul` → P(yes); `choice` → one probability per
option label (dict criteria); `score` → the expected 0-based level +
per-level probabilities (list criteria, first→last). The host ENFORCES
the response contract server-side (`SystemOneRequest.validate_response`
in `server_cluster/llm/data_definitions/jev_api.py`): answer keys ==
question keys, labels == criteria exactly, every distribution finite,
in [0,1] and summing to 1 (tol 1e-6), the expected score inside
`[0, len(levels)-1]`; a backend breach → HTTP 500
`code=contract_violation`, never a malformed 200. The client
re-validates the same way.

Errors arrive in the OpenAI envelope: bad request bodies → 400
(non-empty labels required, ≤26 options/levels, choice dict / score
list types, noul carries no criteria, int criteria rejected), unknown
model → 404, `media` → 400 text-only, weights not loaded → 503 with a
`POST /v1/models/onejev-1.6-4b/load` retry hint.

## How to call it (the only sanctioned way)

Use the client method — never hand-roll the POST in pipelines/agents
(the method carries the validation and the error discipline):

```py
from server_cluster.llm.client import LocalModelHostClient

client = LocalModelHostClient()  # env LOCAL_MODEL_HOST_URL → :8767
response = client.systemone(
    state={"task": "decide whether to add", "rsi14": 71.2},
    questions={
        "overbought": {"type": "noul",
                       "instructions": "RSI 71 means overbought"},
        "conviction": {"type": "score", "instructions": "How strong?",
                       "criteria": ["none", "weak", "moderate",
                                    "strong"]}},
    read_timeout_s=20.0)
p = response.answers["overbought"].noul          # float in [0, 1]
levels = response.answers["conviction"]          # ScoreAnswer
# raises LocalModelHostError on transport/HTTP failure AND on any
# contract breach (host down, malformed answer) — fail-soft callers
# treat both the same way
```

Raw `requests.post` against the endpoint is for throwaway probes in
`temp_scripts/` only. The sync client inside async code: wrap with
`asyncio.to_thread` (the decision engine does; keep read timeouts
SHORT — the repair lane's pause window is run latency).

## Question design — the measured rules (2026-11-11/12 probes)

1. **Prefer `noul`.** The yes/no vocabulary is this model's strong
   suit (obvious propositions read 0.80 yes / 0.12 yes). Lettered
   multi-way choices read POSITION-BIASED (A dominates) — decompose a
   k-way pick into noul questions or keep choices small and
   semantically separated; treat 3+-way probabilities as soft evidence,
   never as a verdict, unless you have re-validated them for YOUR
   question shape.
2. **Labels are answer tokens.** Non-empty, ≤26 per question; the read
   uses the option LETTERS (A, B, C — what the prompt demands). Never
   two labels where one is a prefix of the other is no longer a
   serving hazard (letters fixed the token collision) but keep labels
   DISTINCT IN MEANING — a `buy`/`buy more` pair still answers as one
   concept split arbitrarily.
3. **State carries compact facts, not prose.** The read conditions on
   the state JSON — a few typed fields beat a paragraph (the decision
   lane's facts are the pattern). Each question gets its OWN forward —
   batching questions is correct now (2026-11-11: per-question
   conditioning replaced the one-row read that made every noul in a
   request identical).
4. **Chinese labels work** (`涨/跌/平` → 0.51/0.24/0.25 on a
   放量+上涨 state — the leading-space token artifact was fixed
   2026-11-11).
5. Deterministic and repeatable — safe to gate thresholds on it, but
   CALIBRATION ≠ ACCURACY: the weights are the OmniJev general release
   with the `onejev-slot-v2` slot format NOT ported (see the backend
   docstring) — validate a new question shape against obvious-anchor
   probes (the calibration-obvious block in
   `temp_scripts/self_test_jev.py`) before trusting it in a lane.

## The deep-agent decision submodule

`llm_agents/deep_agents/langgraph/decision/` — System One judgments
inside the harness ("code filters first, the model judges only what passes"):

- `JevDecisionEngine` (`decision/engine.py`) — `decide_resume(...)`
  (noul "resume?" → the repair lane's verdict) and `decide_spawn(...)`
  (noul "spawn worthwhile?" → yes/no), each backed by a
  `DecisionTemplate` (`decision/templates.py`: `resume_judgment` /
  `node_spawn`; a new decision = one template subclass + one registry
  entry + one call site). NEVER raises, NEVER blocks long: every failure (host down,
  contract breach, below `jev_min_confidence`) lands as
  `JevDecision(ok=False)` and the caller's EXISTING lane judges. The
  repair worker uses the read as a FAST PATH that can never veto: only
  a confident YES applies the retry (zero provider spend, the verdict
  persists as a supervisor-review row); a no/below-floor read defers
  to the generative ask — the 2026-11-12 probes measured the
  operational reads near 0.5 (noise), and letting a decisive-no kill
  the lane's retry policy was rejected deliberately. The spawn gate
  may decline (confident no → the nudge lane judges) — skipping a
  spawn is bounded by design. Injection only:
  `SupervisorAgent(decision_engine=...)` /
  `DeepAgent(decision_engine=...)`); the composing layer wires
  `JevDecisionEngine.active()` (None unless `deep_agent_config.json`'s
  `jev_decisions` is on, `JEV_DECISIONS=0` env kill-switch for
  generative-lane probes — knobs `jev_min_confidence`, `jev_model`
  alongside).
- The question vocabulary (`decision/templates.py`) is part of the
  serving contract — changing the texts changes every verdict; keep
  them stable and re-probe when you must edit.
- Thresholds live in `database/sql/monitoring/static/
  deep_agent_config.json` (the Run Limits panel edits it).

## Operations & verification

- Model in `error` state → almost always transformers: the OneJev
  weights need transformers ≥ 5.x (`qwen3_5`); upgrade, then let the
  watchdog restart the host (or `POST /v1/models/onejev-1.6-4b/load`).
- Serving changes (`server_cluster/llm/backends/jev.py`,
  `data_definitions/jev_api.py`, `routers/jev.py`) must keep:
  the per-question forward, the letter-token read for choice/score,
  the whitespace-candidate filter, the warmup via the systemone path
  (NOT `chat_stream` — the lock re-acquire self-deadlocked warmups
  before 2026-11-11), and the response validation. The regression
  battery (run through WSL conda):
  `python -u -I temp_scripts/self_test_jev.py` (live probes + wire
  invariants — must end `SELF-TEST CLEAN`),
  `python -u -I temp_scripts/verify_lm_host_systemone.py` (stub wire
  shape + math), `temp_scripts/test_jev_decision.py` (decision-lane
  stubs), `temp_scripts/probe_jev_decision_live.py` (real-model
  verdicts). Model-independent host checks:
  `verify_lm_host_segmentation.py` / `verify_lm_host_stream.py`.
