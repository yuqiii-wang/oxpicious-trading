/**
 * The Signal Amplification page's amplifier registry — the display
 * metadata for the TWO-LEVEL amplifier axis (2026-11-20): the PARENT
 * TYPE (static.label_dim 'signal_amplifier_type': ohlcv / margin /
 * news — the data domain the anatomy comes from) + each type's SUB
 * TYPES ('signal_amplifier' when ohlcv, 'signal_amplifier_margin'
 * when margin, 'signal_amplifier_news' when news; mirrored from
 * _common/labels.py + label-codes.ts). The Python side owns the
 * bars/sides; this file owns the page's presentation words.
 */
export interface AmplifierMeta {
  /** The SUB-TYPE label (the wire value; static.label_dim
   *  'signal_amplifier' / 'signal_amplifier_margin' /
   *  'signal_amplifier_news'). */
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
  /** The parent type key ("ohlcv" / "margin" / "news"). */
  typeKey: string;
}

export interface AmplifierTypeMeta {
  /** The PARENT TYPE label (static.label_dim 'signal_amplifier_type'). */
  key: string;
  /** The parent type code (0 ohlcv / 1 margin / 2 news). */
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
  {
    key: "news",
    code: 2,
    label: "News State",
    zh: "新闻情绪",
    subs: [
      {
        key: "sent_shock",
        code: 0,
        label: "Sent Shock",
        zh: "情绪冲击",
        side: "buy",
        description:
          "The blended sentiment's trailing z at or below −2.0 on the " +
          "code's own drop leg — the deep capitulation shock; amplifies " +
          "the reversion families' BUY.",
        typeKey: "news",
      },
      {
        key: "tone_panic",
        code: 1,
        label: "Tone Panic",
        zh: "关键词情绪恐慌",
        side: "buy",
        description:
          "The keyword-tone z at or below −1.5 on the drop leg — the " +
          "down-family mention balance surging; amplifies the reversion " +
          "families' BUY.",
        typeKey: "news",
      },
      {
        key: "neg_wave",
        code: 2,
        label: "Neg Wave",
        zh: "负面报道浪潮",
        side: "buy",
        description:
          "The day's strongly-negative-article count at or above 5 on the " +
          "drop leg — the measured strongest single state; amplifies the " +
          "reversion families' BUY.",
        typeKey: "news",
      },
      {
        key: "quiet_cov",
        code: 3,
        label: "Quiet Coverage",
        zh: "报道异常冷清",
        side: "buy",
        description:
          "Abnormally quiet coverage (flow_af at or below −1.0) on the " +
          "drop leg — the honest marginal candidate; a weak BUY " +
          "amplification.",
        typeKey: "news",
      },
      {
        key: "sent_euphoria",
        code: 4,
        label: "Sent Euphoria Divergence",
        zh: "情绪亢奋背离",
        side: "buy",
        description:
          "The sentiment z at or above +1.5 while the code's own curve " +
          "falls — \"good news can't lift a falling market\"; the " +
          "divergence amplifies BUY (the evidence judges).",
        typeKey: "news",
      },
      {
        key: "tone_euphoria",
        code: 5,
        label: "Tone Euphoria Divergence",
        zh: "关键词亢奋背离",
        side: "buy",
        description:
          "The keyword-tone z at or above +1.5 on the drop leg — the " +
          "divergence pair's tone leg; amplifies BUY.",
        typeKey: "news",
      },
      {
        key: "week_neg_wave",
        code: 6,
        label: "Weekly Neg Wave",
        zh: "周度负面浪潮",
        side: "buy",
        description:
          "The week's strongly-negative-article count (5-row sum) at or " +
          "above 10 — the persistent weekly wave; amplifies the reversion " +
          "families' BUY.",
        typeKey: "news",
      },
      {
        key: "week_tone",
        code: 7,
        label: "Weekly Tone Panic",
        zh: "周度情绪恐慌",
        side: "buy",
        description:
          "The week's mean keyword-tone z at or below −1.0 — the quarter " +
          "drift's strongest carrier (stress +5.90%, hit 78%); amplifies " +
          "the reversion families' BUY.",
        typeKey: "news",
      },
      {
        key: "week_shock",
        code: 8,
        label: "Weekly Sent Shock",
        zh: "周度情绪冲击",
        side: "buy",
        description:
          "The week's mean sentiment z at or below −1.0 — the persistent " +
          "weekly shock; amplifies the reversion families' BUY.",
        typeKey: "news",
      },
      {
        key: "shock_wave",
        code: 9,
        label: "Shock × Wave",
        zh: "情绪冲击浪潮",
        side: "buy",
        description:
          "The sentiment shock (z ≤ −1.5) confirmed by the negative-article " +
          "wave (≥ 3) on the drop leg — the interaction beats either leg " +
          "alone; amplifies BUY.",
        typeKey: "news",
      },
      {
        key: "wave_disagree",
        code: 10,
        label: "Wave × Disagreement",
        zh: "情绪冲击分歧",
        side: "buy",
        description:
          "The shock + wave confirmed again by cross-origin disagreement " +
          "(disp_z ≥ 1.0) — the deeper nested claim; amplifies BUY.",
        typeKey: "news",
      },
      {
        key: "tone_disagree",
        code: 11,
        label: "Tone × Disagreement",
        zh: "负面声浪分歧",
        side: "buy",
        description:
          "The keyword-tone panic with high cross-origin disagreement — " +
          "the measured strongest news population (+1.90%, t=7.09, hit " +
          "73%); amplifies BUY.",
        typeKey: "news",
      },
      {
        key: "week_tone_recovery",
        code: 12,
        label: "Weekly Tone Recovery",
        zh: "周度情绪修复",
        side: "sell",
        description:
          "The weekly tone crosses up through 0 after a deep episode " +
          "within the lookback — the one sell side that cleared the bar; " +
          "amplifies the exit-timing families' SELL.",
        typeKey: "news",
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
