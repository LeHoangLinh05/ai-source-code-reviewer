import { Activity, Clock, ShieldCheck } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { CodebaseHealth, SeverityKey } from "@/lib/dashboard";
import { formatRelativeTime } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

const STATUS_CONFIG: Record<
  CodebaseHealth["status"],
  { ringClass: string; badgeClass: string }
> = {
  healthy: {
    ringClass: "stroke-emerald-500",
    badgeClass: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  },
  warning: {
    ringClass: "stroke-amber-500",
    badgeClass: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
  },
  critical: {
    ringClass: "stroke-rose-500",
    badgeClass: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20",
  },
  unknown: {
    ringClass: "stroke-slate-500",
    badgeClass: "bg-muted text-muted-foreground border-border",
  },
};

const SEVERITY_CONFIG: Record<
  SeverityKey,
  { label: string; barClass: string; dotClass: string }
> = {
  critical: {
    label: "Critical",
    barClass: "bg-rose-500",
    dotClass: "bg-rose-500",
  },
  high: {
    label: "High",
    barClass: "bg-orange-500",
    dotClass: "bg-orange-500",
  },
  medium: {
    label: "Medium",
    barClass: "bg-amber-500",
    dotClass: "bg-amber-500",
  },
  low: {
    label: "Low",
    barClass: "bg-sky-500",
    dotClass: "bg-sky-500",
  },
  info: {
    label: "Info",
    barClass: "bg-slate-400",
    dotClass: "bg-slate-400",
  },
};

export function CodebaseHealthCard({ health }: { health: CodebaseHealth }) {
  const statusConfig = STATUS_CONFIG[health.status];
  const circumference = 2 * Math.PI * 42;
  const dashOffset = circumference * (1 - health.score / 100);
  const totalFindings = health.severityBreakdown.reduce(
    (sum, item) => sum + item.value,
    0,
  );

  return (
    <Card className="flex h-full w-full flex-col justify-between rounded-xl border border-border bg-card shadow-sm">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base font-semibold">
            <Activity aria-hidden="true" className="size-4 text-muted-foreground" />
            Codebase health
          </CardTitle>
          <span
            className={cn(
              "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold",
              statusConfig.badgeClass,
            )}
          >
            {health.statusLabel}
          </span>
        </div>
        <CardDescription>
          Overall security posture across your repositories.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col justify-between gap-5">
        {/* Score ring + details */}
        <div className="flex items-center gap-5">
          <div className="relative size-24 shrink-0">
            <svg className="size-24 -rotate-90" viewBox="0 0 100 100">
              <circle
                className="fill-none stroke-muted/60"
                cx="50"
                cy="50"
                r="42"
                strokeWidth="8"
              />
              <circle
                className={cn(
                  "fill-none transition-all duration-700 ease-out",
                  statusConfig.ringClass,
                )}
                cx="50"
                cy="50"
                r="42"
                strokeDasharray={circumference}
                strokeDashoffset={dashOffset}
                strokeLinecap="round"
                strokeWidth="8"
              />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
              <span className="text-2xl font-bold tabular-nums text-foreground">
                {health.score}
              </span>
              <span className="text-[10px] font-medium uppercase text-muted-foreground">
                / 100
              </span>
            </div>
          </div>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <ShieldCheck className="size-4 text-primary" />
              <span>Coverage</span>
            </div>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              <strong className="text-foreground font-semibold">{health.reposCovered}</strong> of{" "}
              {health.totalRepos}{" "}
              {health.totalRepos === 1 ? "repository" : "repositories"} covered
              by reviews.
            </p>
            {health.lastReviewAt ? (
              <p className="mt-1.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                <Clock aria-hidden="true" className="size-3" />
                Last review {formatRelativeTime(health.lastReviewAt)}
              </p>
            ) : null}
          </div>
        </div>

        {/* Visual stacked severity bar */}
        {totalFindings > 0 ? (
          <div className="grid gap-2 border-t border-border pt-4">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span className="font-semibold uppercase tracking-wider text-[11px]">
                Severity distribution
              </span>
              <span className="tabular-nums font-semibold text-foreground">
                {totalFindings} total findings
              </span>
            </div>
            <div className="flex h-2.5 w-full overflow-hidden rounded-full bg-muted/80">
              {health.severityBreakdown.map((item) => {
                const pct = (item.value / totalFindings) * 100;
                if (pct === 0) return null;
                const config = SEVERITY_CONFIG[item.key];
                return (
                  <div
                    className={config.barClass}
                    key={item.key}
                    style={{ width: `${pct}%` }}
                    title={`${config.label}: ${item.value} (${Math.round(pct)}%)`}
                  />
                );
              })}
            </div>
          </div>
        ) : null}

        {/* Severity breakdown grid */}
        <div className={cn("grid gap-2", totalFindings === 0 && "border-t border-border pt-4")}>
          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {health.severityBreakdown.map((item) => {
              const config = SEVERITY_CONFIG[item.key];
              return (
                <li
                  className="flex items-center justify-between rounded-lg border border-border/80 bg-background/60 px-3 py-2.5 transition-colors hover:bg-muted/30"
                  key={item.key}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      aria-hidden="true"
                      className={cn("size-2.5 shrink-0 rounded-full", config.dotClass)}
                    />
                    <span className="truncate text-xs font-medium text-muted-foreground">
                      {config.label}
                    </span>
                  </div>
                  <span className="ml-2 text-xs font-bold tabular-nums text-foreground">
                    {item.value}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      </CardContent>
    </Card>
  );
}