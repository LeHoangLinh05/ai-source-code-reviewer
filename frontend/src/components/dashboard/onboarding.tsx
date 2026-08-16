import { FileSearch, GitFork, ScanSearch, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

const STEPS = [
  {
    icon: GitFork,
    title: "Connect a repository",
    description: "Add a public GitHub repository to review.",
  },
  {
    icon: ScanSearch,
    title: "Run an AI review",
    description: "RepoGuard AI clones, analyzes, and reviews your code.",
  },
  {
    icon: FileSearch,
    title: "Triage findings",
    description: "Open prioritized reports and fix what matters first.",
  },
];

export function DashboardOnboarding() {
  return (
    <Card>
      <CardContent className="grid gap-8 p-6 sm:p-8">
        <div className="flex gap-4">
          <span className="flex size-14 shrink-0 items-center justify-center rounded-md border border-border bg-background text-foreground">
            <ShieldCheck aria-hidden="true" className="size-6" />
          </span>
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Welcome to RepoGuard AI
            </p>
            <h2 className="mt-1 text-2xl font-extrabold tracking-normal text-balance sm:text-3xl">
              Start securing your codebase
            </h2>
            <p className="mt-2 max-w-xl text-[15px] leading-6 text-muted-foreground text-pretty">
              RepoGuard AI runs automated security and quality reviews on your
              source code, then surfaces the findings that need attention first.
              Connect your first repository to get started.
            </p>
          </div>
        </div>

        <ol className="grid gap-4 sm:grid-cols-3">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            return (
              <li
                className="rounded-md border border-border bg-background p-4"
                key={step.title}
              >
                <div className="flex items-center gap-2">
                  <span className="flex size-8 items-center justify-center rounded-md border border-border bg-card text-muted-foreground">
                    <Icon aria-hidden="true" className="size-4" />
                  </span>
                  <span className="text-xs font-semibold uppercase text-muted-foreground">
                    Step {index + 1}
                  </span>
                </div>
                <h3 className="mt-3 text-[15px] font-semibold text-foreground">
                  {step.title}
                </h3>
                <p className="mt-1 text-[13px] leading-5 text-muted-foreground">
                  {step.description}
                </p>
              </li>
            );
          })}
        </ol>

        <div>
          <Button asChild size="lg">
            <Link href="/repositories">
              <GitFork aria-hidden="true" />
              Connect a repository
            </Link>
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
