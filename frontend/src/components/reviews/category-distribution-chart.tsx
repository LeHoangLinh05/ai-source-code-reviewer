"use client";

import {
  BarElement,
  CategoryScale,
  Chart as ChartJS,
  Legend,
  LinearScale,
  Tooltip,
  type ChartOptions,
} from "chart.js";
import { Bar } from "react-chartjs-2";

import type { IssueCategory } from "@/types/issue";

ChartJS.register(BarElement, CategoryScale, LinearScale, Tooltip, Legend);

const CATEGORY_LABELS: Record<IssueCategory, string> = {
  bug: "Bug",
  maintainability: "Maintainability",
  performance: "Performance",
  requirement: "Requirement",
  security: "Security",
  style: "Style",
};

const CATEGORY_COLORS: Record<IssueCategory, string> = {
  bug: "#f97316",
  maintainability: "#94a3b8",
  performance: "#f59e0b",
  requirement: "#14b8a6",
  security: "#e11d48",
  style: "#38bdf8",
};

const CHART_OPTIONS: ChartOptions<"bar"> = {
  indexAxis: "y",
  responsive: true,
  maintainAspectRatio: false,
  scales: {
    x: {
      beginAtZero: true,
      grid: {
        color: "rgba(148, 163, 184, 0.12)",
      },
      ticks: {
        color: "#94a3b8",
        precision: 0,
      },
    },
    y: {
      grid: {
        display: false,
      },
      ticks: {
        color: "#94a3b8",
      },
    },
  },
  plugins: {
    legend: {
      display: false,
    },
    tooltip: {
      callbacks: {
        label: (context) => {
          const value = context.parsed.x;
          return ` ${value} ${value === 1 ? "finding" : "findings"}`;
        },
      },
    },
  },
};

type CategoryDistributionChartProps = {
  items: Array<{ label: IssueCategory; value: number }>;
};

export function CategoryDistributionChart({
  items,
}: CategoryDistributionChartProps) {
  const visibleItems = items
    .filter((item) => item.value > 0)
    .sort((left, right) => right.value - left.value);

  if (visibleItems.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-[15px] text-muted-foreground">
        No issue categories available.
      </p>
    );
  }

  return (
    <div className="h-72 w-full">
      <Bar
        data={{
          labels: visibleItems.map((item) => CATEGORY_LABELS[item.label]),
          datasets: [
            {
              data: visibleItems.map((item) => item.value),
              backgroundColor: visibleItems.map(
                (item) => CATEGORY_COLORS[item.label],
              ),
              borderRadius: 6,
              borderSkipped: false,
              barThickness: 18,
            },
          ],
        }}
        options={CHART_OPTIONS}
      />
    </div>
  );
}
