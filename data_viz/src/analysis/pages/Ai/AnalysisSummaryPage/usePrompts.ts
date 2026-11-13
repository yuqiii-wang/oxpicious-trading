/** The prompt catalog (llm.ai_prompts) + the picker's selection state
 *  and the Plan-goal radio set. */
import { useEffect, useMemo, useRef, useState } from "react";
import { fetchAiPrompts, type AiPrompt } from "@/lib/api-client/aiSummary";
import { goalAppliesToScope } from "../constants";
import { isSelectablePrompt } from "./helpers";

export function usePrompts(scopeHasCodes: boolean): {
  prompts: AiPrompt[];
  setPrompts: React.Dispatch<React.SetStateAction<AiPrompt[]>>;
  promptsLoading: boolean;
  selectedPrompts: Set<string>;
  setSelectedPrompts: React.Dispatch<React.SetStateAction<Set<string>>>;
  togglePrompt: (key: string, checked: boolean) => void;
  /** The plan-goal radio set (the Plan section's study-scope
   *  directives). */
  goalPrompts: AiPrompt[];
  /** The goal row currently picked (null = Auto) — the Generate
   *  dialog's radio default. */
  selectedGoalKey: string | null;
} {
  // Prompts come from llm.ai_prompts (GET /api/ai/prompts); the picker
  // lists the selectable rows (skills + plan) and the first load selects
  // every one.
  const [prompts, setPrompts] = useState<AiPrompt[]>([]);
  const [promptsLoading, setPromptsLoading] = useState(true);
  const [selectedPrompts, setSelectedPrompts] = useState<Set<string>>(
    () => new Set<string>(),
  );
  const promptsLoadedRef = useRef(false);

  useEffect(() => {
    let cancelled = false;
    fetchAiPrompts()
      .then((rows) => {
        if (cancelled) return;
        setPrompts(rows);
        if (!promptsLoadedRef.current) {
          promptsLoadedRef.current = true;
          // "Select all" first load — EXCEPT the plan-goal sub type:
          // those four study-scope rows are ONE radio choice in the
          // picker (its Auto entry = none picked, the runner defaults
          // by the run's scope), so the seed set starts at Auto
          // instead of checking all four at once.
          setSelectedPrompts(
            new Set(
              rows
                .filter(
                  (p) =>
                    isSelectablePrompt(p) &&
                    !(
                      p.prompt_type_label === "plan" &&
                      p.prompt_sub_type === "goal"
                    ),
                )
                .map((p) => p.prompt_key),
            ),
          );
        }
      })
      .catch(() => {
        if (!cancelled) setPrompts([]);
      })
      .finally(() => {
        if (!cancelled) setPromptsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const togglePrompt = (key: string, checked: boolean) => {
    setSelectedPrompts((prev) => {
      const next = new Set(prev);
      if (checked) {
        next.add(key);
      } else {
        next.delete(key);
      }
      return next;
    });
  };

  // The study-scope goal pick follows the scope pick reactively: a goal
  // row the scope rules out (codes-family on an empty pick, market-family
  // with codes named) cannot stay selected — drop it so the set falls
  // back to Auto and the runner re-defaults by scope. Silent no-op when
  // nothing selected is out of scope.
  useEffect(() => {
    setSelectedPrompts((prev) => {
      const stale = prompts.filter(
        (p) => prev.has(p.prompt_key) && !goalAppliesToScope(p, scopeHasCodes),
      );
      if (stale.length === 0) return prev;
      const next = new Set(prev);
      for (const p of stale) next.delete(p.prompt_key);
      return next;
    });
  }, [prompts, scopeHasCodes]);

  // The plan-goal radio set (the Plan section's study-scope directives)
  // — the Generate dialog's radio options and the exclusive-selection
  // sync target its confirm writes into.
  const goalPrompts = useMemo(
    () =>
      prompts.filter(
        (p) => p.prompt_type_label === "plan" && p.prompt_sub_type === "goal",
      ),
    [prompts],
  );
  // The goal row currently picked in selectedPrompts (null = Auto) —
  // the dialog radio's default pick.
  const selectedGoalKey = useMemo(
    () =>
      goalPrompts.find((p) => selectedPrompts.has(p.prompt_key))?.prompt_key ??
      null,
    [goalPrompts, selectedPrompts],
  );

  return {
    prompts,
    setPrompts,
    promptsLoading,
    selectedPrompts,
    setSelectedPrompts,
    togglePrompt,
    goalPrompts,
    selectedGoalKey,
  };
}
