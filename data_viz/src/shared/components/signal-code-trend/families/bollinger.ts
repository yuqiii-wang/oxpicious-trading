/**
 * families/bollinger — the bollinger family (signal_type "bollinger"):
 * the display label; the window x k grid carries no record word of its
 * own (the raw sub_type literal — "boll20_2std" — is the record's
 * word).
 */
import type { SignalFamily } from "./index";

export const bollingerFamily: SignalFamily = {
  label: "Bollinger",
};
