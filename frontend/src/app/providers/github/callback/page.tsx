"use client";

import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  getSafeProviderReturnPath,
  syncGitHubInstallation,
} from "@/lib/providers";

const REDIRECT_DELAY_MS = 900;

type ConnectionState = "loading" | "success" | "failed" | "missing";

export default function GitHubInstallationCallbackPage() {
  return (
    <Suspense fallback={<CallbackLoadingState />}>
      <GitHubInstallationCallbackContent />
    </Suspense>
  );
}

function GitHubInstallationCallbackContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const installationId = searchParams.get("installation_id");
  const returnPath = getSafeProviderReturnPath(searchParams.get("state"));
  const [connectionState, setConnectionState] =
    useState<ConnectionState>("loading");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!installationId) {
      setConnectionState("missing");
      setErrorMessage("GitHub did not return an installation ID.");
      return;
    }

    const confirmedInstallationId = installationId;
    let canceled = false;
    let timeoutId: number | null = null;

    async function syncInstallation() {
      try {
        await syncGitHubInstallation({
          installation_id: confirmedInstallationId,
        });
        if (canceled) {
          return;
        }

        setConnectionState("success");
        toast.success("GitHub App connected.");
        timeoutId = window.setTimeout(() => {
          router.replace(returnPath);
        }, REDIRECT_DELAY_MS);
      } catch (requestError) {
        if (canceled) {
          return;
        }

        const message = getApiErrorMessage(
          requestError,
          "Unable to sync GitHub App installation.",
        );
        setConnectionState("failed");
        setErrorMessage(message);
        toast.error(message);
      }
    }

    void syncInstallation();

    return () => {
      canceled = true;
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [installationId, returnPath, router]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <Card className="w-full max-w-xl">
        <CardHeader>
          <CardTitle>GitHub App connection</CardTitle>
          <CardDescription>
            Finalizing the installation that GitHub just returned to RepoReview.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {connectionState === "loading" ? (
            <StatusRow icon={Loader2} tone="text-sky-500" text="Syncing installation..." />
          ) : null}
          {connectionState === "success" ? (
            <StatusRow
              icon={CheckCircle2}
              tone="text-emerald-500"
              text="Connection saved. Redirecting back to your review."
            />
          ) : null}
          {connectionState === "missing" || connectionState === "failed" ? (
            <div className="grid gap-3 rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              <StatusRow
                icon={AlertTriangle}
                tone="text-destructive"
                text={errorMessage ?? "GitHub installation sync failed."}
              />
              <div className="flex flex-wrap gap-2">
                <Button asChild size="sm" variant="secondary">
                  <a href={returnPath}>Go back</a>
                </Button>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </main>
  );
}

function CallbackLoadingState() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-4">
      <Loader2
        aria-label="Loading GitHub App connection"
        className="size-6 animate-spin text-sky-500"
      />
    </main>
  );
}

function StatusRow({
  icon: Icon,
  tone,
  text,
}: {
  icon: LucideIcon;
  tone: string;
  text: string;
}) {
  return (
    <div className={`flex items-center gap-3 text-sm ${tone}`}>
      <Icon className="size-4 shrink-0" aria-hidden="true" />
      <span>{text}</span>
    </div>
  );
}
