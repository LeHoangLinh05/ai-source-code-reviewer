import { ChevronDown, Wrench } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

type TechnicalDetailsProps = {
  children: ReactNode;
  className?: string;
  contentClassName?: string;
  description?: string;
  title?: string;
};

export function TechnicalDetails({
  children,
  className,
  contentClassName,
  description = "Additional diagnostic information for developers.",
  title = "Technical details",
}: TechnicalDetailsProps) {
  return (
    <details
      className={cn(
        "group/technical overflow-hidden rounded-md border border-border bg-muted/20",
        className,
      )}
    >
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
          <Wrench aria-hidden="true" className="size-4" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-foreground">
            {title}
          </span>
          <span className="mt-0.5 block text-xs leading-5 text-muted-foreground">
            {description}
          </span>
        </span>
        <ChevronDown
          aria-hidden="true"
          className="size-4 shrink-0 text-muted-foreground transition-transform duration-150 group-open/technical:rotate-180"
        />
      </summary>
      <div
        className={cn(
          "border-t border-border px-4 py-4",
          contentClassName,
        )}
      >
        {children}
      </div>
    </details>
  );
}
