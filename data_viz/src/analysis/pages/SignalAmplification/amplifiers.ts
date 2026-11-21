/**
 * The Signal Amplification page's amplifier registry — the display
 * metadata for the TWO-LEVEL amplifier axis (2026-11-20): the PARENT
 * TYPE (static.label_dim 'signal_amplifier_type': ohlcv / margin — the
 * data domain the anatomy comes from) + each type's SUB TYPES
 * ('signal_amplifier' when ohlcv, 'signal_amplifier_margin' when
 * margin; mirrored from _common/labels.py + label-codes.ts). The
 * Python side owns the bars/sides; this file owns the page's
 * presentation words.
 */
export interface AmplifierMeta {
  /** The SUB-TYPE label (the wire value; static.label_dim
   *  'signal_amplifier' / 'signal_amplifier_margin'). */
  key: string;
  /** The sub-type's code in its parent's domain (the evidence-map key
   *  — resolve together with the parent type code). */
  code: number;
  /** Short English display word. */
  label: string;
  /** The Chinese anatomy word. */
  zh: string;
  /** The side the amplifier amplifies (buy / sell / discount). */
  side: "buy" | "sell" | "discount";
  /** One-line meaning (the anatomy shape + what it does). */
  description: string;
  /** The parent type key ("ohlcv" / "margin"). */
  typeKey: string;
}

export interface AmplifierTypeMeta {
  /** The PARENT TYPE label (static.label_dim 'signal_amplifier_type'). */
  key: string;
  /** The parent type code (0 ohlcv / 1 margin). */
  code: number;
  /** Short English display word for the type. */
  label: string;
  /** The Chinese type word. */
  zh: string;
  subs: AmplifierMeta[];
}

export const AMPLIFIER_TYPES: AmplifierTypeMeta[] = [
  {
    key: "ohlcv",
    code: 0,
    label: "OHLCV Anatomy",
    zh: "K线形态",
    subs: [
      {
        key: "flush_washout",
        code: 0,
        label: "Flush Washout",
        zh: "探底洗出",
        side: "buy",
        description:
          "Lower-wick top-decile flush (探底回升) on a down close with heavy " +
          "participation — the capitulation washout; amplifies the reversion " +
          "families' BUY.",
        typeKey: "ohlcv",
      },
      {
        key: "gap_reclaim",
        code: 1,
        label: "Gap Reclaim",
        zh: "低开收复",
        side: "buy",
        description:
          "Down open (≤ −0.5%) reclaimed by the close — the intraday reversal " +
          "of an overnight flush; amplifies the reversion families' BUY.",
        typeKey: "ohlcv",
      },
      {
        key: "breakout_confirm",
        code: 2,
        label: "Breakout Confirm",
        zh: "放量突破确认",
        side: "buy",
        description:
          "Close above the prior day's high with heavy participation and a " +
          "strong close — the volume-confirmed breakout; amplifies the " +
          "continuation families' BUY.",
        typeKey: "ohlcv",
      },
      {
        key: "spike_blowoff",
        code: 3,
        label: "Spike Blowoff",
        zh: "冲高回落放量",
        side: "sell",
        description:
          "Upper-wick top-decile spike (冲高回落) faded into the bottom 30% of " +
          "the range on heavy participation — the blow-off; amplifies the " +
          "exit-timing families' SELL.",
        typeKey: "ohlcv",
      },
      {
        key: "exhaustion_gap",
        code: 4,
        label: "Exhaustion Gap",
        zh: "高开低走竭尽",
        side: "sell",
        description:
          "Up open (≥ +0.5%) on a red body — the exhaustion gap that failed " +
          "intraday; amplifies the exit-timing families' SELL.",
        typeKey: "ohlcv",
      },
      {
        key: "dry_rebound",
        code: 5,
        label: "Dry Rebound",
        zh: "缩量弱反弹",
        side: "discount",
        description:
          "Up close on shrinking participation (amt ratio ≤ 0.8) — the " +
          "fade-prone rebound; DISCOUNTS the bound families' BUY (negative " +
          "bp).",
        typeKey: "ohlcv",
      },
    ],
  },
  {
    key: "margin",
    code: 1,
    label: "Margin Level",
    zh: "融资余额",
    subs: [
      {
        key: "margin_washout",
        code: 0,
        label: "Margin Washout",
        zh: "融资余额低位",
        side: "buy",
        description:
          "Margin balance (融资余额) at or below its own trailing-252d q10 " +
          "bar — leverage washed out; low margin means low risk and a lower " +
          "price, amplifying the reversion families' BUY.",
        typeKey: "margin",
      },
      {
        key: "margin_crowding",
        code: 1,
        label: "Margin Crowding",
        zh: "融资余额高位",
        side: "sell",
        description:
          "Margin balance (融资余额) at or above its own trailing-252d q90 " +
          "bar — leverage crowding and fragility; amplifies the exit-timing " +
          "families' SELL.",
        typeKey: "margin",
      },
    ],
  },
];

/** The flat sub-type roster (both parent types, type order preserved). */
export const AMPLIFIERS: AmplifierMeta[] =
  AMPLIFIER_TYPES.flatMap((t) => t.subs);

const BY_KEY = new Map(AMPLIFIERS.map((a) => [a.key, a]));

export function amplifierMeta(key: string): AmplifierMeta | undefined {
  return BY_KEY.get(key);
}

const BY_TYPE_AND_CODE = new Map(
  AMPLIFIERS.map((a) => [`${a.typeKey}:${a.code}`, a]),
);

/** Look a sub type up by its parent type label + the sub code (the
 *  evidence map's nested key pair — sub codes are per-parent). */
export function amplifierMetaByCode(
  typeKey: string,
  code: number,
): AmplifierMeta | undefined {
  return BY_TYPE_AND_CODE.get(`${typeKey}:${code}`);
}

const TYPE_BY_CODE = new Map(AMPLIFIER_TYPES.map((t) => [t.code, t]));

export function amplifierTypeByCode(
  code: number,
): AmplifierTypeMeta | undefined {
  return TYPE_BY_CODE.get(code);
}
