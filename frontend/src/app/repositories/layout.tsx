import type { ReactNode } from "react";

import { AuthGuard } from "@/components/auth/auth-guard";
import { AppShell } from "@/components/layout/app-shell";

type RepositoriesLayoutProps = {
  children: ReactNode;
};

export default function RepositoriesLayout({
  children,
}: RepositoriesLayoutProps) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
