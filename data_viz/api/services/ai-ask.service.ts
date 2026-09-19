/**
 * AI Ask service — bridges POST /api/ai/ask to the llm_agents.llm_ask
 * python package (chart-adviser mode) via the shared WSL py-runner.
 *
 * Transport: the request (question + plot info + base64 screenshots) is
 * written to a JSON file under temp_scripts/ai_ask/ and passed as
 * --payload-file — runPythonModule joins argv into a bash -lc command line,
 * so a multi-hundred-KB base64 screenshot can NOT travel as an argument
 * (Windows 32k command-line limit). The file is relative to the project
 * root with forward slashes, matching the runner's `cd /mnt/e/...` cwd;
 * the python side deletes it after reading.
 *
 * Result contract: the last "@@LLM_ASK_JSON@@{…}" marker of stdout
 * (same pattern as routes/news.ts). Screenshots switch the python side to
 * its configured vision model (glm-4.5v); a text-only model drops them and
 * proceeds text-only — see llm_agents/llm_ask/core/vision.py. Deduped by
 * process-id-tag `ai-ask` — a second ask while one is in flight resolves
 * as already-running.
 */
import { mkdir, unlink, writeFile } from "fs/promises";
import { randomUUID } from "crypto";
import path from "path";
import { fileURLToPath } from "url";
import { runPythonModule } from "./py-runner.service.js";

const LLM_ASK_MARKER = "@@LLM_ASK_JSON@@";
/** ~4 MB of base64 per screenshot — beyond this it is dropped, the ask proceeds. */
const MAX_SCREENSHOT_CHARS = 4 * 1024 * 1024;
/** Screenshots per ask (multi-chart cards) — cap the payload size. */
const MAX_SCREENSHOTS = 4;
/**
 * Repo root (…/data_viz/api/services → up 3), NOT process.cwd(): the payload
 * path handed to python is interpreted relative to the py-runner's WSL cwd
 * (/mnt/e/oxpicious-trading), which must be exactly where the file lands
 * regardless of where the API server itself was started from.
 */
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
/** Absolute on the Node side; a forward-slash relative form goes to python. */
const PAYLOAD_DIR = path.join(REPO_ROOT, "temp_scripts", "ai_ask");
/** temp_scripts/ai_ask — relative to the repo root == the py-runner's WSL cwd. */
const PAYLOAD_DIR_REL = "temp_scripts/ai_ask";

export interface AiAskPayload {
  question: string;
  plotInfo: Record<string, unknown>;
  /** PNG data URLs, one per stacked chart; optional — the ask proceeds without. */
  screenshots?: string[];
  themeMode?: string;
  /** The modal's "online search" tick — the python side then routes
   *  through the online-search agent (web search + cited summary). */
  onlineSearch?: boolean;
  /** Web-search term for the online-search path (the modal's search line,
   *  seeded with chart title + date) — searched instead of the question;
   *  python falls back to the question when absent/blank. */
  searchQuery?: string;
}

export interface AiAskServiceResult {
  success: boolean;
  answer: string;
  model?: string;
  /** stderr/stdout tail when the module failed to produce a marker. */
  stderrTail?: string;
}

export async function askLlm(
  payload: AiAskPayload,
): Promise<AiAskServiceResult> {
  const body: AiAskPayload = {
    ...payload,
    screenshots: (payload.screenshots ?? [])
      .filter((s) => typeof s === "string" && s.length <= MAX_SCREENSHOT_CHARS)
      .slice(0, MAX_SCREENSHOTS),
  };

  await mkdir(PAYLOAD_DIR, { recursive: true });
  const fileName = `ask-${Date.now()}-${randomUUID().slice(0, 8)}.json`;
  const relPath = `${PAYLOAD_DIR_REL}/${fileName}`;
  await writeFile(path.join(PAYLOAD_DIR, fileName), JSON.stringify(body), "utf8");

  try {
    const result = await runPythonModule(
      "llm_agents.llm_ask",
      ["ask", "--payload-file", relPath, "--json"],
      { processIdTag: "ai-ask" },
    );
    if (result.already_running) {
      return {
        success: false,
        answer: "",
        stderrTail: "another AI ask is already running — try again in a moment",
      };
    }

    const idx = result.stdout.lastIndexOf(LLM_ASK_MARKER);
    if (idx >= 0) {
      try {
        const parsed = JSON.parse(
          result.stdout.slice(idx + LLM_ASK_MARKER.length),
        ) as { answer?: string; model?: string };
        if (typeof parsed.answer === "string" && parsed.answer !== "") {
          return {
            success: true,
            answer: parsed.answer,
            model: typeof parsed.model === "string" ? parsed.model : undefined,
          };
        }
        return {
          success: false,
          answer: "",
          stderrTail: "llm_ask reported no answer",
        };
      } catch {
        // fall through to the failure report below
      }
    }
    return {
      success: false,
      answer: "",
      stderrTail:
        result.stderr.slice(-1000) || result.stdout.slice(-1000) || "llm_ask failed",
    };
  } finally {
    // The python side best-effort deletes the file; this is the backstop.
    await unlink(path.join(PAYLOAD_DIR, fileName)).catch(() => {});
  }
}
