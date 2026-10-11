"""Security classification — ALL rules as JSON, ONE small generic engine.

Two parts:

  1. ``classification_rules/`` — language-agnostic JSON configs (the RULES):
       industry.json  INDUSTRY ruleset — real industries (FIN, HC, TECH,
                     DEBT, …): sector_id holds the sector, industry_id the
                     industry.
       strategy.json  STRATEGY ruleset — themes/strategies (BROAD, DIV,
                     REGION, STRATEGY, SOE, OVERSEAS): sector_id holds the
                     strategy (BROAD, DIV, …), industry_id the theme
                     (BROAD_CSI, DIV_SOE, …).  A strategy IS a sector and a
                     theme IS an industry in the unified model.
       config.json    fallback defaults, strategy sector ids, ETF
                     name-cleaning markers, overlapping catalog entries.
     Schema + scoring semantics: classification_rules/README.md.  Swapping
     in a different rules directory changes the classification with ZERO
     Python changes.

  2. this module — the small generic interpreter (no domain literals):
     keyword scoring, tie-breaks, tag dedup, ETF name cleaning, catalog
     assembly, and a CLI that runs any rules dir:
       python -m database.sec_statics.classification --rules DIR --names FILE

Unified model: a security is classified against BOTH rulesets.  The
industry match is PRIMARY when it exists (is_industry_not_strategy=TRUE,
e.g. 中证银行 → FIN/BANKS), otherwise the strategy match (FALSE, e.g.
沪深300 → BROAD/BROAD_CSI).  Both share the (sector_id, industry_id) DB
columns; ``tags`` carries every matching (sector_id, industry_id) pair.

Scoring: score = (generic_penalty, total_hit_len, n_hits, longest_hit_len,
-rule_order) — higher wins, exact ties keep the earliest rule.
generic_penalty is 0 for the ruleset's generic_penalty_sectors (BROAD /
REGION) so specific strategies always beat broad catch-alls regardless of
keyword length.  tags() dedups by (sector_id, industry_id) keeping the
best, with single_tag_sectors (BROAD) contributing at most one tag.
Rule ORDER matters — see classification_rules/README.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, FrozenSet, List, Mapping, Optional, Set, Tuple

RULES_DIR = Path(__file__).resolve().parent / "classification_rules"

Tag = Tuple[str, str, str, str]  # (sector_id, sector_label, industry_id, industry_label)
Score = Tuple[int, int, int, int, int]
RuleTuple = Tuple[str, str, str, str, List[str]]
Catalog = Dict[str, Dict[str, object]]

__all__ = [
    "DEFAULT_SECTOR_ID", "DEFAULT_SECTOR_LABEL", "DEFAULT_INDUSTRY_ID",
    "DEFAULT_INDUSTRY_LABEL", "STRATEGY_SECTOR_IDS", "OVERLAPPING_CATALOG",
    "INDUSTRY_RULES", "STRATEGY_RULES", "INDEX_RULES", "RULE_ORDER",
    "STRATEGY_RULE_ORDER", "classify_index", "classify_index_tags",
    "classify_index_strategy", "classify_index_strategy_tags",
    "classify_index_all_tags", "classify_index_both", "classify_etf_by_name",
    "classify_etf_strategy_by_name", "build_catalog", "build_catalog_for",
    "load_config", "load_ruleset", "RuleSet", "merge_tags", "main",
]


# ============================================================================
# JSON loading — the only bridge between classification_rules/ and Python
# ============================================================================
def load_config(rules_dir: Path = RULES_DIR) -> Mapping[str, object]:
    """Load ``<rules_dir>/config.json`` (defaults, strategy sector ids, ETF
    name-cleaning markers, overlapping catalog)."""
    with (rules_dir / "config.json").open("r", encoding="utf-8") as f:
        doc: Mapping[str, object] = json.load(f)
    return doc


def load_ruleset(path: Path) -> Mapping[str, object]:
    """Load ONE ruleset document (industry.json / strategy.json / any file
    with the same schema)."""
    with path.open("r", encoding="utf-8") as f:
        doc: Mapping[str, object] = json.load(f)
    return doc


def _defaults_tag(config: Mapping[str, object]) -> Tag:
    """The fallback classification from a config's ``defaults`` section."""
    d: Mapping[str, str] = config["defaults"]
    return (d["sector_id"], d["sector_label"], d["industry_id"], d["industry_label"])


def _overlapping(config: Mapping[str, object]) -> List[Tuple[str, str, str, str]]:
    """Catalog-only (sector, industry) entries from ``overlapping_catalog``."""
    entries: List[Mapping[str, str]] = config["overlapping_catalog"]
    return [(e["sector_id"], e["sector_label"],
             e["industry_id"], e["industry_label"]) for e in entries]


# ============================================================================
# Generic ruleset interpreter
# ============================================================================
class RuleSet:
    """One ruleset JSON: ordered keyword rules + scoring options.

    Rules may carry extra documentation fields (e.g. ``note``) — ignored.
    ``rule_order`` is the rule's 0-based index in the file; when the same
    (sector_id, industry_id) key appears twice, the LATER index wins the
    lookup (both entries score with it).
    """

    def __init__(self, doc: Mapping[str, object]) -> None:
        self.name: str = str(doc.get("ruleset", ""))
        rules: List[Mapping[str, object]] = doc["rules"]
        self.rules: List[RuleTuple] = [
            (str(r["sector_id"]), str(r["sector_label"]),
             str(r["industry_id"]), str(r["industry_label"]),
             [str(kw) for kw in r["keywords"]])
            for r in rules]
        self.generic_penalty_sectors: FrozenSet[str] = frozenset(
            str(s) for s in doc.get("generic_penalty_sectors", ()))
        self.single_tag_sectors: FrozenSet[str] = frozenset(
            str(s) for s in doc.get("single_tag_sectors", ()))
        self.rule_order: Dict[Tuple[str, str], int] = {
            (r[0], r[2]): i for i, r in enumerate(self.rules)}

    def matches(self, name: str) -> List[Tuple[Score, Tag]]:
        """All matching rules as (score, tag), in rule order."""
        s = str(name)
        out: List[Tuple[Score, Tag]] = []
        for sector_id, sector_label, industry_id, industry_label, keywords in self.rules:
            hits = [kw for kw in keywords if kw in s]
            if not hits:
                continue
            penalty = 0 if sector_id in self.generic_penalty_sectors else 1
            score: Score = (penalty, sum(len(kw) for kw in hits), len(hits),
                            max(len(kw) for kw in hits),
                            -self.rule_order[(sector_id, industry_id)])
            out.append((score, (sector_id, sector_label,
                                industry_id, industry_label)))
        return out

    def best(self, name: str) -> Optional[Tag]:
        """Highest-scoring match; exact ties keep the earliest rule."""
        best_tag: Optional[Tag] = None
        best_score: Optional[Score] = None
        for score, tag in self.matches(name):
            if best_score is None or score > best_score:
                best_score, best_tag = score, tag
        return best_tag

    def tags(self, name: str) -> List[Tag]:
        """All matches, score-sorted (desc, stable) and deduplicated by
        (sector_id, industry_id) keeping the best; sectors in
        single_tag_sectors contribute at most one tag."""
        matches = self.matches(name)
        if not matches:
            return []
        # Stable sort on the score alone: equal scores keep rule order.
        matches.sort(key=lambda m: m[0], reverse=True)
        seen: Set[Tuple[str, str]] = set()
        single_kept: Set[str] = set()
        result: List[Tag] = []
        for _, tag in matches:
            key = (tag[0], tag[2])
            if key in seen:
                continue
            if tag[0] in self.single_tag_sectors:
                if tag[0] in single_kept:
                    continue
                single_kept.add(tag[0])
            seen.add(key)
            result.append(tag)
        return result


# ============================================================================
# Packaged rules/config + constants (legacy API kept for existing callers)
# ============================================================================
CONFIG: Mapping[str, object] = load_config()
INDUSTRY_RULESET = RuleSet(load_ruleset(RULES_DIR / "industry.json"))
STRATEGY_RULESET = RuleSet(load_ruleset(RULES_DIR / "strategy.json"))

_OTHER_FALLBACK: Tag = _defaults_tag(CONFIG)
(DEFAULT_SECTOR_ID, DEFAULT_SECTOR_LABEL,
 DEFAULT_INDUSTRY_ID, DEFAULT_INDUSTRY_LABEL) = _OTHER_FALLBACK
STRATEGY_SECTOR_IDS: FrozenSet[str] = frozenset(
    str(s) for s in CONFIG["strategy_sector_ids"])
OVERLAPPING_CATALOG: List[Tuple[str, str, str, str]] = _overlapping(CONFIG)

# Legacy tuple rule tables + order lookups (catalog build, study_select_etf
# and other callers iterate the full rule set).
INDUSTRY_RULES: List[RuleTuple] = INDUSTRY_RULESET.rules
STRATEGY_RULES: List[RuleTuple] = STRATEGY_RULESET.rules
INDEX_RULES: List[RuleTuple] = INDUSTRY_RULES + STRATEGY_RULES
RULE_ORDER: Dict[Tuple[str, str], int] = {
    (r[0], r[2]): i for i, r in enumerate(INDEX_RULES)}
STRATEGY_RULE_ORDER: Dict[Tuple[str, str], int] = {
    (r[0], r[2]): i for i, r in enumerate(STRATEGY_RULES)}

# ETF name-cleaning markers (config.json etf_name_cleaning)
_ETF_CLEANING: Mapping[str, object] = CONFIG["etf_name_cleaning"]
_IB_NAME_MARKER: str = str(_ETF_CLEANING["ib_name_marker"])
_SUFFIX_SPLIT: str = str(_ETF_CLEANING["suffix_split"])
_LOF_MARKERS: List[str] = [str(m) for m in _ETF_CLEANING["lof_markers"]]


# ============================================================================
# classify_index* API (industry + strategy dimensions)
# ============================================================================
def classify_index(name: str) -> Tag:
    """Classify by INDUSTRY rules only; falls back to the config defaults
    (OTHER) when nothing matches.  Strategy-type classifications are
    handled by ``classify_index_strategy``."""
    return INDUSTRY_RULESET.best(name) or _OTHER_FALLBACK


def classify_index_tags(name: str) -> List[Tag]:
    """ALL matching INDUSTRY classifications, score-sorted (the first entry
    is the primary industry); [OTHER fallback] when nothing matches."""
    return INDUSTRY_RULESET.tags(name) or [_OTHER_FALLBACK]


def classify_index_strategy(name: str) -> Tag:
    """Classify by STRATEGY rules only (sector_id holds the strategy,
    industry_id the theme); falls back to the config defaults."""
    return STRATEGY_RULESET.best(name) or _OTHER_FALLBACK


def classify_index_strategy_tags(name: str) -> List[Tag]:
    """ALL matching STRATEGY classifications, score-sorted; an index may
    match several (央企红利 → DIV_SOE + SOE_THEME); sectors in
    single_tag_sectors (BROAD) contribute at most one tag."""
    return STRATEGY_RULESET.tags(name) or [_OTHER_FALLBACK]


def merge_tags(industry_tags: List[Tag], strategy_tags: List[Tag]) -> List[Tag]:
    """Merge ruleset tag lists: industry first, then strategy, deduplicated
    by (sector_id, industry_id)."""
    seen: Set[Tuple[str, str]] = set()
    result: List[Tag] = []
    for tag in industry_tags + strategy_tags:
        key = (tag[0], tag[2])
        if key not in seen:
            seen.add(key)
            result.append(tag)
    return result


def classify_index_all_tags(name: str) -> List[Tag]:
    """ALL matching classifications from BOTH rule sets: industry tags
    first (score-sorted), then strategy tags (score-sorted), deduplicated.
    The OTHER fallback is only returned when NEITHER dimension matched.
    Populates the ``tags`` field in the sec_classification cache and
    stats.sec_index_tags."""
    ind_tags = INDUSTRY_RULESET.tags(name)
    strat_tags = STRATEGY_RULESET.tags(name)
    if not ind_tags and not strat_tags:
        return [_OTHER_FALLBACK]
    return merge_tags(ind_tags, strat_tags)


def classify_index_both(name: str) -> Tuple[Tag, Tag, bool]:
    """(industry_tuple, strategy_tuple, is_industry_not_strategy) — the
    caller picks the PRIMARY (sector_id, industry_id) pair: industry when
    it matched (TRUE), strategy otherwise (FALSE)."""
    ind = classify_index(name)
    strat = classify_index_strategy(name)
    return (ind, strat, ind[0] != DEFAULT_SECTOR_ID)


# ============================================================================
# ETF-name wrappers (markers from config.json etf_name_cleaning)
# ============================================================================
def _clean_etf_name(etf_name: str) -> str:
    """Strip the legal suffix (split at 交易型, dropping 交易型开放式指数
    证券投资基金 — avoids a false 证券 match) and the first LOF marker
    (position > 0) so the theme keywords are exposed."""
    clean = etf_name.split(_SUFFIX_SPLIT)[0]
    for marker in _LOF_MARKERS:
        lof_idx = clean.find(marker)
        if lof_idx > 0:
            clean = clean[:lof_idx]
            break
    return clean


def _classify_etf_name(
    classify: Callable[[str], Tag],
    etf_name: str,
    index_name: str,
) -> Tag:
    """Shared IB-name routing + suffix stripping.

    Names WITHOUT the standard Chinese suffix marker are foreign-branded
    'IB names' (iShares/安硕, Premia, TIGER, …) whose English names carry
    no Chinese keywords — classify those by the underlying ``index_name``
    (Chinese) first, falling back to the ETF name; standard names by the
    cleaned ETF name."""
    etf_name = str(etf_name or "")
    index_name = str(index_name or "")
    if _IB_NAME_MARKER not in etf_name:
        if index_name:
            result = classify(index_name)
            if result[0] != DEFAULT_SECTOR_ID:
                return result
        return classify(etf_name)
    return classify(_clean_etf_name(etf_name))


def classify_etf_by_name(etf_name: str, index_name: str = "") -> Tag:
    """Classify an ETF by its name when parent-index inheritance gives
    OTHER — INDUSTRY rules (see ``_classify_etf_name`` for the IB-name and
    suffix-stripping behaviour)."""
    return _classify_etf_name(classify_index, etf_name, index_name)


def classify_etf_strategy_by_name(etf_name: str, index_name: str = "") -> Tag:
    """Same as ``classify_etf_by_name`` but with STRATEGY rules."""
    return _classify_etf_name(classify_index_strategy, etf_name, index_name)


# ============================================================================
# Catalog assembly (pure function of the JSON configs)
# ============================================================================
def build_catalog_for(
    index_rules: List[RuleTuple],
    overlapping_catalog: List[Tuple[str, str, str, str]],
    defaults: Tag,
) -> Catalog:
    """Build {sector_id: {label, industries: {industry_id: {label, slug}}}}
    from rule tuples — defaults sector first, then rules in order, then the
    overlapping entries; slug = industry_id.lower().  One catalog covers
    industry AND strategy sectors (the DB stores both in the same
    sector_id/industry_id columns; there is no separate theme catalog)."""
    catalog: Catalog = {}

    def _add(sector_id: str, sector_label: str,
             industry_id: str, industry_label: str) -> None:
        if sector_id not in catalog:
            catalog[sector_id] = {"label": sector_label, "industries": {}}
        sector = catalog[sector_id]
        sector["label"] = sector_label
        industries: Dict[str, Dict[str, str]] = sector["industries"]
        if industry_id not in industries:
            industries[industry_id] = {"label": industry_label,
                                       "slug": industry_id.lower()}
        else:
            industries[industry_id]["label"] = industry_label

    _add(*defaults)
    for sector_id, sector_label, industry_id, industry_label, _ in index_rules:
        _add(sector_id, sector_label, industry_id, industry_label)
    for sector_id, sector_label, industry_id, industry_label in overlapping_catalog:
        _add(sector_id, sector_label, industry_id, industry_label)
    return catalog


def build_catalog() -> Catalog:
    """Catalog from the packaged JSON rule configs (INDEX_RULES spans both
    industry and strategy sectors)."""
    return build_catalog_for(INDEX_RULES, OVERLAPPING_CATALOG, _OTHER_FALLBACK)


# ============================================================================
# CLI — run any rules directory over a list of names
# ============================================================================
def main(argv: Optional[List[str]] = None) -> int:
    """``python -m database.sec_statics.classification`` — load ANY rules
    directory (config.json + industry.json + strategy.json) and classify
    names, emitting the catalog + per-name classifications as JSON."""
    from _common.df_utils._activate import activate  # repo entry-point convention
    activate()

    parser = argparse.ArgumentParser(
        prog="python -m database.sec_statics.classification",
        description="Run JSON classification rule configs over a list of names.")
    parser.add_argument("--rules", type=Path, default=RULES_DIR,
                        help="Rules directory (default: the packaged "
                             "classification_rules/).")
    parser.add_argument("--names", type=Path, nargs="*", default=[],
                        help="File(s) with one security name per line.")
    parser.add_argument("--name", action="append", default=[],
                        help="A single security name (repeatable).")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output JSON file (default: stdout).")
    args = parser.parse_args(argv)

    for fname in ("config.json", "industry.json", "strategy.json"):
        if not (args.rules / fname).is_file():
            print(f"[FATAL] {args.rules / fname} not found — not a rules directory",
                  file=sys.stderr)
            return 2

    config = load_config(args.rules)
    industry = RuleSet(load_ruleset(args.rules / "industry.json"))
    strategy = RuleSet(load_ruleset(args.rules / "strategy.json"))
    fallback = _defaults_tag(config)

    names: List[str] = []
    for path in args.names:
        with path.open("r", encoding="utf-8") as f:
            names.extend(line.strip() for line in f if line.strip())
    names.extend(n.strip() for n in args.name if n.strip())
    if not names:
        parser.error("no names given — pass --name and/or --names")

    classifications = {}
    for name in names:
        ind = industry.best(name)
        strat = strategy.best(name)
        is_ind = ind is not None
        primary = ind if is_ind else (strat or fallback)
        tags = merge_tags(industry.tags(name), strategy.tags(name))
        classifications[name] = {
            "industry": list(ind) if ind else None,
            "strategy": list(strat) if strat else None,
            "primary": list(primary),
            "is_industry_not_strategy": is_ind,
            "tags": [list(t) for t in (tags or [fallback])],
        }

    result = {
        "rules_dir": str(args.rules),
        "n_names": len(names),
        "catalog": build_catalog_for(industry.rules + strategy.rules,
                                     _overlapping(config), fallback),
        "classifications": classifications,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out is not None:
        with args.out.open("w", encoding="utf-8") as f:
            f.write(text + "\n")
        print(f"[CLI] {len(names)} names classified -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
