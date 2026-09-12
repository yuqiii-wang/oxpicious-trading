/**
 * QuestionSearchBar — dual-mode search input for the News page.
 *
 * Sits directly beneath the classification nav (above the Source/Author
 * chip rows, which are part of every search request). One question, two
 * parallel request paths behind two buttons:
 *
 *   • Local  — naive-tokenizes the question into news-taxonomy keywords
 *     (GET /api/news/tokenize → WSL python builds.text.tokenize) and drives
 *     the SAME /api/news/items keyword search the old header keyword bar
 *     drove, with the page's scope/source/author filters applied; the
 *     corpus list + date strip + author chips co-filter.
 *   • Online — live zhihu content search (POST /api/news/search), rendered
 *     by NewsSearchResults in place of the list.
 *
 * While the input is non-empty, source chips without question-search support
 * render disabled in the page (QUESTION_SEARCH_ENABLED_SOURCES below,
 * mirrored from api/routes/news.ts).
 */
import { type KeyboardEvent } from "react";
import {
  Button,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { Search as SearchIcon, Clear as ClearIcon, TravelExplore as OnlineIcon } from "@mui/icons-material";

/** Sources the online question search currently supports — every other
 *  source chip is disabled while the question bar has input. */
export const QUESTION_SEARCH_ENABLED_SOURCES: ReadonlyArray<string> = ["zhihu"];

interface Props {
  /** Current input value (page-owned so the chip-disable logic can react). */
  value: string;
  onChange: (v: string) => void;
  /** 本地 — tokenize + search the stored corpus (keyword search API). */
  onLocalSearch: () => void;
  /** 在线 — live source search (zhihu for now). */
  onOnlineSearch: () => void;
  /** Clear only the active local search (keeps input + online results). */
  onClearLocal: () => void;
  /** Full reset: input + local search + online results. */
  onClear: () => void;
  /** Hover state of the Online button — the page disables the non-enabled
   *  source chips while it's true (same cue as question input). */
  onOnlineHoverChange?: (hover: boolean) => void;
  localRunning: boolean;
  onlineRunning: boolean;
  /** Active local-search terms (null = browsing mode). */
  localTerms: string | null;
  /** True when the last local search matched no taxonomy keywords. */
  localEmpty: boolean;
  /** True while an online result set is displayed (shows the reset button). */
  active: boolean;
}

export default function QuestionSearchBar({
  value,
  onChange,
  onLocalSearch,
  onOnlineSearch,
  onClearLocal,
  onClear,
  onOnlineHoverChange,
  localRunning,
  onlineRunning,
  localTerms,
  localEmpty,
  active,
}: Props) {
  const handleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onLocalSearch();
    }
  };

  return (
    <Stack direction="row" spacing={0.5} sx={{ flexWrap: "wrap", gap: 0.5, mb: 0.75, alignItems: "center" }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 600, minWidth: 56, fontSize: "0.75rem" }}>
        Question
      </Typography>
      <TextField
        size="small"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={handleKey}
        placeholder="Ask a question — Local searches the corpus / Online searches Zhihu"
        sx={{ flex: 1, minWidth: 260, "& .MuiInputBase-input": { fontSize: "0.8rem" } }}
      />
      <Tooltip title="Local search — tokenize the question, query the stored corpus with the active Source / Author / classification filters">
        <span>
          <Button
            size="small"
            variant={localTerms ? "contained" : "outlined"}
            color="primary"
            disabled={localRunning || value.trim() === ""}
            onClick={onLocalSearch}
            startIcon={localRunning ? <CircularProgress size={13} thickness={5} /> : <SearchIcon />}
            sx={{ fontSize: "0.7rem", minWidth: 0, px: 1 }}
          >
            Local
          </Button>
        </span>
      </Tooltip>
      {/* Hover handlers live on the wrapper span: with an empty input the
          Online button is disabled (pointer-events: none), so the span is
          what actually receives the hover. */}
      <Tooltip title="Online search — fetch live Zhihu answers (Source / Author travel with the request)">
        <span
          onMouseEnter={() => onOnlineHoverChange?.(true)}
          onMouseLeave={() => onOnlineHoverChange?.(false)}
        >
          <Button
            size="small"
            variant={active ? "contained" : "outlined"}
            color="secondary"
            disabled={onlineRunning || value.trim() === ""}
            onClick={onOnlineSearch}
            startIcon={onlineRunning ? <CircularProgress size={13} thickness={5} /> : <OnlineIcon />}
            sx={{ fontSize: "0.7rem", minWidth: 0, px: 1 }}
          >
            Online
          </Button>
        </span>
      </Tooltip>
      {localTerms && (
        <Chip
          label={`Local: ${localTerms}`}
          size="small"
          color="primary"
          variant="filled"
          onDelete={onClearLocal}
          sx={{
            fontSize: "0.7rem",
            maxWidth: 320,
            "& .MuiChip-label": {
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            },
          }}
        />
      )}
      {localEmpty && !localTerms && (
        <Typography variant="subtitle2" color="text.secondary" sx={{ fontSize: "0.7rem" }}>
          No taxonomy keyword matched
        </Typography>
      )}
      {active && (
        <Tooltip title="清除提问搜索结果">
          <IconButton aria-label="清除提问搜索" size="small" onClick={onClear}>
            <ClearIcon sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>
      )}
    </Stack>
  );
}
