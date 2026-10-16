/**
 * AnalysisAdviceButton — the "A" rendered beside the population toggle
 * strip (right after the pass: group). The ANALYSIS-WIDE shared Analysis
 * Advice surface: it owns the dialog and fetches the advice registered
 * for its (secType, code, signalType) context (GET /api/analysis-advice →
 * analysis.analysis_advice), rendering the concise significant /
 * insignificant text, the implications, and the DYNAMIC justification
 * tables that ground the analysis.
 *
 * Hidden while nothing is registered for the context (load or empty) —
 * the icon promises advice, so it only appears where advice exists
 * (fetchJson's cache keeps the probe to one request per context).
 */
import { useEffect, useState } from "react";
import {
  Box,
  Dialog,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Stack,
  Typography,
} from "@mui/material";
import {
  fetchAnalysisAdvice,
  type AdviceImplication,
  type AnalysisAdvice,
} from "@/lib/api-client";
import { SIGNAL_TYPE_LABELS } from "@/shared/components/signal-code-trend/families";
import AdviceJustificationTable from "./AdviceJustificationTable";

/** Labels for the significance bundle's known keys; unknown keys render
 *  with their own name (title-cased). */
const SIGNIFICANCE_LABELS: Record<string, string> = {
  significant: "Significant",
  insignificant: "Insignificant",
  basis: "Evidence basis",
};

const titleCase = (key: string): string =>
  key.charAt(0).toUpperCase() + key.slice(1).replace(/_/g, " ");

/** The significance bundle's display order — canonical reading order
 *  (definition first, evidence last); jsonb does NOT preserve key order
 *  (it sorts by length), so the renderer imposes it. Unknown keys follow
 *  in their arrival order. */
const SIGNIFICANCE_ORDER = ["significant", "insignificant", "basis"];

const orderedSignificance = (
  sig: Record<string, unknown>,
): Array<[string, string]> =>
  Object.entries(sig ?? {})
    .filter(([, v]) => typeof v === "string" && v !== "")
    .sort((x, y) => {
      const ix = SIGNIFICANCE_ORDER.indexOf(x[0]);
      const iy = SIGNIFICANCE_ORDER.indexOf(y[0]);
      return (ix < 0 ? SIGNIFICANCE_ORDER.length : ix)
        - (iy < 0 ? SIGNIFICANCE_ORDER.length : iy);
    }) as Array<[string, string]>;

export interface AnalysisAdviceButtonProps {
  secType: string;
  code: string;
  /** The analysis item / signal family the advice explains (the Signal
   *  picker value — 'mov_rsi' / 'bollinger' / 'ma_cross' / ...). */
  signalType: string;
}

export default function AnalysisAdviceButton({
  secType,
  code,
  signalType,
}: AnalysisAdviceButtonProps) {
  const [open, setOpen] = useState(false);
  const [advice, setAdvice] = useState<AnalysisAdvice[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setAdvice(null);
    setError(null);
    fetchAnalysisAdvice(signalType, secType, code)
      .then((r) => {
        if (!cancelled) setAdvice(r.advice);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [signalType, secType, code]);

  // no advice registered for this context (or the probe failed) — no icon
  if (error != null || advice == null || advice.length === 0) return null;

  const label = SIGNAL_TYPE_LABELS[signalType] ?? signalType;

  return (
    <>
      <IconButton
        size="small"
        aria-label={`Analysis advice: ${label}`}
        title="Analysis Advice"
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        sx={{
          ml: 0.25,
          p: "1px 5px",
          borderRadius: "4px",
          border: "1px solid",
          borderColor: "divider",
          color: open ? "primary.main" : "var(--chart-subtitle)",
        }}
      >
        <Typography sx={{ fontSize: "0.75rem", fontWeight: 700, lineHeight: 1 }}>
          A
        </Typography>
      </IconButton>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth="md"
        fullWidth
        scroll="paper"
      >
        <DialogTitle sx={{ pb: 1 }}>
          Analysis Advice — {label}
        </DialogTitle>
        <DialogContent dividers>
          {advice.map((a, i) => (
            <Box key={`${a.analysis_item}|${a.code}|${a.custom_text}`}>
              {i > 0 && <Divider sx={{ my: 2 }} />}
              <Typography
                variant="caption"
                sx={{ color: "text.secondary" }}
                component="div"
              >
                {SIGNAL_TYPE_LABELS[a.analysis_item] ?? a.analysis_item}
                {` · ${a.sec_type}`}
                {a.industry ? ` · ${a.industry}` : ""}
                {a.code ? ` · ${a.code}` : ""}
                {` · as of ${a.as_of_date}`}
              </Typography>

              <Typography variant="subtitle2" sx={{ mt: 1.5, mb: 0.5 }}>
                What significant / insignificant means here
              </Typography>
              {orderedSignificance(a.significance).map(([k, v]) => (
                  <Typography
                    key={k}
                    variant="body2"
                    sx={{ fontSize: "0.8rem", mb: 0.75 }}
                  >
                    <Box component="span" sx={{ fontWeight: 600 }}>
                      {SIGNIFICANCE_LABELS[k] ?? titleCase(k)}:{" "}
                    </Box>
                    <Box component="span" sx={{ color: "text.secondary" }}>
                      {v as string}
                    </Box>
                  </Typography>
                ))}

              {(a.implications?.length ?? 0) > 0 && (
                <>
                  <Typography variant="subtitle2" sx={{ mt: 1.5, mb: 0.5 }}>
                    Implications
                  </Typography>
                  <Stack spacing={0.75}>
                    {a.implications.map((imp: AdviceImplication, j) =>
                      typeof imp === "string" ? (
                        <Typography
                          key={j}
                          variant="body2"
                          sx={{ fontSize: "0.8rem" }}
                        >
                          • {imp}
                        </Typography>
                      ) : (
                        <Typography
                          key={j}
                          variant="body2"
                          sx={{ fontSize: "0.8rem" }}
                        >
                          <Box component="span" sx={{ fontWeight: 600 }}>
                            {imp.title ?? "—"}
                          </Box>
                          <Box
                            component="span"
                            sx={{ color: "text.secondary" }}
                          >
                            {imp.title ? " — " : "• "}
                            {imp.text ?? ""}
                          </Box>
                        </Typography>
                      ),
                    )}
                  </Stack>
                </>
              )}

              {(a.justification?.length ?? 0) > 0 && (
                <>
                  <Typography variant="subtitle2" sx={{ mt: 1.5, mb: 0.5 }}>
                    Justification — the data behind the text
                  </Typography>
                  <Stack spacing={1.5}>
                    {a.justification.map((t, j) => (
                      <Box key={j}>
                        <Typography
                          sx={{ fontSize: "0.78rem", fontWeight: 600, mb: 0.25 }}
                        >
                          {t.title}
                        </Typography>
                        {t.note ? (
                          <Typography
                            variant="caption"
                            sx={{ display: "block", mb: 0.5, color: "text.secondary" }}
                          >
                            {t.note}
                          </Typography>
                        ) : null}
                        <AdviceJustificationTable table={t} />
                      </Box>
                    ))}
                  </Stack>
                </>
              )}
            </Box>
          ))}
        </DialogContent>
      </Dialog>
    </>
  );
}
