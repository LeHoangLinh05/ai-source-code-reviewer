import {
  AlertOctagon,
  ArrowRight,
  CheckCircle2,
  FileWarning,
  ShieldAlert,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { AttentionItem, AttentionTone } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

const TONE_CONFIG: Record<
  AttentionTone,
  {
    icon: LucideIcon;
    iconWrap: string;
    actionLabel: string;
    actionVariant: "destructive" | "secondary" | "outline";
  }
> = {
  critical: {
    icon: AlertOctagon,
    iconWrap:
      "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400",
    actionLabel: "Triage now",
    actionVariant: "destructive",
  },
  high: {
    icon: ShieldAlert,
    iconWrap:
      "border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-400",
    actionLabel: "Review",
    actionVariant: "secondary",
  },
  failed: {
    icon: XCircle,
    iconWrap:
      "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400",
    actionLabel: "Retry",
    actionVariant: "secondary",
  },
  report: {
    icon: FileWarning,
    iconWrap:
      "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    actionLabel: "View",
    actionVariant: "secondary",
  },
  file: {
    icon: FileWarning,
    iconWrap: "border-border bg-background text-muted-foreground",
    actionLabel: "Inspect",
    actionVariant: "outline",
  },
};

export function ActionCenter({ items }: { items: AttentionItem[] }) {
  const hasActions = items.length > 0;

  return (
    <Card className="flex h-full w-full flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <CardTitle className="text-base font-semibold">Action required</CardTitle>
          {hasActions ? (
            <span className="inline-flex items-center rounded-full border border-rose-500/30 bg-rose-500/10 px-2 py-0.5 text-xs font-semibold text-rose-600 dark:text-rose-400">
              {items.length}
            </span>
          ) : null}
        </div>
        <CardDescription>
          {hasActions
            ? "These items need your attention before you can ship safely."
            : "Nothing blocking you right now."}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col pt-0 overflow-hidden">
        {hasActions ? (
          <div className="flex-1 overflow-y-auto overflow-x-hidden pr-1 max-h-[460px]">
            <ul className="grid gap-2.5">
              {items.map((item) => (
                <ActionRow item={item} key={item.id} />
              ))}
            </ul>
          </div>
        ) : (
          <AllClearState />
        )}
      </CardContent>
    </Card>
  );
}

function ActionRow({ item }: { item: AttentionItem }) {
  const config = TONE_CONFIG[item.tone];
  const Icon = config.icon;

  return (
    <li className="min-w-0">
      <div
        className={cn(
          "group flex flex-col gap-3 rounded-lg border border-border bg-background p-3.5 transition-all duration-150 hover:border-border hover:bg-muted/40 sm:flex-row sm:items-center sm:justify-between min-w-0",
        )}
      >
        <div className="flex min-w-0 flex-1 items-center gap-3">
          <span
            className={cn(
              "flex size-9 shrink-0 items-center justify-center rounded-lg border",
              config.iconWrap,
            )}
          >
            <Icon aria-hidden="true" className="size-4" />
          </span>

          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-foreground break-words">
              {item.title}
            </p>
            <p className="mt-0.5 text-xs text-muted-foreground break-words leading-relaxed">
              {item.meta}
            </p>
          </div>
        </div>

        <Button
          asChild
          className="shrink-0 self-end sm:self-center whitespace-nowrap"
          size="sm"
          variant={config.actionVariant}
        >
          <Link href={item.href}>
            {config.actionLabel}
            <ArrowRight aria-hidden="true" className="size-3.5 ml-1" />
          </Link>
        </Button>
      </div>
    </li>
  );
}

function AllClearState() {
  return (
    <div className="flex items-center gap-3.5 rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-4">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 aria-hidden="true" className="size-4" />
      </span>
      <div>
        <p className="text-sm font-semibold text-foreground">
          All clear — no blockers
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">
          No critical or high-severity findings in your recent reviews.
        </p>
      </div>
    </div>
  );
}