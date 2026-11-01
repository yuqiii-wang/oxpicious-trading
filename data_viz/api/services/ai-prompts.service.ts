/**
 * AI prompts service — the text.ai_prompts registry (the UI's Skills
 * picker + the deep agent's searchable prompt set; DDL + seed:
 * database/sql/text/08_ai_prompts.sql). The UI lists the active rows in
 * ordinal/title/version order and sends the picked prompt_key values in the
 * summary generation request; the deep agent fetches the matching instruction
 * texts itself through its DB tools. 10-min TTL — rows change only on a
 * seed/edit, and the edit write (updateAiPromptInstruction) invalidates the
 * cache so the picker reflects the save immediately.
 */
import { queryRows } from "../lib/db.js";
import { cachedRows, invalidateCachedRows } from "./classification-cache.js";
import { labelDecodeSql } from "./label-codes.js";
import type { QueryResultRow } from "pg";

export interface AiPromptRow extends QueryResultRow {
  prompt_key: string;
  prompt_type: number;
  prompt_type_label: string;
  title: string;
  version: number;
  instruction: string;
  note: string | null;
  ordinal: number;
}

export function listAiPrompts(): Promise<AiPromptRow[]> {
  return cachedRows("ai-prompts:active", () =>
    queryRows<AiPromptRow>(
      `SELECT prompt_key,
              prompt_type,
              ${labelDecodeSql("prompt_type", "prompt_type")} AS prompt_type_label,
              title, version, instruction, note, ordinal
         FROM text.ai_prompts
        WHERE is_active
        ORDER BY ordinal, title, version, prompt_key`,
    ),
  );
}

/**
 * Save an edited instruction: the row's text is replaced in place and its
 * version bumps by one (prompt_key stays the stable join key — the UI's
 * picked keys, the deep agent's fetches and the persisted plan payloads all
 * keep pointing at the same row). Returns the refreshed row, or null when
 * no ACTIVE row carries the key (unknown or deactivated key = 404 upstream).
 * UPDATE is outside the db.service's read-statement whitelist, so it routes
 * to the primary pool.
 */
export async function updateAiPromptInstruction(
  promptKey: string,
  instruction: string,
): Promise<AiPromptRow | null> {
  const rows = await queryRows<AiPromptRow>(
    `UPDATE text.ai_prompts
        SET instruction = $2,
            version = version + 1,
            updated_at = now()
      WHERE prompt_key = $1
        AND is_active
      RETURNING prompt_key,
                prompt_type,
                ${labelDecodeSql("prompt_type", "prompt_type")} AS prompt_type_label,
                title, version, instruction, note, ordinal`,
    [promptKey, instruction],
  );
  invalidateCachedRows("ai-prompts:active");
  return rows[0] ?? null;
}
