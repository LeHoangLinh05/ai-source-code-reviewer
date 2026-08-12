import {
  AlertOctagon,
  AlertTriangle,
  CircleAlert,
  LoaderCircle,
  ShieldAlert,
  ShieldCheck,
  ShieldQuestion,
  type LucideIcon,
} from "lucide-react";

import type { PostureTone, RiskPosture } from "@/lib/dashboard";
import { cn } from "@/lib/utils";

type ToneStyle = {
  icon: LucideIcon;
  iconWrap: string;
  accentBar: string;
};

const TONE_STYLES: Record<PostureTone, ToneStyle> = {
  critical: {
    icon: AlertOctagon,
    iconWrap: "border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400",
    accentBar: "bg-rose-500",
  },
  high: {
    icon: ShieldAlert,
    iconWrap:
      "border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-400",
    accentBar: "bg-orange-500",
  },
  attention: {
    icon: AlertTriangle,
    iconWrap:
      "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    accentBar: "bg-amber-500",
  },
  clear: {
    icon: ShieldCheck,
    iconWrap:
      "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    accentBar: "bg-emerald-500",
  },
  unavailable: {
    icon: CircleAlert,
    iconWrap:
      "border-amber-500/30 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    accentBar: "bg-amber-500",
  },
  running: {
    icon: LoaderCircle,
    iconWrap: "border-border bg-background text-muted-foreground",
    accentBar: "bg-primary",
  },
  pending: {
    icon: ShieldQuestion,
    iconWrap: "border-border bg-background text-muted-foreground",
    accentBar: "bg-border",
  },
};

export function RiskPostureCard({ posture }: { posture: RiskPosture }) {
  const tone = TONE_STYLES[posture.tone];
  const Icon = tone.icon;

  return (
    <section
      aria-labelledby="risk-posture-heading"
      className="relative overflow-hidden rounded-md border border-border bg-card shadow-sm shadow-foreground/5"
    >
      <span aria-hidden="true" className={cn("absolute inset-x-0 top-0 h-1", tone.accentBar)} />
      <div className="p-6">
        <div className="flex gap-4">
          <span
            className={cn(
              "flex size-14 shrink-0 items-center justify-center rounded-md border",
              tone.iconWrap,
            )}
          >
            <Icon
              aria-hidden="true"
              className={cn("size-6", posture.tone === "running" && "animate-spin")}
            />
          </span>
          <div className="min-w-0">
            <h2
              className="text-2xl font-extrabold tracking-normal text-balance sm:text-3xl"
              id="risk-posture-heading"
            >
              {posture.title}
            </h2>
            <p className="mt-2 max-w-xl text-[15px] leading-6 text-muted-foreground text-pretty">
              {posture.message}
            </p>
          </div>
        </div>

      </div>

      <dl className="grid grid-cols-2 gap-px border-t border-border bg-border sm:grid-cols-4">
        <PostureStat
          emphasize={posture.criticalCount > 0}
          label="Critical"
          value={posture.criticalCount}
          valueClass="text-rose-600 dark:text-rose-400"
        />
        <PostureStat
          emphasize={posture.highCount > 0}
          label="High"
          value={posture.highCount}
          valueClass="text-orange-600 dark:text-orange-400"
        />
        <PostureStat label="Total findings" value={posture.totalIssues} />
        <PostureStat label="Repos reviewed" value={posture.reposReviewed} />
      </dl>
    </section>
  );
}

function PostureStat({
  emphasize = false,
  label,
  value,
  valueClass,
}: {
  emphasize?: boolean;
  label: string;
  value: number;
  valueClass?: string;
}) {
  return (
    <div className="bg-card px-5 py-4">
      <dt className="text-xs font-medium uppercase text-muted-foreground">
        {label}
      </dt>
      <dd
        className={cn(
          "mt-1 text-2xl font-extrabold tabular-nums",
          emphasize && valueClass ? valueClass : "text-foreground",
        )}
      >
        {value}
      </dd>
    </div>
  );
}
