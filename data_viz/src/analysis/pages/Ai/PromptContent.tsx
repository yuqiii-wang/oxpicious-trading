/**
 * PromptContent — the summary page's main panel in prompt mode: one
 * picked text.ai_prompts row's content shown in place of the generated
 * summary (opened by clicking the prompt's row in the Prompts picker).
 * The header line carries the title with its type / version / prompt_key
 * identity, the note sits beneath it, then the instruction text itself;
 * while editing, the instruction becomes a textarea — the header row's
 * Save (AnalysisSummaryPage) PUTs the text and the prompt's version
 * bumps by one; prompt_key never changes.
 */
import { Box, TextField, Typography } from "@mui/material";
import type { AiPrompt } from "@/lib/api-client/aiSummary";

interface PromptContentProps {
  prompt: AiPrompt;
  editing: boolean;
  draft: string;
  onDraftChange: (value: string) => void;
}

export default function PromptContent({
  prompt,
  editing,
  draft,
  onDraftChange,
}: PromptContentProps) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
        {prompt.title}
        <Box
          component="span"
          sx={{ fontWeight: 400, color: "text.secondary", ml: 1, fontSize: 13 }}
        >
          {prompt.prompt_type_label} · v{prompt.version} · {prompt.prompt_key}
        </Box>
      </Typography>
      {prompt.note && (
        <Typography variant="caption" color="text.secondary">
          {prompt.note}
        </Typography>
      )}
      {editing ? (
        <TextField
          fullWidth
          multiline
          minRows={12}
          maxRows={30}
          value={draft}
          onChange={(e) => onDraftChange(e.target.value)}
          sx={{ mt: 1.5 }}
          helperText="Saving replaces the instruction and bumps the version by one."
        />
      ) : (
        <Box
          sx={{
            mt: 1.5,
            p: 1.5,
            borderRadius: 1,
            bgcolor: "action.hover",
          }}
        >
          <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>
            {prompt.instruction}
          </Typography>
        </Box>
      )}
    </Box>
  );
}
