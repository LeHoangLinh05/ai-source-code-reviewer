"use client";

import {
  ExternalLink,
  GitPullRequest,
  Loader2,
  RefreshCw,
  Unplug,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ConfirmationDialog } from "@/components/ui/confirmation-dialog";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  buildGitHubInstallUrl,
  buildGitHubInstallationManageUrl,
  disconnectProviderConnection,
  getGitHubInstallUrl,
  getProviderConnections,
  syncGitHubInstallation,
} from "@/lib/providers";
import type { ProviderConnection } from "@/types/provider";

export function ProviderConnectionsPanel() {
  const [connections, setConnections] = useState<ProviderConnection[]>([]);
  const [connectionPendingDisconnect, setConnectionPendingDisconnect] =
    useState<ProviderConnection | null>(null);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [syncingId, setSyncingId] = useState<string | null>(null);

  const loadConnections = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setConnections(await getProviderConnections());
    } catch (requestError) {
      setError(
        getApiErrorMessage(requestError, "Unable to load provider connections."),
      );
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

  async function handleConnectGitHub() {
    setIsConnecting(true);
    try {
      const response = await getGitHubInstallUrl();
      window.location.assign(
        buildGitHubInstallUrl(response.install_url, "/settings"),
      );
    } catch (requestError) {
      const message = getApiErrorMessage(
        requestError,
        "Unable to open the GitHub App installation page.",
      );
      toast.error(message);
      setIsConnecting(false);
    }
  }

  async function handleSyncConnection(connection: ProviderConnection) {
    setSyncingId(connection.id);
    try {
      const updatedConnection = await syncGitHubInstallation({
        installation_id: connection.installation_id,
      });
      setConnections((currentConnections) =>
        currentConnections.map((currentConnection) =>
          currentConnection.id === updatedConnection.id
            ? updatedConnection
            : currentConnection,
        ),
      );
      toast.success(`GitHub connection for @${connection.account_login} synced.`);
    } catch (requestError) {
      toast.error(
        getApiErrorMessage(requestError, "Unable to sync GitHub connection."),
      );
    } finally {
      setSyncingId(null);
    }
  }

  async function handleDisconnectConnection() {
    if (connectionPendingDisconnect === null) {
      return;
    }

    const connection = connectionPendingDisconnect;
    setDisconnectingId(connection.id);
    try {
      await disconnectProviderConnection(connection.id);
      setConnections((currentConnections) =>
        currentConnections.filter(
          (currentConnection) => currentConnection.id !== connection.id,
        ),
      );
      setConnectionPendingDisconnect(null);
      toast.success(`Disconnected GitHub account @${connection.account_login}.`);
    } catch (requestError) {
      toast.error(
        getApiErrorMessage(requestError, "Unable to disconnect GitHub account."),
      );
    } finally {
      setDisconnectingId(null);
    }
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>Provider connections</CardTitle>
          <CardDescription>
            Manage the GitHub installations RepoGuard can use to publish fixes.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-5">
          <div className="flex flex-col gap-3 rounded-md border border-border bg-background p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <h2 className="text-sm font-semibold">GitHub App</h2>
              <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                Code updates apply automatically. Use Sync now after approving new
                GitHub permissions or changing repository access.
              </p>
            </div>
            <Button
              disabled={isConnecting}
              onClick={() => void handleConnectGitHub()}
              type="button"
            >
              {isConnecting ? (
                <Loader2 aria-hidden="true" className="animate-spin" />
              ) : (
                <GitPullRequest aria-hidden="true" />
              )}
              {connections.length > 0
                ? "Connect another account"
                : "Connect GitHub App"}
            </Button>
          </div>

          {error ? (
            <div className="grid gap-3">
              <ErrorNotice message={error} />
              <Button
                className="w-fit"
                onClick={() => void loadConnections()}
                type="button"
                variant="secondary"
              >
                <RefreshCw aria-hidden="true" />
                Retry
              </Button>
            </div>
          ) : null}

          {isLoading ? <ConnectionsSkeleton /> : null}

          {!isLoading && !error && connections.length === 0 ? (
            <p className="rounded-md border border-dashed border-border p-5 text-sm text-muted-foreground">
              No provider account is connected to RepoGuard yet.
            </p>
          ) : null}

          {!isLoading && !error
            ? connections.map((connection) => (
                <ProviderConnectionRow
                  connection={connection}
                  isSyncing={syncingId === connection.id}
                  key={connection.id}
                  onDisconnect={() =>
                    setConnectionPendingDisconnect(connection)
                  }
                  onSync={() => void handleSyncConnection(connection)}
                />
              ))
            : null}
        </CardContent>
      </Card>

      <ConfirmationDialog
        confirmLabel="Disconnect"
        description={
          connectionPendingDisconnect
            ? `This removes @${connectionPendingDisconnect.account_login} from RepoGuard and disables publishing through this connection. The GitHub App remains installed on GitHub.`
            : ""
        }
        isPending={disconnectingId !== null}
        onCancel={() => setConnectionPendingDisconnect(null)}
        onConfirm={() => void handleDisconnectConnection()}
        open={connectionPendingDisconnect !== null}
        pendingLabel="Disconnecting..."
        title="Disconnect GitHub account?"
      />
    </>
  );
}

function ProviderConnectionRow({
  connection,
  isSyncing,
  onDisconnect,
  onSync,
}: {
  connection: ProviderConnection;
  isSyncing: boolean;
  onDisconnect: () => void;
  onSync: () => void;
}) {
  const repositoryAccess =
    connection.repository_selection === "all"
      ? "All repositories"
      : connection.repository_selection === "selected"
        ? "Selected repositories"
        : "Repository access not reported";

  return (
    <article className="grid gap-4 rounded-md border border-border bg-background p-4 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="truncate text-base font-semibold">
            @{connection.account_login}
          </h2>
          <span className="rounded-full border border-border bg-card px-2 py-0.5 text-xs font-medium text-muted-foreground">
            {connection.account_type ?? "GitHub account"}
          </span>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          {repositoryAccess} · Installation {connection.installation_id}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          disabled={isSyncing}
          onClick={onSync}
          size="sm"
          type="button"
          variant="secondary"
        >
          <RefreshCw
            aria-hidden="true"
            className={isSyncing ? "animate-spin" : undefined}
          />
          {isSyncing ? "Syncing..." : "Sync now"}
        </Button>
        <Button asChild size="sm" variant="outline">
          <a
            href={buildGitHubInstallationManageUrl(connection)}
            rel="noreferrer"
            target="_blank"
          >
            <ExternalLink aria-hidden="true" />
            Manage on GitHub
          </a>
        </Button>
        <Button
          onClick={onDisconnect}
          size="sm"
          type="button"
          variant="destructive"
        >
          <Unplug aria-hidden="true" />
          Disconnect
        </Button>
      </div>
    </article>
  );
}

function ConnectionsSkeleton() {
  return (
    <div className="grid gap-3 rounded-md border border-border bg-background p-4">
      <div className="h-5 w-40 animate-pulse rounded bg-muted" />
      <div className="h-4 w-72 max-w-full animate-pulse rounded bg-muted" />
      <div className="h-10 w-full animate-pulse rounded bg-muted" />
    </div>
  );
}

function ErrorNotice({ message }: { message: string }) {
  return (
    <div
      className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive"
      role="alert"
    >
      {message}
    </div>
  );
}
