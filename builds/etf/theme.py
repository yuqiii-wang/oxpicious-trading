"""ETF theme classification (keyword-based)."""
from _common.study_select_etf import ETF_THEMES

# Derive ETF_THEME_RULES (list of (theme_id, label, slug, keywords) tuples)
# from ETF_THEMES OrderedDict.
ETF_THEME_RULES = [
    (tid, cfg.get("theme_label", tid), cfg.get("slug", tid), cfg.get("kw", []))
    for tid, cfg in ETF_THEMES.items()
]

_BUILD_THEME_RULE_ORDER = {tid: i for i, (tid, _, _, _) in enumerate(ETF_THEME_RULES)}


def classify_etf_theme(name):
    """Classify an ETF name into a theme via keyword matching.

    Returns (theme_id, theme_label, theme_slug).
    """
    s = str(name)
    best = None
    best_score = None
    for tid, label, slug, kws in ETF_THEME_RULES:
        hits = [kw for kw in kws if kw in s]
        if not hits:
            continue
        n_hits = len(hits)
        total_len = sum(len(k) for k in hits)
        longest_kw = max(len(k) for k in hits)
        rule_order = _BUILD_THEME_RULE_ORDER.get(tid, 9999)
        score = (total_len, n_hits, longest_kw, -rule_order)
        if best_score is None or score > best_score:
            best_score = score
            best = (tid, label, slug)
    if best is not None:
        return best
    return "OTHER", "其他｜未分类  Unclassified", "other"
