import { CirclePlay, GitFork, ShieldCheck, Timer } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function DashboardPage() {
  return (
    <>
      <header className="flex flex-col gap-4 border-b border-border pb-5 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            AI source review workspace
          </p>
          <h1 className="mt-2 text-2xl font-extrabold tracking-normal">
            Dashboard
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Monitor repositories, review jobs, and security posture from one
            dense workspace.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <Link href="/repositories">
              <GitFork aria-hidden="true" />
              Add Repository
            </Link>
          </Button>
        </div>
      </header>

      <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard icon={GitFork} label="Repositories" value="0" />
        <MetricCard icon={CirclePlay} label="Active reviews" value="0" />
        <MetricCard icon={ShieldCheck} label="Critical issues" value="0" />
        <MetricCard icon={Timer} label="Avg. run time" value="--" />
      </section>

      <section className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <Card>
          <CardHeader>
            <CardTitle>Review Entry Points</CardTitle>
            <CardDescription>
              Connect source repositories and start queued AI reviews.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            <Button asChild className="justify-start" variant="secondary">
              <Link href="/repositories">
                <GitFork aria-hidden="true" />
                Open Repositories
              </Link>
            </Button>
            <Button asChild className="justify-start" variant="secondary">
              <Link href="/reviews">
                <CirclePlay aria-hidden="true" />
                Open Reviews
              </Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Analysis Pipeline</CardTitle>
            <CardDescription>
              Realtime SSE will stream these stages as the worker progresses.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3">
              {[
                "Cloning repository",
                "Running static analyzers",
                "Building AI context",
                "Generating report",
              ].map((step, index) => (
                <div
                  className="flex items-center gap-3 rounded-md border border-border bg-background px-3 py-2 text-sm"
                  key={step}
                >
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full border border-slate-700 text-xs text-muted-foreground">
                    {index + 1}
                  </span>
                  <span className="text-muted-foreground">{step}</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </section>
    </>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof GitFork;
  label: string;
  value: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-4 p-5">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            {label}
          </p>
          <p className="mt-2 text-3xl font-black tracking-normal">{value}</p>
        </div>
        <span className="flex size-10 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
          <Icon aria-hidden="true" className="size-5" />
        </span>
      </CardContent>
    </Card>
  );
}
