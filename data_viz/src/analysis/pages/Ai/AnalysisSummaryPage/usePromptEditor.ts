/** The prompt view's open/edit state — the main panel's prompt mode. */
import { useState } from "react";
import {
  promptIdentity,
  updateAiPrompt,
  type AiPrompt,
} from "@/lib/api-client/aiSummary";

export function usePromptEditor(
  prompts: AiPrompt[],
  promptsEffective: AiPrompt[],
  setPrompts: React.Dispatch<React.SetStateAction<AiPrompt[]>>,
  setErrorMsg: React.Dispatch<React.SetStateAction<string | null>>,
  setNoticeMsg: React.Dispatch<React.SetStateAction<string | null>>,
): {
  /** The composite identity (promptIdentity — key|sub_type) of the
   *  opened prompt, null = the summary view. */
  openPromptKey: string | null;
  openPrompt: AiPrompt | null;
  /** The open prompt's persisted baseline — what the labels editor
   *  diffs its session delta against. */
  openBasePrompt: AiPrompt | null;
  editingPrompt: boolean;
  setEditingPrompt: React.Dispatch<React.SetStateAction<boolean>>;
  draftInstruction: string;
  setDraftInstruction: React.Dispatch<React.SetStateAction<string>>;
  savingPrompt: boolean;
  openPromptByKey: (key: string | null) => void;
  startPromptEdit: () => void;
  savePromptEdit: () => Promise<void>;
} {
  // ---- Prompt view (the main panel's prompt mode) ----
  // The opened prompt (clicking its picker row, not the checkbox) — the
  // main panel shows its content with Edit/Save in place of Generate.
  // The state carries the row's composite identity (promptIdentity —
  // key|sub_type): prompt_key alone is not unique (update_plan has one
  // row per lane among the tool rows).
  const [openPromptKey, setOpenPromptKey] = useState<string | null>(null);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [draftInstruction, setDraftInstruction] = useState("");
  const [savingPrompt, setSavingPrompt] = useState(false);

  const openPrompt =
    promptsEffective.find((p) => promptIdentity(p) === openPromptKey) ?? null;
  // The open prompt's persisted baseline — what the labels editor diffs
  // its session delta against (null for the summary view).
  const openBasePrompt =
    prompts.find((p) => promptIdentity(p) === openPromptKey) ?? null;

  // Open/close the prompt content view (clicking its picker row again
  // closes); opening always lands in read mode.
  const openPromptByKey = (key: string | null) => {
    setOpenPromptKey(key);
    setEditingPrompt(false);
    setSavingPrompt(false);
  };

  const startPromptEdit = () => {
    if (!openPrompt) return;
    setDraftInstruction(openPrompt.instruction);
    setEditingPrompt(true);
  };

  // The prompt view's Save: PUT the edited instruction (the row's
  // sub_type rides along so duplicate-key rows — update_plan's lanes —
  // save independently) — the server replaces the row's text in place
  // and bumps its version by one; the refreshed row swaps into state
  // (the picker + the open view follow).
  const savePromptEdit = async () => {
    if (!openPrompt) return;
    setSavingPrompt(true);
    setErrorMsg(null);
    try {
      const updated = await updateAiPrompt(
        openPrompt.prompt_key,
        draftInstruction.trim(),
        openPrompt.prompt_sub_type,
      );
      setPrompts((prev) =>
        prev.map((p) => (promptIdentity(p) === promptIdentity(updated) ? updated : p)),
      );
      setEditingPrompt(false);
      setNoticeMsg(
        `Saved "${updated.title}" — now at version ${updated.version}`,
      );
    } catch (err) {
      setErrorMsg(
        `Failed to save the prompt: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setSavingPrompt(false);
    }
  };

  return {
    openPromptKey,
    openPrompt,
    openBasePrompt,
    editingPrompt,
    setEditingPrompt,
    draftInstruction,
    setDraftInstruction,
    savingPrompt,
    openPromptByKey,
    startPromptEdit,
    savePromptEdit,
  };
}
