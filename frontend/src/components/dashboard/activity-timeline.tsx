import {
  CheckCircle2,
  ChevronRight,
  Clock,
  GitBranch,
  Loader2,
  XCircle,
} from "lucide-react";
import Link from "next/link";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { ActivityGroup, TimelineEvent } from "@/lib/dashboard";
import { formatRelativeTime } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

export function ActivityTimeline({ groups }: { groups: ActivityGroup[] }) {
  return (
    <Card className="rounded-xl border border-border bg-card shadow-sm">
      <CardHeader className="pb-3">
        <CardTitle className="text-base font-semibold">Recent activity</CardTitle>
        <CardDescription>
          Latest review runs across your repositories.
        </CardDescription>
      </CardHeader>
      <CardContent className="pt-0">
        {groups.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No review activity yet. Start a review to see updates here.
          </p>
        ) : (
          <div className="grid gap-5">
            {groups.map((group) => (
              <div key={group.label}>
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {group.label}
                </p>
                <ul className="mt-2.5 grid gap-2">
                  {group.events.map((event) => (
                    <TimelineRow event={event} key={event.id} />
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function TimelineRow({ event }: { event: TimelineEvent }) {
  return (
    <li>
      <Link
        className="group flex flex-col gap-2 rounded-lg border border-border bg-background px-4 py-3 transition-all duration-150 hover:border-border hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:flex-row sm:items-center sm:justify-between"
        href={event.href}
      >
        <div className="flex min-w-0 items-center gap-3">
          <EventIcon event={event} />
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold text-foreground transition-colors group-hover:text-primary">
              {event.repositoryName}
            </p>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <GitBranch aria-hidden="true" className="size-3 text-muted-foreground" />
                {event.branch}
              </span>
              <span>·</span>
              <span className="inline-flex items-center gap-1">
                <Clock aria-hidden="true" className="size-3 text-muted-foreground" />
                {formatRelativeTime(event.timestamp)}
              </span>
            </div>
          </div>
        </div>

        <div className="flex shrink-0 items-center justify-between sm:justify-end gap-3 self-end sm:self-center">
          <EventSummary event={event} />
          <ChevronRight
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground opacity-40 transition-all duration-150 group-hover:translate-x-0.5 group-hover:opacity-100"
          />
        </div>
      </Link>
    </li>
  );
}

function EventIcon({ event }: { event: TimelineEvent }) {
  if (event.type === "review_completed") {
    const hasCritical = (event.criticalFindings ?? 0) > 0;
    return (
      <span
        className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-lg border",
          hasCritical
            ? "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400"
            : "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
        )}
      >
        <CheckCircle2 aria-hidden="true" className="size-4" />
      </span>
    );
  }

  if (event.type === "review_failed") {
    return (
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400">
        <XCircle aria-hidden="true" className="size-4" />
      </span>
    );
  }

  return (
    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400">
      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
    </span>
  );
}

function EventSummary({ event }: { event: TimelineEvent }) {
  if (event.type === "review_completed") {
    const findings = event.findings ?? 0;
    const critical = event.criticalFindings ?? 0;

    if (findings === 0) {
      return (
        <span className="shrink-0 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
          Clean
        </span>
      );
    }

    return (
      <span
        className={cn(
          "shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-semibold",
          critical > 0
            ? "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400"
            : "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
        )}
      >
        {findings} finding{findings === 1 ? "" : "s"}
        {critical > 0 ? ` · ${critical} critical` : ""}
      </span>
    );
  }

  if (event.type === "review_failed") {
    return (
      <span className="shrink-0 rounded-full border border-rose-500/30 bg-rose-500/10 px-2.5 py-0.5 text-xs font-semibold text-rose-600 dark:text-rose-400">
        Failed
      </span>
    );
  }

  if (event.type === "review_queued") {
    return (
      <span className="shrink-0 rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs font-semibold text-muted-foreground">
        Queued
      </span>
    );
  }

  return (
    <span className="shrink-0 rounded-full border border-sky-500/30 bg-sky-500/10 px-2.5 py-0.5 text-xs font-semibold text-sky-600 dark:text-sky-400">
      Running
    </span>
  );
}