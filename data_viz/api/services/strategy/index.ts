/**
 * Barrel re-export of the strategy service sub-modules.
 *
 * Consumers should keep importing from `../services/strategy/index.js`
 * (or `../services/strategy.js` resolves here) — this preserves the public
 * surface of the original monolithic strategy.service.ts.
 */
export {
  DEFAULT_STRATEGY_NAME,
} from "./_shared.js";

export {
  runSingletonBacktest,
  type StrategyDecision,
  type StrategyOhlcRow,
  type StrategyBacktestResponse,
} from "./backtest.js";

export {
  fetchStrategyRisks,
} from "./risks.js";

export {
  checkExistingStrategy,
} from "./check-existing.js";

export {
  runStrategyScript,
  runTrainingScript,
  type RunStrategyResult,
} from "./run-scripts.js";

export {
  fetchTrainInfo,
  type TrainInfoResponse,
  type TrainConfigRow,
  type TrainRunRow,
  type TrainTrialRow,
  type TrainLossType,
} from "./train-history.js";
