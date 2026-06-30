import type { ReactNode } from "react";

import { AuthGuard } from "@/components/auth/auth-guard";

type DashboardLayoutProps = {
  children: ReactNode;
};

export default function DashboardLayout({ children }: DashboardLayoutProps) {
  return <AuthGuard>{children}</AuthGuard>;
}
