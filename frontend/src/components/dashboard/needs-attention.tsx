import {
  AlertOctagon,
  ChevronRight,
  FileWarning,
  ShieldAlert,
  ShieldCheck,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { AttentionItem, AttentionTone } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

const TONE_STYLES: Record<
  AttentionTone,
  { icon: LucideIcon; wrap: string }
> = {
  critical: {
    icon: AlertOctagon,
    wrap: "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400",
  },
  high: {
    icon: ShieldAlert,
    wrap: "border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-400",
  },
  failed: {
    icon: XCircle,
    wrap: "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400",
  },
  report: {
    icon: FileWarning,
    wrap: "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
  },
  file: {
    icon: FileWarning,
    wrap: "border-border bg-background text-muted-foreground",
  },
};

export function NeedsAttention({ items }: { items: AttentionItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Needs attention</CardTitle>
        <CardDescription>
          Actionable findings and blocked reviews from your latest results.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="grid gap-2.5">
            {items.map((item) => (
              <li key={item.id}>
                <AttentionRow item={item} />
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const tone = TONE_STYLES[item.tone];
  const Icon = item.tone === "failed" ? XCircle : tone.icon;

  return (
    <Link
      className="group flex items-center gap-3 rounded-md border border-border bg-background p-3 transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      href={item.href}
    >
      <span
        className={cn(
          "flex size-9 shrink-0 items-center justify-center rounded-md border",
          tone.wrap,
        )}
      >
        <Icon aria-hidden="true" className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate break-all text-sm font-semibold text-foreground">
          {item.title}
        </span>
        <span className="mt-0.5 block truncate text-[13px] text-muted-foreground">
          {item.meta}
        </span>
      </span>
      <ChevronRight
        aria-hidden="true"
        className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
      />
    </Link>
  );
}

function EmptyState() {
  return (
    <div className="flex items-start gap-3 rounded-md border border-emerald-500/30 bg-emerald-500/5 p-4">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
        <ShieldCheck aria-hidden="true" className="size-4" />
      </span>
      <div>
        <p className="text-sm font-semibold text-foreground">All clear</p>
        <p className="mt-0.5 text-[15px] leading-6 text-muted-foreground">
          No critical issues found in the latest completed reviews.
        </p>
      </div>
    </div>
  );
}
