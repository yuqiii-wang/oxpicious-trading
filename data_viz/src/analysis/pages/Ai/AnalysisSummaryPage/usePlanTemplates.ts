/** The saved plan templates (llm.deep_agent_plan_templates) — the
 *  picker's option list, the picked id, and the refresh/delete actions. */
import { useCallback, useEffect, useState } from "react";
import {
  deletePlanTemplate,
  fetchPlanTemplates,
  type AiPlanTemplate,
} from "@/lib/api-client/aiPlanTemplates";

export function usePlanTemplates(
  setErrorMsg: React.Dispatch<React.SetStateAction<string | null>>,
): {
  planTemplates: AiPlanTemplate[];
  planTemplateId: number | null;
  setPlanTemplateId: (id: number | null) => void;
  refreshPlanTemplates: () => void;
  removePlanTemplate: (templateId: number) => Promise<void>;
} {
  // ---- Saved plan templates (llm.deep_agent_plan_templates) ----
  // A template picked here starts the run's plan from the saved
  // placeholder-templated plan (zero planning LLM calls); null = the
  // normal LLM planning session.
  const [planTemplates, setPlanTemplates] = useState<AiPlanTemplate[]>([]);
  const [planTemplateId, setPlanTemplateId] = useState<number | null>(null);
  const refreshPlanTemplates = useCallback(() => {
    fetchPlanTemplates()
      // Only TYPE-PLAN rows are loadable as a plan (the renamed
      // langgraph-templates table also carries the seeded type-result
      // review-wave row — listing it in the picker would only send the
      // daemon a template it must fail-soft refuse).
      .then((rows) => setPlanTemplates(rows.filter((t) => t.type === 0)))
      .catch(() => setPlanTemplates([]));
  }, []);
  useEffect(() => {
    refreshPlanTemplates();
  }, [refreshPlanTemplates]);
  const removePlanTemplate = async (templateId: number) => {
    try {
      await deletePlanTemplate(templateId);
    } catch (err) {
      setErrorMsg(String(err instanceof Error ? err.message : err));
      return;
    }
    setPlanTemplateId((current) =>
      current === templateId ? null : current,
    );
    refreshPlanTemplates();
  };
  return {
    planTemplates,
    planTemplateId,
    setPlanTemplateId,
    refreshPlanTemplates,
    removePlanTemplate,
  };
}
