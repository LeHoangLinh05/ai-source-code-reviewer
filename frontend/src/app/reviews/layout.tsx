import type { ReactNode } from "react";

import { AuthGuard } from "@/components/auth/auth-guard";
import { AppShell } from "@/components/layout/app-shell";

type ReviewsLayoutProps = {
  children: ReactNode;
};

export default function ReviewsLayout({ children }: ReviewsLayoutProps) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  );
}
