"use client";

import {
  ArcElement,
  Chart as ChartJS,
  Legend,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import { Doughnut } from "react-chartjs-2";

import type { LatestReportSummary, SeverityKey } from "@/lib/dashboard";

ChartJS.register(ArcElement, Tooltip, Legend);

const SEVERITY_LABELS: Record<SeverityKey, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

const SEVERITY_COLORS: Record<SeverityKey, string> = {
  critical: "#e11d48",
  high: "#f97316",
  medium: "#f59e0b",
  low: "#38bdf8",
  info: "#94a3b8",
};

const CHART_OPTIONS: ChartOptions<"doughnut"> = {
  responsive: true,
  maintainAspectRatio: false,
  cutout: "72%",
  elements: {
    arc: {
      borderRadius: 0,
    },
  },
  plugins: {
    legend: {
      position: "bottom",
      labels: {
        color: "#94a3b8",
        padding: 16,
        usePointStyle: true,
        pointStyle: "circle",
      },
    },
    tooltip: {
      callbacks: {
        label: (context) => {
          const value = context.parsed;
          return ` ${context.label}: ${value} ${value === 1 ? "finding" : "findings"}`;
        },
      },
    },
  },
};

type SeverityBreakdownChartProps = {
  items: LatestReportSummary["severity"];
};

export function SeverityBreakdownChart({
  items,
}: SeverityBreakdownChartProps) {
  const visibleItems = items.filter((item) => item.value > 0);
  const totalFindings = visibleItems.reduce((sum, item) => sum + item.value, 0);

  return (
    <div className="relative h-64 min-w-0 w-full max-w-full overflow-hidden">
      <Doughnut
        data={{
          labels: visibleItems.map((item) => SEVERITY_LABELS[item.key]),
          datasets: [
            {
              data: visibleItems.map((item) => item.value),
              backgroundColor: visibleItems.map(
                (item) => SEVERITY_COLORS[item.key],
              ),
              borderColor: "transparent",
              borderWidth: 0,
              hoverOffset: 0,
              spacing: 0,
            },
          ],
        }}
        options={CHART_OPTIONS}
      />
      <div className="pointer-events-none absolute inset-x-0 top-0 bottom-8 flex flex-col items-center justify-center">
        <span className="text-3xl font-extrabold tabular-nums text-foreground">
          {totalFindings}
        </span>
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          findings
        </span>
      </div>
    </div>
  );
}
