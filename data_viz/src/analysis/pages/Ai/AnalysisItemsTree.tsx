/**
 * AnalysisItemsTree — the summary page's first left foldable, "Scopes":
 * the analysis items as a nested checkbox list grouped by their top-nav
 * category (Commons / Derivatives / Composites / Signals).
 *
 * The panel folds HORIZONTALLY: collapsed it is a slim vertical strip on
 * the far left (icon + rotated title + selected/total count), and a
 * click expands it to the RIGHT; the expanded header's chevron folds it
 * back. On narrow screens (xs) there is no room to grow sideways, so the
 * strip becomes a full-width bar and the fold falls back to vertical.
 * The panel stretches to the summary card's height; its body scrolls.
 * Inside, every item starts CHECKED ("select all" default). Each category
 * row is a tri-state checkbox (checked = all items, indeterminate = some)
 * that also toggles the group open/closed via the chevron; the master
 * "All" row on top spans every category. Item keys are
 * `categorySlug/itemSlug` so the selection set is ready to feed the
 * summary generation when it lands. Every item row carries its own Ask AI
 * "?" (AnalysisItemAskButton) that opens the shared ask modal scoped to
 * that analysis.
 */
import { useState } from "react";
import {
  Box,
  Checkbox,
  Collapse,
  IconButton,
  Paper,
  Typography,
} from "@mui/material";
import {
  CenterFocusStrong,
  ChevronLeft,
  ChevronRight,
  ExpandLess,
  ExpandMore,
} from "@mui/icons-material";
import AnalysisItemAskButton from "./AnalysisItemAskButton";
import {
  ALL_ANALYSIS_ITEM_KEYS,
  ANALYSIS_CATEGORIES,
  analysisItemKey,
  type AnalysisCategoryEntry,
} from "./constants";

/** Folded strip width vs expanded panel width (px, sm+). */
const STRIP_WIDTH = 46;
const PANEL_WIDTH = 300;

export interface AnalysisItemsTreeProps {
  /** Checked leaf keys (`categorySlug/itemSlug`); all-checked by default. */
  selectedKeys: Set<string>;
  onToggleItem: (key: string, checked: boolean) => void;
  onToggleCategory: (category: AnalysisCategoryEntry, checked: boolean) => void;
  onToggleAll: (checked: boolean) => void;
}

export default function AnalysisItemsTree(props: AnalysisItemsTreeProps) {
  const { selectedKeys, onToggleItem, onToggleCategory, onToggleAll } = props;
  // The panel starts folded into a left strip — a click expands it
  // rightward; the expanded header's chevron folds it back.
  const [expanded, setExpanded] = useState(false);
  // All categories start expanded — nothing hides behind a second
  // collapsed level inside the open panel.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const allCount = ALL_ANALYSIS_ITEM_KEYS.length;
  const allChecked = selectedKeys.size === allCount;
  const someChecked = selectedKeys.size > 0 && !allChecked;

  const toggleCollapsed = (slug: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) {
        next.delete(slug);
      } else {
        next.add(slug);
      }
      return next;
    });
  };

  return (
    <Paper
      variant="outlined"
      onClick={expanded ? undefined : () => setExpanded(true)}
      sx={{
        width: { xs: "100%", sm: expanded ? PANEL_WIDTH : STRIP_WIDTH },
        flexShrink: 0,
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        cursor: expanded ? "default" : "pointer",
        transition: (theme) =>
          theme.transitions.create("width", {
            duration: theme.transitions.duration.complex,
          }),
      }}
    >
      {expanded ? (
        <>
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 0.5,
              px: 1.5,
              py: 0.75,
              borderBottom: 1,
              borderColor: "divider",
              flexShrink: 0,
            }}
          >
            <CenterFocusStrong fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Scopes
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {selectedKeys.size}/{allCount}
            </Typography>
            <IconButton
              size="small"
              aria-label="Collapse scopes"
              onClick={(e) => {
                e.stopPropagation();
                setExpanded(false);
              }}
              sx={{ p: 0.25 }}
            >
              <ChevronLeft fontSize="small" />
            </IconButton>
          </Box>
          <Box sx={{ flex: 1, minHeight: 0, overflowY: "auto", p: 1 }}>
            {/* Master row — spans every category. */}
            <Box sx={{ display: "flex", alignItems: "center" }}>
              <Checkbox
                size="small"
                checked={allChecked}
                indeterminate={someChecked}
                onChange={(e) => onToggleAll(e.target.checked)}
                inputProps={{ "aria-label": "Select all analyses" }}
                sx={{ py: 0.5 }}
              />
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                All
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ ml: "auto" }}>
                {selectedKeys.size}/{allCount}
              </Typography>
            </Box>
            {ANALYSIS_CATEGORIES.map((category) => {
              const checkedCount = category.items.filter((item) =>
                selectedKeys.has(analysisItemKey(category, item)),
              ).length;
              const categoryAll = checkedCount === category.items.length;
              const isCollapsed = collapsed.has(category.slug);
              return (
                <Box key={category.slug}>
                  <Box sx={{ display: "flex", alignItems: "center" }}>
                    <Checkbox
                      size="small"
                      checked={categoryAll}
                      indeterminate={checkedCount > 0 && !categoryAll}
                      onChange={(e) => onToggleCategory(category, e.target.checked)}
                      inputProps={{ "aria-label": `Select all ${category.title} analyses` }}
                      sx={{ py: 0.5 }}
                    />
                    <Typography
                      variant="body2"
                      onClick={() => toggleCollapsed(category.slug)}
                      sx={{ fontWeight: 600, cursor: "pointer", userSelect: "none", flexGrow: 1 }}
                    >
                      {category.title}
                    </Typography>
                    <IconButton size="small" onClick={() => toggleCollapsed(category.slug)} sx={{ p: 0.25 }}>
                      {isCollapsed ? <ExpandMore fontSize="small" /> : <ExpandLess fontSize="small" />}
                    </IconButton>
                  </Box>
                  <Collapse in={!isCollapsed}>
                    <Box sx={{ pl: 3 }}>
                      {category.items.map((item) => {
                        const key = analysisItemKey(category, item);
                        return (
                          <Box key={key} sx={{ display: "flex", alignItems: "center" }}>
                            <Checkbox
                              size="small"
                              checked={selectedKeys.has(key)}
                              onChange={(e) => onToggleItem(key, e.target.checked)}
                              inputProps={{ "aria-label": item.title }}
                              sx={{ py: 0.25 }}
                            />
                            <Typography
                              variant="body2"
                              color="text.secondary"
                              noWrap
                              onClick={() => onToggleItem(key, !selectedKeys.has(key))}
                              sx={{ cursor: "pointer", userSelect: "none", flexGrow: 1 }}
                            >
                              {item.title}
                            </Typography>
                            <AnalysisItemAskButton category={category} item={item} />
                          </Box>
                        );
                      })}
                    </Box>
                  </Collapse>
                </Box>
              );
            })}
          </Box>
        </>
      ) : (
        <>
          {/* Folded strip (sm+) — vertical title; the chevron points the
              way the panel expands (rightward). */}
          <Box
            sx={{
              display: { xs: "none", sm: "flex" },
              flexDirection: "column",
              alignItems: "center",
              gap: 1,
              py: 1.5,
              flex: 1,
            }}
          >
            <CenterFocusStrong fontSize="small" color="primary" />
            <Typography
              component="div"
              sx={{ writingMode: "vertical-rl", fontWeight: 600, fontSize: 13 }}
            >
              Scopes
            </Typography>
            <Typography
              component="div"
              variant="caption"
              color="text.secondary"
              sx={{ writingMode: "vertical-rl" }}
            >
              {selectedKeys.size}/{allCount}
            </Typography>
            <ChevronRight fontSize="small" color="action" sx={{ mt: "auto" }} />
          </Box>
          {/* Folded bar on xs — narrow screens fold vertically instead. */}
          <Box
            sx={{
              display: { xs: "flex", sm: "none" },
              alignItems: "center",
              gap: 1,
              px: 1.5,
              py: 1,
            }}
          >
            <CenterFocusStrong fontSize="small" color="primary" sx={{ mr: 0.5 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 600, flexGrow: 1 }}>
              Scopes
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {selectedKeys.size}/{allCount}
            </Typography>
            <ExpandMore fontSize="small" color="action" />
          </Box>
        </>
      )}
    </Paper>
  );
}
