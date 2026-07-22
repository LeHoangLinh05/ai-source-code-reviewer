import { ShieldCheck } from "lucide-react";

import { cn } from "@/lib/utils";

type ScoreTrackProps = {
  caption?: string;
  className?: string;
  label: string;
  showNoFindings?: boolean;
  value: number | null;
};

export function ScoreTrack({
  caption,
  className,
  label,
  showNoFindings = false,
  value,
}: ScoreTrackProps) {
  const percentage = value === null ? 0 : Math.max(0, Math.min(100, value * 10));

  return (
    <div className={cn("grid gap-2", className)}>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{label}</p>
          {caption ? (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {caption}
            </p>
          ) : null}
        </div>
        {showNoFindings ? (
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-muted px-2 py-1 text-xs font-semibold text-muted-foreground">
            <ShieldCheck aria-hidden="true" className="size-3.5" />
            No findings
          </span>
        ) : (
          <span className="shrink-0 text-sm font-semibold text-foreground">
            {value === null ? "--" : value.toFixed(1)}
            <span className="ml-1 text-xs font-normal text-muted-foreground">
              /10
            </span>
          </span>
        )}
      </div>
      <div className="h-2.5 overflow-hidden rounded-md bg-muted">
        <div
          className="h-full rounded-md bg-foreground"
          style={{ width: `${showNoFindings ? 100 : percentage}%` }}
        />
      </div>
    </div>
  );
}
