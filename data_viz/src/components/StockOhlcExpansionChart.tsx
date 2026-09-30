/**
 * StockOhlcExpansionChart — closeable daily OHLC expansion for a single
 * stock, rendered below the composition pie chart when the user clicks a
 * stock slice in Layer 2.
 *
 * Thin STOCK config over the shared CodeTrendExpansionChart (the generic
 * secType closeable expansion also used by the linked-ETF tables' row
 * click) — passes the holding weight as the subtitle extra.
 *
 * The close (×) button calls `onClose`; the parent CompositionPieChart also
 * toggles the slice off when the same stock is clicked again.
 */
import CodeTrendExpansionChart from "@/components/CodeTrendExpansionChart";

interface Props {
  /** Stock code — suffixed ("000001.SZ") or bare ("000001"). */
  code: string;
  /** Display name (from the pie chart's stock_name). */
  name: string;
  /** Weight % the stock held in the parent ETF/index (for the subtitle). */
  weightPct?: number;
  onClose: () => void;
}

export default function StockOhlcExpansionChart({
  code,
  name,
  weightPct,
  onClose,
}: Props) {
  return (
    <CodeTrendExpansionChart
      secType="stock"
      code={code}
      name={name}
      onClose={onClose}
      closeLabel="close stock chart"
      subtitleExtra={weightPct != null ? `${weightPct.toFixed(2)}% holding` : undefined}
    />
  );
}
