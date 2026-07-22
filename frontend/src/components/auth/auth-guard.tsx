"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuthBootstrap } from "@/components/providers/redux-provider";
import { useAppSelector } from "@/store/hooks";

type AuthGuardProps = {
  children: ReactNode;
};

export function AuthGuard({ children }: AuthGuardProps) {
  const router = useRouter();
  const pathname = usePathname();
  const hasBootstrapped = useAuthBootstrap();
  const isAuthenticated = useAppSelector((state) => state.auth.isAuthenticated);

  useEffect(() => {
    if (hasBootstrapped && !isAuthenticated) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [hasBootstrapped, isAuthenticated, pathname, router]);

  if (!hasBootstrapped || !isAuthenticated) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background px-4 text-sm text-muted-foreground">
        Loading session...
      </main>
    );
  }

  return children;
}
