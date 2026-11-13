/** The session label overrides (the prompt view's PromptLabels "Just
 *  this run" saves) and the derived effective prompt rows. */
import { useMemo, useState } from "react";
import {
  promptIdentity,
  updateAiPromptLabels,
  type AiPrompt,
  type PromptLabelSets,
} from "@/lib/api-client/aiSummary";
import { normalizeLabel } from "./helpers";

export function usePromptLabels(
  prompts: AiPrompt[],
  setPrompts: React.Dispatch<React.SetStateAction<AiPrompt[]>>,
  setErrorMsg: React.Dispatch<React.SetStateAction<string | null>>,
  setNoticeMsg: React.Dispatch<React.SetStateAction<string | null>>,
): {
  /** The prompts with the session label overrides applied. */
  promptsEffective: AiPrompt[];
  /** The run-scoped overrides in the request's full-set shape. */
  promptLabelOverrides: Record<string, PromptLabelSets>;
  /** The registry's label vocabulary per group — the editor's
   *  suggestion list. */
  labelSuggestions: PromptLabelSets;
  /** The session overrides themselves (the Generate dialog's
   *  sessionModified read). */
  sessionLabels: Record<string, Partial<PromptLabelSets>>;
  labelsFocusSignal: number;
  /** The focus pulse for the "+" button's open-then-focus flow. */
  bumpLabelsFocus: () => void;
  applyLabelChange: (
    identity: string,
    group: keyof PromptLabelSets,
    label: string,
    add: boolean,
    permanent: boolean,
  ) => Promise<void>;
} {
  // ---- Labels (the prompt view's PromptLabels section) ----
  // The "Just this run" saves: prompt identity → per-group effective
  // arrays (only the groups with a session delta — the other group keeps
  // reading its persisted arrays). The Generate request carries them as
  // promptLabels (→ the agent's prompt_labels), and the page unmount —
  // leaving the UI page — forgets them; nothing here is persisted.
  const [sessionLabels, setSessionLabels] = useState<
    Record<string, Partial<PromptLabelSets>>
  >({});

  // The prompts with the session label overrides applied — the picker
  // rows' label counts and the open prompt view read these; `prompts`
  // stays the persisted baseline the editor diffs against.
  const promptsEffective = useMemo(
    () =>
      prompts.map((p) => {
        const override = sessionLabels[promptIdentity(p)];
        if (!override) return p;
        return {
          ...p,
          bound_labels: override.bound ?? p.bound_labels,
          optional_labels: override.optional ?? p.optional_labels,
        };
      }),
    [prompts, sessionLabels],
  );

  // The run-scoped overrides in the request's full-set shape (identity →
  // both arrays) — promptsEffective already merged the partial deltas.
  const promptLabelOverrides = useMemo(() => {
    const out: Record<string, PromptLabelSets> = {};
    for (const p of promptsEffective) {
      if (sessionLabels[promptIdentity(p)] !== undefined) {
        out[promptIdentity(p)] = {
          bound: p.bound_labels,
          optional: p.optional_labels,
        };
      }
    }
    return out;
  }, [promptsEffective, sessionLabels]);

  // The registry's label vocabulary per group — the editor's suggestion
  // list (the scope-bound guides' seeded phrases; a permanently saved
  // custom phrase joins it for the next picker).
  const labelSuggestions = useMemo<PromptLabelSets>(
    () => ({
      bound: [...new Set(promptsEffective.flatMap((p) => p.bound_labels))].sort((a, b) =>
        a.localeCompare(b),
      ),
      optional: [...new Set(promptsEffective.flatMap((p) => p.optional_labels))].sort(
        (a, b) => a.localeCompare(b),
      ),
    }),
    [promptsEffective],
  );

  // One committed Labels-editor change. Permanent: PUT the row's full
  // effective arrays (the server replaces wholesale) and swap the
  // refreshed row into state — the session override entry for THAT GROUP
  // then dissolves (the DB now matches it; the other group's run-only
  // delta survives). Run-only: the arrays land in the session override
  // map (forgotten on page leave; rides the next Generate).
  const applyLabelChange = async (
    identity: string,
    group: keyof PromptLabelSets,
    label: string,
    add: boolean,
    permanent: boolean,
  ) => {
    const base = prompts.find((p) => promptIdentity(p) === identity);
    if (!base) return;
    const value = normalizeLabel(label, group);
    if (value === "") return;
    const override = sessionLabels[identity];
    const current: PromptLabelSets = {
      bound: override?.bound ?? base.bound_labels,
      optional: override?.optional ?? base.optional_labels,
    };
    const next: PromptLabelSets =
      group === "bound"
        ? {
            bound: add
              ? [...new Set([...current.bound, value])]
              : current.bound.filter((l) => l !== value),
            optional: current.optional,
          }
        : {
            bound: current.bound,
            optional: add
              ? [...new Set([...current.optional, value])]
              : current.optional.filter((l) => l !== value),
          };
    if (!permanent) {
      setSessionLabels((prev) => ({ ...prev, [identity]: { ...prev[identity], [group]: next[group] } }));
      return;
    }
    // The permanent save promotes ONLY its own group's effective arrays;
    // the other group rides its persisted baseline so a run-only delta
    // there stays run-only (the endpoint replaces both arrays wholesale).
    const payload: PromptLabelSets = {
      bound: group === "bound" ? next.bound : base.bound_labels,
      optional: group === "optional" ? next.optional : base.optional_labels,
    };
    try {
      const updated = await updateAiPromptLabels(
        base.prompt_key,
        payload,
        base.prompt_sub_type,
      );
      setPrompts((prev) =>
        prev.map((p) =>
          promptIdentity(p) === promptIdentity(updated) ? updated : p,
        ),
      );
      // The saved group's session delta dissolves; the other group's
      // run-only delta stays until its own change or the page leave.
      setSessionLabels((prev) => {
        const entry = { ...prev[identity] };
        delete entry[group];
        const rest = { ...prev };
        if (Object.keys(entry).length > 0) {
          rest[identity] = entry;
        } else {
          delete rest[identity];
        }
        return rest;
      });
      setNoticeMsg(`Saved labels for "${updated.title}"`);
    } catch (err) {
      setErrorMsg(
        `Failed to save the labels: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  };

  // The picker row's "+" label button's focus pulse — PromptLabels
  // scrolls itself into view and focuses the bound group's add control
  // on the bump (the open itself lives in the page's openPromptLabels).
  const [labelsFocusSignal, setLabelsFocusSignal] = useState(0);
  const bumpLabelsFocus = () => setLabelsFocusSignal((s) => s + 1);

  return {
    promptsEffective,
    promptLabelOverrides,
    labelSuggestions,
    sessionLabels,
    labelsFocusSignal,
    applyLabelChange,
    bumpLabelsFocus,
  };
}
