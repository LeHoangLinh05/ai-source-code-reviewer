"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { TechnicalDetails } from "@/components/ui/technical-details";

type GlobalErrorProps = {
  error: Error & { digest?: string };
  reset: () => void;
};

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4 py-10 text-foreground">
      <Card className="w-full max-w-lg">
        <CardHeader>
          <CardTitle>Something went wrong</CardTitle>
          <CardDescription>
            The current view failed to render. You can retry the view or return
            to the dashboard.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <div className="flex flex-col gap-3 sm:flex-row">
            <Button onClick={reset} type="button">
              <RefreshCw aria-hidden="true" />
              Retry
            </Button>
            <Button asChild type="button" variant="secondary">
              <Link href="/dashboard">Open Dashboard</Link>
            </Button>
          </div>
          <TechnicalDetails
            description="Diagnostic information for support and development."
            title="Technical error details"
          >
            <dl className="grid gap-3 text-sm">
              <div>
                <dt className="font-semibold text-foreground">Message</dt>
                <dd className="mt-1 break-words text-muted-foreground">
                  {error.message}
                </dd>
              </div>
              {error.digest ? (
                <div>
                  <dt className="font-semibold text-foreground">Digest</dt>
                  <dd className="mt-1 break-all font-mono text-xs text-muted-foreground">
                    {error.digest}
                  </dd>
                </div>
              ) : null}
              {error.stack ? (
                <div>
                  <dt className="font-semibold text-foreground">Stack trace</dt>
                  <dd className="mt-1">
                    <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-muted-foreground">
                      {error.stack}
                    </pre>
                  </dd>
                </div>
              ) : null}
            </dl>
          </TechnicalDetails>
        </CardContent>
      </Card>
    </main>
  );
}
