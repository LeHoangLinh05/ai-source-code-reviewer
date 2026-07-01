import { ShieldOff } from "lucide-react";

import { LogoutAllButton } from "@/components/auth/logout-button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function SettingsPage() {
  return (
    <>
      <header className="flex flex-col gap-4 border-b border-border pb-5">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">
            Workspace controls
          </p>
          <h1 className="mt-2 text-2xl font-extrabold tracking-normal">
            Settings
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage account session behavior and workspace-level controls.
          </p>
        </div>
      </header>

      <section className="grid gap-4 lg:grid-cols-[1fr_0.8fr]">
        <Card>
          <CardHeader>
            <CardTitle>Session Management</CardTitle>
            <CardDescription>
              End active sessions without changing repository or review data.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border border-border bg-background p-4">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-start gap-3">
                  <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-card text-muted-foreground">
                    <ShieldOff aria-hidden="true" className="size-4" />
                  </span>
                  <div>
                    <h2 className="text-sm font-semibold">Logout all sessions</h2>
                    <p className="mt-1 max-w-xl text-sm text-muted-foreground">
                      Revoke server-side sessions on every device. Your current
                      browser will return to login after the request succeeds.
                    </p>
                  </div>
                </div>
                <LogoutAllButton className="border-rose-900/60 bg-background text-slate-200 hover:bg-rose-950/30 hover:text-rose-100" />
              </div>
            </div>
          </CardContent>
        </Card>
      </section>
    </>
  );
}
