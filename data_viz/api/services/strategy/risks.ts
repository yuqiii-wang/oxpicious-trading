/**
 * Risk metrics — reads pre-computed risk rows from strategy.strategy_risks
 * + strategy.strategy_risk_period + strategy.strategy_risk_factors
 * (computed by `python -m strategy._risks`).
 */
import { queryRows, formatDate, toNum } from "../db.service.js";
import type {
  MaSpreadSecType,
  StrategyRiskSeq,
  StrategyRiskPeriod,
  StrategyRiskResponse,
  StrategyRiskFactor,
} from "../../../shared/types.js";
import { DEFAULT_STRATEGY_NAME } from "./_shared.js";

interface RiskSeqRow {
  seq_id: number;
  code: string;
  total_realized_pnl: string | number;
  total_abs_pnl: string | number;
  n_sells: number;
  n_buys: number;
  pnl_gain_1st_decision_no: number | null;
  pnl_gain_2nd_decision_no: number | null;
  pnl_gain_3rd_decision_no: number | null;
  pnl_loss_1st_decision_no: number | null;
  pnl_loss_2nd_decision_no: number | null;
  pnl_loss_3rd_decision_no: number | null;
  confidence_buy_1st_decision_no: number | null;
  confidence_buy_2nd_decision_no: number | null;
  confidence_buy_3rd_decision_no: number | null;
  // Derived via LEFT JOIN to trade_decision (1st gain / 1st loss details)
  top_gain_pnl: string | number | null;
  top_gain_exec_date: string | null;
  top_gain_signal_reason: string | null;
  top_loss_pnl: string | number | null;
  top_loss_exec_date: string | null;
  top_loss_signal_reason: string | null;
  max_30d_abs_pnl: string | number | null;
  concentration_ratio: string | number | null;
  concentration_window_start: string | null;
  concentration_window_end: string | null;
  drawdown_1st_date: string | null;
  drawdown_2nd_date: string | null;
  drawdown_3rd_date: string | null;
  drawdown_1st_val: string | number | null;
  drawdown_2nd_val: string | number | null;
  drawdown_3rd_val: string | number | null;
  risk_score: string | number | null;
  risk_grade: string | null;
  ft_amplified_total_pnl: string | number | null;
  deepest_drop_since_unzero_pos: string | number | null;
  deepest_drop_since_unzero_pos_peak_date: string | null;
  deepest_drop_since_unzero_pos_trough_date: string | null;
  deepest_drop_since_last_buy: string | number | null;
  deepest_drop_since_last_buy_peak_date: string | null;
  deepest_drop_since_last_buy_trough_date: string | null;
}

interface RiskPeriodRow {
  seq_id: number;
  code: string;
  period_type: string;
  period_value: string;
  n_sells: number;
  n_buys: number;
  realized_pnl: string | number;
  ft_amplified_pnl: string | number;
  unrealized_pnl: string | number;
  max_loss_unrealized_pnl: string | number;
  max_gain_unrealized_pnl: string | number;
  end_unrealized_pnl: string | number;
  abs_pnl: string | number;
  period_share: string | number | null;
  is_concentration_hotspot: boolean;
  is_counter_trend: boolean;
}

interface RiskFactorRow {
  seq_id: number;
  code: string;
  component: string;
  label: string;
  sub_key: string;
  contribution: string | number;
  raw_value: string | number | null;
  threshold: string | number | null;
  ratio: string | number | null;
}

const RISK_SEQ_SQL = `
  SELECT r.seq_id, r.code,
         i.total_realized_pnl, i.total_abs_pnl, i.n_sells, i.n_buys,
         r.pnl_gain_1st_decision_no, r.pnl_gain_2nd_decision_no, r.pnl_gain_3rd_decision_no,
         r.pnl_loss_1st_decision_no, r.pnl_loss_2nd_decision_no, r.pnl_loss_3rd_decision_no,
         r.confidence_buy_1st_decision_no, r.confidence_buy_2nd_decision_no, r.confidence_buy_3rd_decision_no,
         g1.realized_pnl  AS top_gain_pnl,
         g1.exec_date     AS top_gain_exec_date,
         g1.signal_reason AS top_gain_signal_reason,
         l1.realized_pnl  AS top_loss_pnl,
         l1.exec_date     AS top_loss_exec_date,
         l1.signal_reason AS top_loss_signal_reason,
         r.max_30d_abs_pnl, r.concentration_ratio,
         r.concentration_window_start, r.concentration_window_end,
         r.drawdown_1st_date, r.drawdown_2nd_date, r.drawdown_3rd_date,
         r.drawdown_1st_val, r.drawdown_2nd_val, r.drawdown_3rd_val,
         r.risk_score, r.risk_grade,
         r.ft_amplified_total_pnl,
         r.deepest_drop_since_unzero_pos,
         r.deepest_drop_since_unzero_pos_peak_date,
         r.deepest_drop_since_unzero_pos_trough_date,
         r.deepest_drop_since_last_buy,
         r.deepest_drop_since_last_buy_peak_date,
         r.deepest_drop_since_last_buy_trough_date
  FROM strategy.strategy_risks r
  JOIN strategy.strategy_identity s ON s.seq_id = r.seq_id
  JOIN strategy.strategy_results i ON i.seq_id = r.seq_id
  LEFT JOIN strategy.trade_decision g1
    ON g1.seq_id = r.seq_id AND g1.decision_no = r.pnl_gain_1st_decision_no
  LEFT JOIN strategy.trade_decision l1
    ON l1.seq_id = r.seq_id AND l1.decision_no = r.pnl_loss_1st_decision_no
  WHERE s.sec_type = $1 AND r.code = $2 AND s.strategy_name = $3
  ORDER BY CASE WHEN s.is_active THEN 0 ELSE 1 END, s.seq_no DESC
  LIMIT 1
`;

const RISK_PERIODS_SQL = `
  SELECT p.seq_id, p.code, p.period_type, p.period_value,
         p.n_sells, p.n_buys, p.realized_pnl, p.ft_amplified_pnl, p.unrealized_pnl,
         p.max_loss_unrealized_pnl, p.max_gain_unrealized_pnl, p.end_unrealized_pnl,
         p.abs_pnl, p.period_share,
         p.is_concentration_hotspot, p.is_counter_trend
  FROM strategy.strategy_risk_period p
  JOIN strategy.strategy_identity s ON s.seq_id = p.seq_id
  WHERE s.sec_type = $1 AND p.code = $2 AND s.seq_id = $3
  ORDER BY p.period_type, p.period_value
`;

const RISK_FACTORS_SQL = `
  SELECT f.seq_id, f.code, f.component, f.label, f.sub_key,
         f.contribution, f.raw_value, f.threshold, f.ratio
  FROM strategy.strategy_risk_factors f
  WHERE f.seq_id = $1 AND f.code = $2
  ORDER BY
    CASE f.component
      WHEN 'realized' THEN 1
      WHEN 'unrealized' THEN 2
      WHEN 'streak' THEN 3
      WHEN 'period_asymmetry' THEN 4
      WHEN 'period_tail' THEN 5
      ELSE 9
    END,
    f.sub_key
`;

function mapRiskSeq(r: RiskSeqRow): StrategyRiskSeq {
  return {
    seq_id: r.seq_id,
    code: r.code,
    total_realized_pnl: toNum(r.total_realized_pnl) ?? 0,
    total_abs_pnl: toNum(r.total_abs_pnl) ?? 0,
    n_sells: r.n_sells,
    n_buys: r.n_buys,
    pnl_gain_1st_decision_no: r.pnl_gain_1st_decision_no,
    pnl_gain_2nd_decision_no: r.pnl_gain_2nd_decision_no,
    pnl_gain_3rd_decision_no: r.pnl_gain_3rd_decision_no,
    pnl_loss_1st_decision_no: r.pnl_loss_1st_decision_no,
    pnl_loss_2nd_decision_no: r.pnl_loss_2nd_decision_no,
    pnl_loss_3rd_decision_no: r.pnl_loss_3rd_decision_no,
    confidence_buy_1st_decision_no: r.confidence_buy_1st_decision_no,
    confidence_buy_2nd_decision_no: r.confidence_buy_2nd_decision_no,
    confidence_buy_3rd_decision_no: r.confidence_buy_3rd_decision_no,
    // Derived via LEFT JOIN to trade_decision
    top_gain_pnl: toNum(r.top_gain_pnl),
    top_gain_exec_date: r.top_gain_exec_date ? formatDate(r.top_gain_exec_date) : null,
    top_gain_signal_reason: r.top_gain_signal_reason,
    top_loss_pnl: toNum(r.top_loss_pnl),
    top_loss_exec_date: r.top_loss_exec_date ? formatDate(r.top_loss_exec_date) : null,
    top_loss_signal_reason: r.top_loss_signal_reason,
    max_30d_abs_pnl: toNum(r.max_30d_abs_pnl),
    concentration_ratio: toNum(r.concentration_ratio),
    concentration_window_start: r.concentration_window_start ? formatDate(r.concentration_window_start) : null,
    concentration_window_end: r.concentration_window_end ? formatDate(r.concentration_window_end) : null,
    drawdown_1st_date: r.drawdown_1st_date ? formatDate(r.drawdown_1st_date) : null,
    drawdown_2nd_date: r.drawdown_2nd_date ? formatDate(r.drawdown_2nd_date) : null,
    drawdown_3rd_date: r.drawdown_3rd_date ? formatDate(r.drawdown_3rd_date) : null,
    drawdown_1st_val: toNum(r.drawdown_1st_val),
    drawdown_2nd_val: toNum(r.drawdown_2nd_val),
    drawdown_3rd_val: toNum(r.drawdown_3rd_val),
    risk_score: toNum(r.risk_score),
    risk_grade: (r.risk_grade as StrategyRiskSeq["risk_grade"]) ?? null,
    ft_amplified_total_pnl: toNum(r.ft_amplified_total_pnl),
    deepest_drop_since_unzero_pos: toNum(r.deepest_drop_since_unzero_pos),
    deepest_drop_since_unzero_pos_peak_date: r.deepest_drop_since_unzero_pos_peak_date ? formatDate(r.deepest_drop_since_unzero_pos_peak_date) : null,
    deepest_drop_since_unzero_pos_trough_date: r.deepest_drop_since_unzero_pos_trough_date ? formatDate(r.deepest_drop_since_unzero_pos_trough_date) : null,
    deepest_drop_since_last_buy: toNum(r.deepest_drop_since_last_buy),
    deepest_drop_since_last_buy_peak_date: r.deepest_drop_since_last_buy_peak_date ? formatDate(r.deepest_drop_since_last_buy_peak_date) : null,
    deepest_drop_since_last_buy_trough_date: r.deepest_drop_since_last_buy_trough_date ? formatDate(r.deepest_drop_since_last_buy_trough_date) : null,
  };
}

function mapRiskPeriod(r: RiskPeriodRow): StrategyRiskPeriod {
  return {
    seq_id: r.seq_id,
    code: r.code,
    period_type: r.period_type as StrategyRiskPeriod["period_type"],
    period_value: r.period_value,
    n_sells: r.n_sells,
    n_buys: r.n_buys,
    realized_pnl: toNum(r.realized_pnl) ?? 0,
    ft_amplified_pnl: toNum(r.ft_amplified_pnl) ?? 0,
    unrealized_pnl: toNum(r.unrealized_pnl) ?? 0,
    max_loss_unrealized_pnl: toNum(r.max_loss_unrealized_pnl) ?? 0,
    max_gain_unrealized_pnl: toNum(r.max_gain_unrealized_pnl) ?? 0,
    end_unrealized_pnl: toNum(r.end_unrealized_pnl) ?? 0,
    abs_pnl: toNum(r.abs_pnl) ?? 0,
    period_share: toNum(r.period_share),
    is_concentration_hotspot: r.is_concentration_hotspot,
    is_counter_trend: r.is_counter_trend,
  };
}

function mapRiskFactor(r: RiskFactorRow): StrategyRiskFactor {
  return {
    seq_id: r.seq_id,
    code: r.code,
    component: r.component as StrategyRiskFactor["component"],
    label: r.label,
    sub_key: r.sub_key,
    contribution: toNum(r.contribution) ?? 0,
    raw_value: toNum(r.raw_value),
    threshold: toNum(r.threshold),
    ratio: toNum(r.ratio),
  };
}

export async function fetchStrategyRisks(
  rawCode: string,
  rawSecType: string | undefined | null,
  strategyName: string = DEFAULT_STRATEGY_NAME,
): Promise<StrategyRiskResponse> {
  const secType = (rawSecType as MaSpreadSecType) ?? "index";

  // 1. Fetch the latest risk_seq row for this (sec_type, code).
  const seqRows = await queryRows<RiskSeqRow>(RISK_SEQ_SQL, [secType, rawCode, strategyName]);

  if (seqRows.length === 0) {
    return { code: rawCode, sec_type: secType, risk_seq: null, periods: [], risk_factors: [] };
  }

  const riskSeq = mapRiskSeq(seqRows[0]);

  // 2. Fetch all period rows for the same seq_id.
  const periodRows = await queryRows<RiskPeriodRow>(
    RISK_PERIODS_SQL, [secType, rawCode, riskSeq.seq_id],
  );
  const periods = periodRows.map(mapRiskPeriod);

  // 3. Fetch risk score contribution factors for the same seq_id.
  const factorRows = await queryRows<RiskFactorRow>(
    RISK_FACTORS_SQL, [riskSeq.seq_id, rawCode],
  );
  const riskFactors = factorRows.map(mapRiskFactor);

  return {
    code: rawCode,
    sec_type: secType,
    risk_seq: riskSeq,
    periods,
    risk_factors: riskFactors,
  };
}
