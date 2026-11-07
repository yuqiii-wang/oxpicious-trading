/**
 * Prompt routes — GET /prompts (the prompts picker's catalog), PUT
 * /prompts/:promptKey (instruction save) and PUT
 * /prompts/:promptKey/labels (labels editor save).
 * Full route docs: see index.ts.
 */

import { Router, type Request, type Response } from "express";
import {
  listAiPrompts,
  updateAiPromptInstruction,
  updateAiPromptLabels,
} from "../../services/ai-prompts.service.js";
import { str } from "./_shared.js";

const router = Router();

// GET /api/ai/prompts — the Prompts picker's catalog (llm.ai_prompts):
// the active rows of every type — the skills + plan rows the picker
// SELECTS into a run's request, plus the tool rows (the model-callable
// tools' schema descriptions, resolved automatically by the runner each
// run) listed for view/edit only (no checkbox, never in the request).
router.get("/prompts", async (_req: Request, res: Response) => {
  try {
    res.json(await listAiPrompts());
  } catch (err) {
    console.error("[ai/prompts] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// PUT /api/ai/prompts/:promptKey — save an edited instruction (the prompt
// view's Edit → Save): the row's text is replaced in place and its version
// bumps by one; responds with the refreshed row, 404 when no active row
// carries the key. The optional { prompt_sub_type } body field narrows the
// match to the one PK row — duplicate-key rows (update_plan's plan_main /
// plan_fork lanes) save independently.
router.put("/prompts/:promptKey", async (req: Request, res: Response) => {
  try {
    const promptKey = str(req.params.promptKey);
    const instruction =
      typeof req.body?.instruction === "string" ? req.body.instruction.trim() : "";
    const promptSubType =
      typeof req.body?.prompt_sub_type === "string"
        ? req.body.prompt_sub_type
        : undefined;
    if (!promptKey) {
      res.status(400).json({ error: "Missing 'prompt_key'" });
      return;
    }
    if (!instruction) {
      res.status(400).json({ error: "Missing 'instruction'" });
      return;
    }
    const row = await updateAiPromptInstruction(promptKey, instruction, promptSubType);
    if (row === null) {
      res.status(404).json({ error: `prompt_key ${promptKey} not found or inactive` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/prompts put] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

// PUT /api/ai/prompts/:promptKey/labels — save the prompt view's Labels
// editor (the bound/optional label chips): the row's two TEXT[] arrays are
// REPLACED wholesale with the posted effective sets (the service trims,
// lowercases the optional group and dedupes), so the client always sends
// the full arrays. Same PK-narrowing { prompt_sub_type } body field as the
// instruction save; responds with the refreshed row, 404 when no active
// row carries the key.
router.put("/prompts/:promptKey/labels", async (req: Request, res: Response) => {
  try {
    const promptKey = str(req.params.promptKey);
    const promptSubType =
      typeof req.body?.prompt_sub_type === "string"
        ? req.body.prompt_sub_type
        : undefined;
    if (!promptKey) {
      res.status(400).json({ error: "Missing 'prompt_key'" });
      return;
    }
    if (!Array.isArray(req.body?.bound_labels) || !Array.isArray(req.body?.optional_labels)) {
      res.status(400).json({ error: "Missing 'bound_labels' / 'optional_labels' arrays" });
      return;
    }
    const row = await updateAiPromptLabels(
      promptKey,
      req.body.bound_labels,
      req.body.optional_labels,
      promptSubType,
    );
    if (row === null) {
      res.status(404).json({ error: `prompt_key ${promptKey} not found or inactive` });
      return;
    }
    res.json(row);
  } catch (err) {
    console.error("[ai/prompts labels put] error:", err);
    res.status(500).json({ error: String(err) });
  }
});

export default router;
