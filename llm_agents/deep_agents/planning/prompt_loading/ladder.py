"""llm_agents.deep_agents.planning.prompt_loading.ladder — the loading
ladder itself (:class:`PromptLadder`).

The six rungs (keyword match, eager fast path, load-set decision,
pass-down lines, named keys, per-node matches) as methods of the
object that holds ONE run's catalog rows. Callers holding a SUBSET
(the not-yet-loaded rows of a step hop) pass it as *rows*; None means
the ladder's own catalog. The rungs:

  1. KEYWORD MATCH — the pure label filter (:meth:`match`): a
     ``bound_labels`` phrase in the run's vocabulary is FORCED, an
     ``optional_labels`` hit a candidate;
  2. EAGER — the small-run fast path (:meth:`eager`): a selection at
     or under the configured rows/chars loads wholesale, no verdict;
  3. LOAD SET — the final decision (:meth:`load_set`): plan rows ∪
     eager/verdict+forced+hotpath skills, catalog order;
  4. PASS-DOWN — the unloaded rows' preview lines (:meth:`pass_down`);
  5. NAMED KEYS — keys named outright in a directive (:meth:`named_in`);
  6. PER-NODE MATCHES — a hop's/fork's own dynamic rung
     (:meth:`node_matches`), plus the KG-hotpath corroboration
     (:meth:`hotpath_rung` — a promoted chain implicating a row loads
     it like a force).

Pure domain: no DB, no provider — stub-testable.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence

from llm_agents.deep_agents.limits import run_limits

from llm_agents.deep_agents.planning.prompt_loading.catalog import (
    CatalogRow, _normalize,
)

# A prompt_key as a standalone token inside free text — keys are
# kebab-case words (ma-spread-guide), so the boundaries exclude the
# word/dash characters a longer identifier would continue with.
_KEY_TOKEN_RE_TEMPLATE = r"(?<![\w-]){key}(?![\w-])"


class PromptLadder:
    """The loading ladder over ONE run's catalog rows — the six rungs
    (keyword match, eager fast path, load-set decision, pass-down
    lines, named keys, per-node matches) as methods of the object that
    holds the catalog. Callers holding a SUBSET (the not-yet-loaded
    rows of a step hop) pass it as *rows*; None means the ladder's own
    catalog."""

    def __init__(self, rows: Sequence[CatalogRow]) -> None:
        self._rows: List[CatalogRow] = list(rows)

    @property
    def rows(self) -> List[CatalogRow]:
        """The bound catalog (a copy — callers never mutate the rung's
        universe)."""
        return list(self._rows)

    @property
    def skills(self) -> List[CatalogRow]:
        """The skill rows (the ladder gates only these; plan rows
        always load)."""
        return [r for r in self._rows if not r.is_plan]

    def _rows_for(self, rows: Optional[Sequence[CatalogRow]]
                  ) -> List[CatalogRow]:
        return list(rows) if rows is not None else self._rows

    def match(
        self,
        *,
        question: str = "",
        scope_name: str = "",
        item_labels: Sequence[str] = (),
        prompt_labels: Optional[Dict[str, Dict[str, Sequence[str]]]] = None,
        rows: Optional[Sequence[CatalogRow]] = None,
    ) -> Dict[str, str]:
        """The pure keyword rung: which catalog rows the run's own
        vocabulary forces or candidates — the ai_prompts label contract
        (bound = force-load phrase, optional = findability vocabulary),
        case-insensitive substring per the DDL, with the run-scoped label
        OVERRIDES (``data.prompt_labels``, "key|sub_type" → effective
        arrays) applied first.

        Returns prompt_key -> ``"forced"`` | ``"matched"`` | ``""``
        (absent key = no signal). Forced rows load unconditionally;
        matched rows are candidates the verdict may load."""
        catalog = self._rows_for(rows)
        haystack = " | ".join(
            _normalize(p) for p in
            [question, scope_name, *item_labels] if str(p or "").strip())
        overrides: Dict[str, Dict[str, Sequence[str]]] = {
            str(k).strip(): v for k, v in (prompt_labels or {}).items()
            if isinstance(v, dict)}
        verdict: Dict[str, str] = {}
        for row in catalog:
            identity = f"{row.prompt_key}|{row.sub_type}"
            effective = overrides.get(identity) or {}
            bound = [str(x) for x in (effective.get("bound")
                                      or row.bound_labels) if str(x).strip()]
            optional = [str(x) for x in (effective.get("optional")
                                         or row.optional_labels)
                        if str(x).strip()]
            if not haystack:
                continue
            key = "forced" if any(
                _normalize(label) in haystack for label in bound) else (
                "matched" if any(
                    _normalize(label) in haystack for label in optional)
                else "")
            if key:
                verdict[row.prompt_key] = key
        return verdict

    def eager(
        self,
        *,
        skills: Optional[Sequence[CatalogRow]] = None,
        instruction_chars: Optional[Dict[str, int]] = None,
    ) -> bool:
        """Whether the small-run FAST PATH holds: everything selected
        loads eagerly and the verdict never runs. True when the skills
        selection is small (≤ the configured prompt_eager_rows) or its
        full texts fit the configured prompt_eager_chars — the ladder
        only pays when the bulk it prevents is real. *instruction_chars*
        is the run's best known full-text size per key (0 when unknown —
        the planner-side estimate uses the catalog fetch; the ladder
        then trusts the row count). *skills* defaults to the ladder's
        own skill rows."""
        selected = list(skills) if skills is not None else self.skills
        limits = run_limits()
        if len(selected) <= limits.prompt_eager_rows:
            return True
        known = [instruction_chars.get(s.prompt_key, 0)
                 for s in selected] if instruction_chars else \
            [0 for _ in selected]
        if all(known):
            return sum(known) <= limits.prompt_eager_chars
        # Sizes unknown (DB fetch of texts not done yet): a wide selection
        # goes through the verdict — the catalog is what makes planning
        # cheap, not the eager load.
        return False

    def load_set(
        self,
        *,
        label_matches: Dict[str, str],
        verdict: Optional[Sequence[str]],
        instruction_chars: Optional[Dict[str, int]] = None,
    ) -> List[str]:
        """The run's FINAL load set, in catalog order: every plan-type
        row (plan prompts shape the plan itself), every keyword-forced/
        candidate skill under the eager fast path, and otherwise the
        planner's verdict ∪ the keyword-forced skills ∪ the
        hotpath-corroborated skills (``"hotpath"`` — the KG hotpaths
        evidence lane, :meth:`hotpath_rung` — loads exactly like a
        bound-label force). Unknown verdict keys are dropped (the
        catalog is the universe); the verdict only ever NARROWS the
        skills — it cannot unload a plan prompt, a forced row or a
        hotpath row."""
        catalog = self._rows
        if self.eager(instruction_chars=instruction_chars or {}):
            chosen = {r.prompt_key for r in self.skills}
        else:
            forced = {k for k, v in label_matches.items()
                      if v in ("forced", "hotpath")}
            allowed = {r.prompt_key for r in self.skills}
            chosen = (forced | {str(k) for k in (verdict or [])}) & allowed
        return [r.prompt_key for r in catalog
                if r.is_plan or r.prompt_key in chosen]

    def pass_down(
        self, rows: Optional[Sequence[CatalogRow]] = None,
    ) -> Dict[str, str]:
        """The unloaded rows' pass-down face: prompt_key -> one preview
        line (the dict every step hop's message and every forked child's
        structured data carries — plain str -> str, checkpoint-safe)."""
        return {r.prompt_key: r.preview_line() for r in self._rows_for(rows)}

    def named_in(
        self, text: str, rows: Optional[Sequence[CatalogRow]] = None,
    ) -> List[str]:
        """The catalog rows whose prompt_key is NAMED in *text* — a
        sub-plan goal or step directive that writes "study prompts:
        ma-spread-guide, pe-guide" names its skills outright. The exact
        key string is a far stronger signal than any label match: a
        named key is the planner's/step's own declaration of what it
        works under."""
        catalog = self._rows_for(rows)
        haystack = str(text or "")
        named: List[str] = []
        for row in catalog:
            pattern = _KEY_TOKEN_RE_TEMPLATE.format(
                key=re.escape(row.prompt_key))
            if re.search(pattern, haystack):
                named.append(row.prompt_key)
        return named

    def node_matches(
        self,
        directive: str,
        *,
        rows: Optional[Sequence[CatalogRow]] = None,
        scope_name: str = "",
        item_labels: Sequence[str] = (),
        prompt_labels: Optional[Dict[str, Dict[str, Sequence[str]]]] = None,
    ) -> Dict[str, str]:
        """The DYNAMIC loading rung — one NODE's (step hop's or forked
        child's) own match over the NOT-YET-LOADED catalog rows, judged
        from the node's own vocabulary: its directive text (title + fork
        goal — a sub-plan goal naming its study prompts hits
        :meth:`named_in`). Keyword matching stays the HIGHER-priority
        contract it is at plan time: bound-label hits force-load
        (``"forced"``), optional-label hits candidate-load
        (``"matched"``), and a key named outright in the directive
        loads as ``"named"`` — none of these wait for the model's
        judgment. Rows already loaded at run level never re-enter (the
        caller passes only the not-yet-loaded rows); returns
        prompt_key -> reason, absences = nothing to load for this
        node."""
        catalog = self._rows_for(rows)
        verdict = dict(self.match(
            question=directive, scope_name=scope_name,
            item_labels=item_labels, prompt_labels=prompt_labels,
            rows=catalog))
        for key in self.named_in(directive, catalog):
            verdict.setdefault(key, "named")
        return verdict

    def hotpath_rung(
        self,
        evidence_texts: Sequence[str],
        rows: Optional[Sequence[CatalogRow]] = None,
    ) -> Dict[str, str]:
        """The KG-HOTPATH corroboration rung (2026-11-07): the catalog
        rows whose OPTIONAL labels hit the hotpath evidence vocabulary —
        the promoted (two-hit-verified) ``llm.kg_hotpaths`` chains'
        query texts and their entities' names/tags (ns0's bilingual
        trader vocabulary bridges the label↔question synonym gap: a
        row labeled "margin flows" matches a question asking 两融 through
        the margin tables' tags). A hit returns ``"hotpath"`` — the
        verdict that LOADS (:meth:`load_set`), no planner verdict
        needed: a question whose domain the KG already resolved twice
        is known territory, and the skills its promoted chains carry
        are the run's standing instructions. Bound labels are NOT
        consulted here (they load via :meth:`match` on the question
        itself); the run-scoped overrides apply like everywhere. Pure —
        the caller (``planning/kg_prompts.HotpathPromptProbe``) owns
        the evidence gathering."""
        catalog = self._rows_for(rows)
        haystack = " | ".join(
            _normalize(t) for t in evidence_texts if str(t or "").strip())
        if not haystack:
            return {}
        return {row.prompt_key: "hotpath" for row in catalog
                if any(_normalize(label) in haystack for label in row.optional_labels
                       if str(label).strip())}
