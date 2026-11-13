/** The run-limits fetch/save state — the mount fetch, the draft
 *  editing, the dirty check and the whole-entry PUT save. */
import { useEffect, useMemo, useState } from "react";
import {
  fetchDeepAgentLimits,
  saveDeepAgentLimits,
  type DeepAgentLimits,
} from "@/lib/api-client/config";
import { ALL_FIELDS, draftOf, type NumericLimitKey } from "./limitFields";

export function useRunLimits() {
  const [limits, setLimits] = useState<DeepAgentLimits | null>(null);
  const [draft, setDraft] = useState<Record<NumericLimitKey, string> | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    fetchDeepAgentLimits()
      .then((res) => {
        if (!alive) return;
        setLimits(res.limits);
        setDraft(draftOf(res.limits));
      })
      .catch((err) => alive && setError(String(err)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const dirty = useMemo(() => {
    if (!limits || !draft) return false;
    return ALL_FIELDS.some((f) => {
      const current = limits[f.key];
      const text = draft[f.key].trim();
      if (f.nullable) {
        return text === "" ? current !== null : Number(text) !== current;
      }
      return text === "" || Number(text) !== current;
    });
  }, [limits, draft]);

  const save = async () => {
    if (!limits || !draft) return;
    setSaving(true);
    setError(null);
    try {
      const payload = { ...limits };
      for (const f of ALL_FIELDS) {
        const text = draft[f.key].trim();
        (payload[f.key] as number | null) =
          text === "" && f.nullable ? null : Number(text);
      }
      const saved = await saveDeepAgentLimits(payload);
      setLimits(saved.limits);
      setDraft(draftOf(saved.limits));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return { limits, draft, setDraft, loading, saving, error, setError, dirty, save };
}
