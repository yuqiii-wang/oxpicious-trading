/**
 * RuleTextField — a MUI TextField wired to a shared FieldRule
 * (./fieldRules): the rule validates the typed text on every keystroke,
 * the field shows error + "must be …" when violated and the rule's (or a
 * per-field override) expected-format hint while valid. The config
 * dialogs use it so every field states and enforces the text its JSON
 * expects without per-field validation code.
 */
import { TextField, type TextFieldProps } from "@mui/material";
import { type FieldRule } from "./fieldRules";

interface RuleTextFieldProps
  extends Omit<TextFieldProps, "error" | "helperText" | "onChange"> {
  rule: FieldRule;
  /** Keystroke-level change — fires with the raw field text. */
  onChange: (value: string) => void;
  /** Replaces the rule's default hint while the value is valid (the
   *  error message always wins over either hint). */
  hint?: string;
}

export default function RuleTextField({
  rule,
  onChange,
  hint,
  value,
  ...textFieldProps
}: RuleTextFieldProps) {
  const err = rule.error(typeof value === "string" ? value : "");
  return (
    <TextField
      {...textFieldProps}
      value={value}
      error={err != null}
      helperText={err ?? hint ?? rule.hint}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
