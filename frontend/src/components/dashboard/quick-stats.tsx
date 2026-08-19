import {
  AlertOctagon,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  FolderGit2,
  ScanSearch,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { CardContent } from "@/components/ui/card";
import type { QuickStats } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

type TrendDirection = "up" | "down" | "stable" | "unknown";

const TREND_CONFIG: Record<
  TrendDirection,
  { icon: LucideIcon | null; label: string; className: string }
> = {
  up: {
    icon: ArrowUpRight,
    label: "Increasing",
    className: "text-rose-600 dark:text-rose-400 bg-rose-500/10 border-rose-500/20",
  },
  down: {
    icon: ArrowDownRight,
    label: "Decreasing",
    className: "text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  },
  stable: {
    icon: null,
    label: "Stable",
    className: "",
  },
  unknown: {
    icon: null,
    label: "",
    className: "",
  },
};

export function QuickStatsRow({ stats }: { stats: QuickStats }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <StatCard
        href={stats.repositories.href}
        icon={FolderGit2}
        iconClassName="text-sky-500 bg-sky-500/10 border-sky-500/20"
        label="Repositories"
        sublabel={`${stats.repositories.reviewed} reviewed`}
        value={stats.repositories.total}
      />
      <StatCard
        href={stats.reviews.href}
        icon={ScanSearch}
        iconClassName="text-violet-500 bg-violet-500/10 border-violet-500/20"
        label="Reviews"
        sublabel={
          stats.reviews.active > 0
            ? `${stats.reviews.active} running`
            : "None active"
        }
        value={stats.reviews.total}
      />
      <StatCard
        href={stats.openIssues.href}
        icon={ShieldAlert}
        iconClassName="text-amber-500 bg-amber-500/10 border-amber-500/20"
        label="Open findings"
        sublabel="Across recent reports"
        trend={stats.openIssues.trend}
        value={stats.openIssues.total}
      />
      <StatCard
        href={stats.criticalIssues.href}
        icon={AlertOctagon}
        iconClassName="text-rose-500 bg-rose-500/10 border-rose-500/20"
        label="Critical"
        sublabel="Need immediate action"
        trend={stats.criticalIssues.trend}
        value={stats.criticalIssues.total}
      />
    </div>
  );
}

function StatCard({
  href,
  icon: Icon,
  iconClassName,
  label,
  sublabel,
  trend,
  value,
}: {
  href: string;
  icon: LucideIcon;
  iconClassName?: string;
  label: string;
  sublabel: string;
  trend?: TrendDirection;
  value: number;
}) {
  const trendConfig = trend ? TREND_CONFIG[trend] : null;
  const TrendIcon = trendConfig?.icon;

  return (
    <Link
      className={cn(
        "group flex flex-col justify-between rounded-xl border border-border bg-card p-5 transition-all duration-200 hover:border-primary/40 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      )}
      href={href}
    >
      <CardContent className="p-0">
        <div className="flex items-center justify-between">
          <span
            className={cn(
              "flex size-10 items-center justify-center rounded-lg border",
              iconClassName,
            )}
          >
            <Icon aria-hidden="true" className="size-5" />
          </span>
          {trendConfig && TrendIcon ? (
            <span
              className={cn(
                "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-semibold",
                trendConfig.className,
              )}
            >
              <TrendIcon aria-hidden="true" className="size-3" />
              <span>{trendConfig.label}</span>
            </span>
          ) : null}
        </div>
        <p className="mt-4 text-3xl font-extrabold tabular-nums tracking-tight text-foreground">
          {value}
        </p>
        <p className="mt-1 text-sm font-semibold text-foreground">{label}</p>
        <p className="mt-1 flex items-center justify-between text-xs text-muted-foreground">
          <span>{sublabel}</span>
          <ArrowRight
            aria-hidden="true"
            className="size-3.5 opacity-0 transition-all duration-200 group-hover:translate-x-0.5 group-hover:opacity-100"
          />
        </p>
      </CardContent>
    </Link>
  );
}