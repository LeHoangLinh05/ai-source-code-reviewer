import type { ReactNode } from "react";

import { AuthGuard } from "@/components/auth/auth-guard";
import { AppShell } from "@/components/layout/app-shell";

type SettingsLayoutProps = {
  children: ReactNode;
};

export default function SettingsLayout({ children }: SettingsLayoutProps) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
