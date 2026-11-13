/**
 * GenerateQueryDialog — the Generate button's pre-run popup: instead of
 * posting the run immediately, the click opens this dialog over the
 * summary page to confirm/edit the run's four knobs before it starts:
 *
 * - The QUERY text (the request's `question` — the run's focus
 *   instruction the planning session and plan templates carry as
 *   {{question}}). It DEFAULTS to the codes/market under the current
 *   scope pick ("Study 000001 上证指数 + …" / "Study the whole market")
 *   and FOLLOWS the dialog's code edits until the user edits the text
 *   itself (a touched text stops tracking the codes).
 * - The SCOPE CODES — the same subject tag bar the Scopes panel keeps
 *   (tags + a freeSolo add input over the loaded trees' suggestions),
 *   defaulting to the page's current pick; empty = market-wide. Hidden
 *   under the "all" sec_type (its run scope stays absent by contract).
 * - The FORCED TABLE SCOPE — the DB tables this run's KG surface
 *   (kg_search / kg_expand / the data hints) serves; the deep-agent
 *   serving layer filters structurally to the ticked names. The SAME
 *   shared nested tick dropdown the Knowledge Graph config page mounts
 *   (KgTableScopeSelect — schema group headers, one checkbox per
 *   table, chips for the ticks; the data imports from the KG's ns0
 *   table corpus, one session fetch). Empty = no limitation.
 * - The PLAN GOAL — the ONE-radio study-scope choice (Auto + the
 *   plan/goal prompt rows), defaulting to the Prompts panel's current
 *   pick. The radios REACT to the dialog's own code pick — the
 *   codes-family rows need named codes, the market-family rows the
 *   empty pick (goalAppliesToScope, the same contract the panel drives
 *   from the page-level scope) — and a selection the edited codes rule
 *   out falls back to Auto.
 *
 * Confirm hands the four values to the page: it syncs the goal into
 * the prompt selection and the codes into the scope tags (the dialog
 * edits the SAME lists the panels keep, so the run, the history-strip
 * filter and the reactive effects all agree), then starts the run.
 * Cancel discards everything — the page state never saw the edits.
 */
import { useEffect, useState } from "react";
import {
  Autocomplete,
  Box,
  Button,
  Chip,
  createFilterOptions,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControlLabel,
  Radio,
  RadioGroup,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import { PlayArrow } from "@mui/icons-material";
import type { AiPrompt } from "@/lib/api-client/aiSummary";
import KgTableScopeSelect from "@/shared/components/kg-table-scope";
import { goalAppliesToScope } from "./constants";
import type { ScopeSubjectEntry } from "./AnalysisItemsTree";

/** The confirm payload — the run's focus instruction, the scope codes
 *  (bare codes / industry ids, in tag order), the forced-table scope
 *  (empty = no limitation), and the picked plan-goal prompt_key
 *  (null = Auto — none picked, the runner defaults by the run's
 *  scope). */
export interface GenerateQueryValues {
  question: string;
  codes: string[];
  forcedTables: string[];
  goalKey: string | null;
}

interface GenerateQueryDialogProps {
  open: boolean;
  onClose: () => void;
  onConfirm: (values: GenerateQueryValues) => void;
  /** The plan-type goal rows (the radio set; the runner's mutually
   *  exclusive study-scope family). */
  goalPrompts: AiPrompt[];
  /** The currently picked goal's prompt_key from the Prompts panel —
   *  the dialog radio's default (null = Auto). */
  selectedGoalKey: string | null;
  /** The page's current scope subjects — the codes editor's seed. */
  initialCodes: ScopeSubjectEntry[];
  /** The page's current forced-table scope — the editor's seed. */
  initialForcedTables: string[];
  /** The codes editor's suggestion list (the loaded trees' items). */
  subjectSuggestions: Array<{ code: string; name: string }>;
  /** The subject kind label (Index / ETF / Stock / Industry); absent
   *  under the "all" sec_type — the codes editor stays hidden then
   *  (an "all" run's scope is absent = market-wide, no codes to name). */
  subjectKind?: string;
}

/** The stock tree carries thousands of items — the default match set
 *  stays bounded so the popper never renders it whole (the Scopes
 *  panel's input bar runs the same cap). */
const defaultOptionFilter = createFilterOptions<{
  code: string;
  name: string;
}>();
const SUBJECT_OPTION_CAP = 60;

/** Suffix-insensitive code key (000001.SZ ≙ 000001) — matches the
 *  page's own subjectKey so a typed suffixed code joins its bare
 *  twin's tag instead of duplicating it. */
const subjectKey = (code: string): string =>
  code.toUpperCase().replace(/\.(SS|SZ|SH|BJ|HK)$/i, "");

/** The untouched query text's default — the codes/market under scope:
 *  the picked subjects joined by " + " (code + resolved name), or the
 *  whole-market sentence on an empty pick. */
function defaultQueryText(codes: ScopeSubjectEntry[]): string {
  if (codes.length === 0) return "Study the whole market";
  return (
    "Study " +
    codes
      .map((s) => (s.name === undefined ? s.code : `${s.code} ${s.name}`))
      .join(" + ")
  );
}

export default function GenerateQueryDialog({
  open,
  onClose,
  onConfirm,
  goalPrompts,
  selectedGoalKey,
  initialCodes,
  initialForcedTables,
  subjectSuggestions,
  subjectKind,
}: GenerateQueryDialogProps) {
  const [codes, setCodes] = useState<ScopeSubjectEntry[]>([]);
  const [forcedTables, setForcedTables] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [questionTouched, setQuestionTouched] = useState(false);
  const [goalKey, setGoalKey] = useState<string | null>(null);
  const [input, setInput] = useState("");

  // Fresh dialog state per open — seeded from the page's current pick
  // (codes → the scope tags, the goal → the Prompts panel's radio, the
  // text → the scope default, the tables → the forced-table scope). A
  // closed-then-reopened dialog does not resurrect the previous
  // session's edits; a mid-open nav refresh must not wipe the user's
  // edits either, so the seed runs on the open flip only.
  useEffect(() => {
    if (!open) return;
    setCodes(initialCodes);
    setForcedTables(initialForcedTables);
    setGoalKey(selectedGoalKey);
    setQuestionTouched(false);
    setQuestion(defaultQueryText(initialCodes));
    setInput("");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  /** A codes edit (add/remove): the untouched question follows the new
   *  pick, and the goal selection the new pick rules out falls back to
   *  Auto (the runner re-defaults by scope) — the dialog's plan-goal
   *  radios react to the dialog's own codes, not the page's. */
  const handleCodesChange = (next: ScopeSubjectEntry[]) => {
    setCodes(next);
    if (!questionTouched) setQuestion(defaultQueryText(next));
    setGoalKey((current) => {
      if (current === null) return current;
      const row = goalPrompts.find((p) => p.prompt_key === current);
      if (row === undefined || goalAppliesToScope(row, next.length > 0)) {
        return current;
      }
      return null;
    });
  };

  const hasCode = (code: string) =>
    codes.some((s) => subjectKey(s.code) === subjectKey(code));
  const commitInput = (raw: string) => {
    const code = raw.trim();
    if (code === "" || hasCode(code)) {
      setInput("");
      return;
    }
    setInput("");
    handleCodesChange([...codes, { code }]);
  };

  const hasCodes = codes.length > 0;
  const codesEditable = subjectKind !== undefined;

  // The Plan-goal disabled radios' shared reason (the panel's wording):
  // codes-family rows need named codes, market-family rows the empty
  // pick — which this dialog reads as all codes → the whole market.
  const goalDisabledNote = hasCodes
    ? "needs an empty pick (all codes — whole market)"
    : "needs named codes — the pick is empty (all codes)";

  const confirm = () => {
    onConfirm({
      question: question.trim(),
      codes: codes.map((c) => c.code),
      forcedTables,
      goalKey,
    });
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Generate summary</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 0.5 }}>
          <TextField
            autoFocus
            fullWidth
            size="small"
            multiline
            minRows={2}
            maxRows={5}
            label="Query"
            value={question}
            onChange={(e) => {
              setQuestionTouched(true);
              setQuestion(e.target.value);
            }}
            helperText="The run's focus instruction — defaults to the scope under it; edit freely"
          />
          {codesEditable && (
            <Box>
              <Typography variant="caption" color="text.secondary">
                Scope subjects · {subjectKind} — empty = market-wide
              </Typography>
              <Box
                sx={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 0.5,
                  alignItems: "center",
                  mt: 0.5,
                }}
              >
                {codes.map((s) => (
                  <Chip
                    key={subjectKey(s.code)}
                    label={s.name ? `${s.code} ${s.name}` : s.code}
                    size="small"
                    color="primary"
                    variant="outlined"
                    onDelete={() =>
                      handleCodesChange(
                        codes.filter((c) => subjectKey(c.code) !== subjectKey(s.code)),
                      )
                    }
                  />
                ))}
                {codes.length === 0 && (
                  <Tooltip title="Market-wide — no subject filter; the run covers the whole market">
                    <Chip
                      label="All Market"
                      size="small"
                      disabled
                      variant="outlined"
                      sx={{ fontStyle: "italic" }}
                    />
                  </Tooltip>
                )}
                <Autocomplete
                  freeSolo
                  size="small"
                  disableClearable
                  options={subjectSuggestions.filter((o) => !hasCode(o.code))}
                  getOptionLabel={(o) =>
                    typeof o === "string" ? o : `${o.code} ${o.name}`
                  }
                  filterOptions={(options, state) =>
                    defaultOptionFilter(options, state).slice(0, SUBJECT_OPTION_CAP)
                  }
                  inputValue={input}
                  onInputChange={(_, value) => setInput(value)}
                  onChange={(_, value) => {
                    const code =
                      typeof value === "string" ? value.trim() : value?.code ?? "";
                    if (code !== "") commitInput(code);
                  }}
                  sx={{ minWidth: 150, flexGrow: 1, maxWidth: 240 }}
                  renderInput={(params) => (
                    <TextField
                      {...params}
                      placeholder="Pick or type a code…"
                      variant="standard"
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          commitInput(input);
                        }
                      }}
                    />
                  )}
                />
              </Box>
            </Box>
          )}
          <Box>
            <Typography variant="caption" color="text.secondary">
              Tables — one tick per section of the run's document, and
              the KG serves these tables only
            </Typography>
            <Box sx={{ mt: 0.5 }}>
              <KgTableScopeSelect
                value={forcedTables}
                onChange={setForcedTables}
                helperText="kg_search / kg_expand / the data hints serve only these tables this run"
              />
            </Box>
          </Box>
          {goalPrompts.length > 0 && (
            <Box>
              <Typography variant="caption" color="text.secondary">
                Plan goal — one choice; Auto = the runner defaults by the
                run's scope
              </Typography>
              <RadioGroup
                name="generate-plan-goal"
                value={goalKey ?? ""}
                onChange={(_, value) =>
                  setGoalKey(value === "" ? null : value)
                }
              >
                <FormControlLabel
                  value=""
                  control={<Radio size="small" sx={{ py: 0.5 }} />}
                  label={
                    <Box sx={{ py: 0.5 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        Auto — by scope
                      </Typography>
                      <Typography variant="caption" color="text.secondary">
                        {hasCodes
                          ? "none picked — codes are named, the runner defaults to the named codes"
                          : "none picked — empty pick = all codes, the runner defaults to the whole-market sweep"}
                      </Typography>
                    </Box>
                  }
                />
                {goalPrompts.map((p) => {
                  const applies = goalAppliesToScope(p, hasCodes);
                  return (
                    <FormControlLabel
                      key={p.prompt_key}
                      value={p.prompt_key}
                      disabled={!applies}
                      control={<Radio size="small" sx={{ py: 0.5 }} />}
                      label={
                        <Box sx={{ py: 0.5, minWidth: 0 }}>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {p.title}
                          </Typography>
                          {!applies && (
                            <Typography
                              variant="caption"
                              sx={{ display: "block", fontStyle: "italic" }}
                            >
                              {goalDisabledNote}
                            </Typography>
                          )}
                        </Box>
                      }
                    />
                  );
                })}
              </RadioGroup>
            </Box>
          )}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        {/* ≥1 table required: the ticked tables ARE the run's item list
            (analysisItems — the server rejects an empty list), so a
            table-less confirm would 400 on the API. */}
        <Button
          variant="contained"
          startIcon={<PlayArrow />}
          disabled={forcedTables.length === 0}
          onClick={confirm}
        >
          Generate
        </Button>
      </DialogActions>
    </Dialog>
  );
}
