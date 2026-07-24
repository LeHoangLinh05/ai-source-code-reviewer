import type { SeverityKey } from "@/lib/dashboard";

export const SEVERITY_META: Record<
  SeverityKey,
  { label: string; bar: string; dot: string; text: string }
> = {
  critical: {
    label: "Critical",
    bar: "bg-rose-500",
    dot: "bg-rose-500",
    text: "text-rose-600 dark:text-rose-400",
  },
  high: {
    label: "High",
    bar: "bg-orange-500",
    dot: "bg-orange-500",
    text: "text-orange-600 dark:text-orange-400",
  },
  medium: {
    label: "Medium",
    bar: "bg-amber-500",
    dot: "bg-amber-500",
    text: "text-amber-600 dark:text-amber-400",
  },
  low: {
    label: "Low",
    bar: "bg-sky-500",
    dot: "bg-sky-500",
    text: "text-sky-600 dark:text-sky-400",
  },
  info: {
    label: "Info",
    bar: "bg-slate-400",
    dot: "bg-slate-400",
    text: "text-muted-foreground",
  },
};
