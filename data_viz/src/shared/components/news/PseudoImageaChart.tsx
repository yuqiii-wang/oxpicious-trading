/**
 * PseudoImageaChart — the rough chart reconstruction of a Pseudo Imagea
 * chart block: the comprehension's critical points on a compact themed
 * chart (base-chart kit, bare variant — it lives inside the panel).
 */
import { useMemo } from "react";
import { BaseChart, useChartThemeMode } from "@/shared/charts/base-chart";
import {
  buildPseudoImageaChartOption,
  type PseudoImageChart,
} from "./pseudoImageaChartOption";

export default function PseudoImageaChart({ chart }: { chart: PseudoImageChart }) {
  const themeMode = useChartThemeMode();
  const option = useMemo(
    () => buildPseudoImageaChartOption(chart, themeMode),
    [chart, themeMode],
  );
  return <BaseChart variant="bare" option={option} height={200} />;
}
