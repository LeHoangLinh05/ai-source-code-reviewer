import { Activity, FileText, ListChecks, type LucideIcon } from "lucide-react";
import Link from "next/link";

import { cn } from "@/lib/utils";

export type ReviewWorkspaceTab = "overview" | "issues" | "report" | "trace";

const TABS: Array<{
  icon: LucideIcon;
  id: ReviewWorkspaceTab;
  label: string;
  suffix?: string;
}> = [
  { icon: Activity, id: "overview", label: "Overview" },
  { icon: ListChecks, id: "issues", label: "Issues" },
  { icon: FileText, id: "report", label: "Report" },
];

type ReviewWorkspaceTabsProps = {
  activeTab: ReviewWorkspaceTab;
  jobId: string;
};

export function ReviewWorkspaceTabs({
  activeTab,
  jobId,
}: ReviewWorkspaceTabsProps) {
  return (
    <nav
      aria-label="Review workspace"
      className="overflow-x-auto border-b border-border"
    >
      <div className="flex min-w-max gap-1">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const isActive = tab.id === activeTab;

          return (
            <Link
              aria-current={isActive ? "page" : undefined}
              className={cn(
                "inline-flex h-11 items-center gap-2 border-b-2 px-3 text-sm font-semibold transition-colors",
                isActive
                  ? "border-foreground text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
              href={getTabHref(jobId, tab.id)}
              key={tab.id}
            >
              <Icon aria-hidden="true" className="size-4" />
              {tab.label}
              {tab.suffix ? (
                <span className="rounded-md border border-border bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                  {tab.suffix}
                </span>
              ) : null}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}

function getTabHref(jobId: string, tab: ReviewWorkspaceTab) {
  if (tab === "overview") {
    return `/reviews/${jobId}`;
  }

  return `/reviews/${jobId}/${tab}`;
}
