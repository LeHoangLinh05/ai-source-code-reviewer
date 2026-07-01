"use client";

import axios from "axios";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useAuthBootstrap } from "@/components/providers/redux-provider";
import { api } from "@/lib/api";
import { clearSessionMarker } from "@/lib/session-marker";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { clearCredentials } from "@/store/slices/authSlice";

type AuthGuardProps = {
  children: ReactNode;
};

const SESSION_CHECK_INTERVAL_MS = 15_000;

export function AuthGuard({ children }: AuthGuardProps) {
  const dispatch = useAppDispatch();
  const router = useRouter();
  const pathname = usePathname();
  const hasBootstrapped = useAuthBootstrap();
  const isAuthenticated = useAppSelector((state) => state.auth.isAuthenticated);

  useEffect(() => {
    if (hasBootstrapped && !isAuthenticated) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [hasBootstrapped, isAuthenticated, pathname, router]);

  useEffect(() => {
    if (!hasBootstrapped || !isAuthenticated) {
      return;
    }

    let isCancelled = false;

    function clearInvalidSession() {
      if (isCancelled) {
        return;
      }

      dispatch(clearCredentials());
      clearSessionMarker();
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }

    async function validateSession() {
      try {
        await api.get("/auth/me");
      } catch (error) {
        if (axios.isAxiosError(error) && error.response?.status === 401) {
          clearInvalidSession();
        }
      }
    }

    function validateVisibleSession() {
      if (document.visibilityState === "visible") {
        void validateSession();
      }
    }

    const intervalId = window.setInterval(
      () => void validateSession(),
      SESSION_CHECK_INTERVAL_MS,
    );

    window.addEventListener("focus", validateVisibleSession);
    document.addEventListener("visibilitychange", validateVisibleSession);

    return () => {
      isCancelled = true;
      window.clearInterval(intervalId);
      window.removeEventListener("focus", validateVisibleSession);
      document.removeEventListener("visibilitychange", validateVisibleSession);
    };
  }, [dispatch, hasBootstrapped, isAuthenticated, pathname, router]);

  if (!hasBootstrapped || !isAuthenticated) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-background px-4 text-sm text-muted-foreground">
        Loading session...
      </main>
    );
  }

  return children;
}
