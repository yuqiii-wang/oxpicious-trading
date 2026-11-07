/**
 * AI prompts service — the llm.ai_prompts registry (the UI's Prompts
 * picker + the deep agent's searchable prompt set; DDL:
 * database/sql/text/llm/06_ai_prompts.sql, seeds:
 * database/sql/text/llm/static/ai_prompts/). The UI lists the active rows in
 * ordinal/title/version order — nested into prompt_sub_type foldables
 * inside each prompt_type section — and sends the picked prompt_key values
 * in the summary generation request; the deep agent fetches the matching
 * instruction texts itself through its DB tools. The Labels editor saves a
 * row's bound_labels / optional_labels arrays wholesale (the force-load /
 * search vocabulary; updateAiPromptLabels). 10-min TTL — rows change
 * only on a seed/edit, and the edit writes (updateAiPromptInstruction,
 * updateAiPromptLabels) invalidate the cache so the picker reflects the
 * save immediately.
 */
import { queryRows } from "../lib/db.js";
import { querySqlView, sqlFrag } from "../lib/sql-views.js";
import { loadSqlWrite } from "./sql-writes.service.js";
import { cachedRows, invalidateCachedRows } from "./classification-cache.js";
import { labelDecodeSql } from "./label-codes.js";
import type { QueryResultRow } from "pg";

export interface AiPromptRow extends QueryResultRow {
  prompt_key: string;
  prompt_sub_type: string;
  prompt_type: number;
  prompt_type_label: string;
  title: string;
  version: number;
  instruction: string;
  preview: string;
  note: string | null;
  /** Force-load keywords — a run question containing one force-loads the
   *  row alongside the picked prompts (contract: 06_ai_prompts.sql). */
  bound_labels: string[];
  /** Search labels — the prompt's find vocabulary ('{}' = unlabeled). */
  optional_labels: string[];
  ordinal: number;
}

export function listAiPrompts(): Promise<AiPromptRow[]> {
  // The SELECT lives in the sql view
  // data_viz/api/services/ai-prompts/active_prompts.
  return cachedRows("ai-prompts:active", () =>
    querySqlView<AiPromptRow>(
      "data_viz/api/services/ai-prompts/active_prompts",
      {
        prompt_type_decode: sqlFrag(labelDecodeSql("prompt_type", "prompt_type")),
      },
    ),
  );
}

/**
 * Save an edited instruction: the row's text is replaced in place and its
 * version bumps by one (prompt_key stays the stable join key — the deep
 * agent's fetches and the persisted plan payloads all keep pointing at
 * the same row). promptSubType narrows the match to the one
 * (prompt_key, prompt_sub_type) PK row — the UI sends it on every save so
 * duplicate-key rows (update_plan's plan_main / plan_fork lanes) edit
 * independently; absent = the key-only match for callers without the
 * sub_type at hand. Returns the refreshed row, or null when no active row
 * matches (unknown or deactivated = 404 upstream). UPDATE is outside the
 * db.service's read-statement whitelist, so it routes to the primary pool.
 */
export async function updateAiPromptInstruction(
  promptKey: string,
  instruction: string,
  promptSubType?: string,
): Promise<AiPromptRow | null> {
  // The write lives in the sql write action
  // database/sql_writes/data_viz/api/ai_prompts/update_instruction.
  const rows = await queryRows<AiPromptRow>(
    loadSqlWrite("data_viz", "api", "ai_prompts", "update_instruction"),
    [promptKey, instruction, promptSubType ?? null],
  );
  invalidateCachedRows("ai-prompts:active");
  return rows[0] ?? null;
}

/** Normalize one label array: trim, drop empties, dedupe (case-
 *  insensitive), and — for the optional group — lowercase (the contract
 *  keeps optional labels lowercase English/alias phrases; bound labels
 *  keep their case, mostly Chinese terms). */
function normalizeLabels(labels: unknown, lowercase: boolean): string[] {
  if (!Array.isArray(labels)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of labels) {
    if (typeof raw !== "string") continue;
    const value = lowercase ? raw.trim().toLowerCase() : raw.trim();
    if (value === "") continue;
    const dedupeKey = value.toLowerCase();
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);
    out.push(value);
  }
  return out;
}

/**
 * Save a prompt row's label arrays wholesale (the UI's Labels editor —
 * the arrays arrive as the row's FULL effective sets, so this is a plain
 * in-place replace, no merge). promptSubType narrows the match to the
 * one (prompt_key, prompt_sub_type) PK row, exactly like the instruction
 * save; the write invalidates the list cache and returns the refreshed
 * row, or null when no active row matches (404 upstream).
 */
export async function updateAiPromptLabels(
  promptKey: string,
  boundLabels: unknown,
  optionalLabels: unknown,
  promptSubType?: string,
): Promise<AiPromptRow | null> {
  // The write lives in the sql write action
  // database/sql_writes/data_viz/api/ai_prompts/update_labels.
  const rows = await queryRows<AiPromptRow>(
    loadSqlWrite("data_viz", "api", "ai_prompts", "update_labels"),
    [
      promptKey,
      normalizeLabels(boundLabels, false),
      normalizeLabels(optionalLabels, true),
      promptSubType ?? null,
    ],
  );
  invalidateCachedRows("ai-prompts:active");
  return rows[0] ?? null;
}
