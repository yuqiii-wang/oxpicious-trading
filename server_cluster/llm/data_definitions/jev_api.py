"""server_cluster.llm.data_definitions.jev_api — the System
One API payloads (the standard Jev decision contract, ``POST
/v1/systemone``).

A Jev model answers TYPED QUESTIONS about a state with calibrated
probabilities — three question types (the TypeSafe System One
vocabulary):

* ``noul``  — a yes/no proposition → ``{noul: p_yes}``
* ``choice``— unordered options (``criteria`` maps label → description)
              → ``{probabilities: {label: p}}``
* ``score`` — ordered levels (``criteria`` lists them first→last)
              → ``{score: expected_level}`` (+ ``level_probabilities``)

The wire shape mirrors the OneJev/Flide examples: ``state`` (task
context), ``media`` (rejected here — text-only), ``questions`` (the
typed map). Our host serves several jev models, so the request carries
an OPTIONAL ``model`` extension field routing to a registered model id
(omitted → the single registered jev model; ambiguous → error).

The RESPONSE is always the same envelope — ``{"model": <served id>,
"answers": {question_key: answer}}`` — and every answer obeys the
invariants enforced here (:class:`NoulAnswer` / :class:`ChoiceAnswer` /
:class:`ScoreAnswer`): one entry per requested option, finite
probabilities in [0, 1] summing to 1, the expected ``score`` inside
``[0, len(levels) - 1]``. :meth:`SystemOneRequest.validate_response`
is the full contract check (key/label correspondence included) — the
router validates its backend's output with it and the HTTP client
(``server_cluster.llm.client``) validates the wire answer the same way.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)

# Float64 softmax output sums to 1 within ~1e-12; the tolerance only
# absorbs serialization round-trips.
PROB_SUM_TOL = 1e-6

# The prompt letters options A..Z — both option counts are capped by it.
MAX_OPTIONS = 26


def _check_distribution(where: str, dist: Dict[str, float]) -> None:
    """One categorical distribution: every entry a finite float in
    [0, 1] and the whole dist summing to 1 (within tolerance)."""
    total = 0.0
    for label, p in dist.items():
        if not math.isfinite(p):
            raise ValueError(f"{where}: p({label!r})={p!r} is not finite")
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"{where}: p({label!r})={p} out of [0, 1]")
        total += p
    if abs(total - 1.0) > PROB_SUM_TOL:
        raise ValueError(f"{where}: probabilities sum to {total!r} "
                         "(must be 1)")


def _check_label(label: str, where: str) -> str:
    """One option label / level name: non-empty (it becomes the answer
    token the probability read keys on — an empty or blank label
    silently reads a whitespace token)."""
    if not label or not label.strip():
        raise ValueError(f"{where}: labels must be non-empty "
                         f"(got {label!r})")
    return label


class SystemOneQuestion(BaseModel):
    """One typed question (see module docstring for the vocabulary)."""

    type: Literal["noul", "choice", "score"]
    instructions: str = Field(min_length=1,
                              description="The proposition / question "
                              "text.")
    criteria: Optional[Union[Dict[str, str], List[str]]] = Field(
        default=None,
        description="choice: {option_label: description}; score: the "
        "ordered levels (first → last); noul: none.")

    @model_validator(mode="after")
    def _criteria_required_per_type(self) -> "SystemOneQuestion":
        if self.type == "noul":
            if self.criteria is not None:
                raise ValueError("noul takes no criteria (yes/no is "
                                 "implied)")
            return self
        if not self.criteria:
            raise ValueError(
                f"{self.type} questions require criteria "
                "(choice: {label: description}; score: ordered levels)")
        if self.type == "choice" and not isinstance(self.criteria, dict):
            raise ValueError("choice criteria must be "
                             "{option_label: description}")
        if self.type == "score" and not isinstance(self.criteria, list):
            raise ValueError("score criteria must be the ordered levels "
                             "as a list")
        if len(self.criteria) > MAX_OPTIONS:
            raise ValueError(f"{self.type} supports at most "
                             f"{MAX_OPTIONS} entries (A..Z lettering)")
        for label in (self.criteria if isinstance(self.criteria, list)
                      else self.criteria):
            _check_label(label, f"{self.type} criteria")
        return self


class SystemOneRequest(BaseModel):
    """The ``POST /v1/systemone`` body (the standard fields + our
    optional ``model`` routing extension)."""

    state: Optional[Dict[str, Any]] = Field(
        default=None, description="Task context the questions are "
        "about (arbitrary JSON; ``<image:N>`` placeholders reference "
        "media on multimodal servers).")
    media: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="Images/video — REJECTED by this "
        "host for now (text-only serving).")
    questions: Dict[str, SystemOneQuestion] = Field(
        min_length=1, description="The typed questions to answer.")
    model: Optional[str] = Field(
        default=None, description="HOST EXTENSION — the registered "
        "jev model id; omitted resolves to the single registered jev "
        "model.")

    # ------------------------------------------------- response contract
    def validate_response(self, model_id: str,
                          answers: Any, *,
                          usage: Optional[Dict[str, int]] = None
                          ) -> "SystemOneResponse":
        """The full response contract, checked against THIS request:
        the served model id, one answer per question key, each answer's
        labels exactly the question's criteria, calibrated
        distributions, the expected score inside its range. ``usage``
        (optional, host-supplied token counts) rides on the envelope.
        Raises ``ValueError`` naming the breach; returns the typed
        envelope."""
        if self.model is not None and model_id != self.model:
            raise ValueError(
                f"served model {model_id!r} != requested "
                f"{self.model!r}")
        if not isinstance(answers, dict) or \
                set(answers) != set(self.questions):
            raise ValueError(
                f"answers keys {sorted(answers) if isinstance(answers, dict) else answers!r} "
                f"!= question keys {sorted(self.questions)}")
        checked: Dict[str, Any] = {}
        for key, q in self.questions.items():
            where = f"answers[{key!r}]"
            if q.type == "noul":
                checked[key] = NoulAnswer.model_validate(answers[key])
                continue
            if q.type == "choice":
                assert isinstance(q.criteria, dict)
                expected = set(q.criteria)
                answer = ChoiceAnswer.model_validate(answers[key])
                if set(answer.probabilities) != expected:
                    raise ValueError(
                        f"{where}: labels {sorted(answer.probabilities)} "
                        f"!= criteria {sorted(expected)}")
                checked[key] = answer
                continue
            assert isinstance(q.criteria, list)
            expected_levels = [str(c) for c in q.criteria]
            answer = ScoreAnswer.model_validate(answers[key])
            if set(answer.level_probabilities) != set(expected_levels):
                raise ValueError(
                    f"{where}: levels {sorted(answer.level_probabilities)} "
                    f"!= criteria {expected_levels}")
            if not 0.0 <= answer.score <= len(expected_levels) - 1:
                raise ValueError(
                    f"{where}: score {answer.score} outside "
                    f"[0, {len(expected_levels) - 1}]")
            checked[key] = answer
        return SystemOneResponse(
            model=model_id, answers=checked, usage=usage)


class NoulAnswer(BaseModel):
    """The ``noul`` answer — P(yes), calibrated."""

    model_config = ConfigDict(extra="forbid")

    noul: float = Field(ge=0.0, le=1.0, description="The calibrated "
                        "probability of YES.")

    @field_validator("noul")
    @classmethod
    def _finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("noul probability must be finite")
        return v


class ChoiceAnswer(BaseModel):
    """The ``choice`` answer — one calibrated probability per option."""

    model_config = ConfigDict(extra="forbid")

    probabilities: Dict[str, float] = Field(
        min_length=1,
        description="One probability per option label (sums to 1).")

    @field_validator("probabilities")
    @classmethod
    def _calibrated(cls, v: Dict[str, float]) -> Dict[str, float]:
        _check_distribution("probabilities", v)
        return v


class ScoreAnswer(BaseModel):
    """The ``score`` answer — the expected level + the level mass."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(description="The expected level — the "
                         "probability-weighted index into the ordered "
                         "criteria (0-based; the request check bounds it "
                         "to [0, len(levels) - 1]).")
    level_probabilities: Dict[str, float] = Field(
        min_length=1,
        description="The probability of each ordered level (sums to 1).")

    @field_validator("level_probabilities")
    @classmethod
    def _calibrated(cls, v: Dict[str, float]) -> Dict[str, float]:
        _check_distribution("level_probabilities", v)
        return v


# The answer union — the three shapes carry DISJOINT required keys
# ({noul} / {probabilities} / {score, level_probabilities}) and forbid
# extras, so exactly one member validates per payload.
SystemOneAnswer = Union[NoulAnswer, ScoreAnswer, ChoiceAnswer]


class SystemOneResponse(BaseModel):
    """The ``POST /v1/systemone`` response envelope — ``model`` names
    the served jev model, ``answers`` carries one calibrated answer per
    question key. Validate with
    :meth:`SystemOneRequest.validate_response` (the request carries the
    criteria the answers must match)."""

    model: str = Field(description="The served jev model id.")
    answers: Dict[str, SystemOneAnswer] = Field(
        min_length=1, description="One answer per question key.")
    usage: Optional[Dict[str, int]] = Field(
        default=None,
        description="Token consumption of the read (host-supplied): "
        "prompt_tokens / completion_tokens / total_tokens — the "
        "templated decision prompt of each question's forward plus one "
        "answer-letter read per question.")
