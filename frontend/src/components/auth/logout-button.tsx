"use client";

import axios from "axios";
import { LogOut, ShieldOff, type LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button, type ButtonProps } from "@/components/ui/button";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { publishAuthEvent } from "@/lib/auth-events";
import { clearSessionMarker } from "@/lib/session-marker";
import { useAppDispatch } from "@/store/hooks";
import { clearCredentials } from "@/store/slices/authSlice";

type LogoutActionButtonProps = {
  className?: string;
  endpoint: "/auth/logout" | "/auth/logout-all";
  errorMessage: string;
  icon: LucideIcon;
  label: string;
  showLabel?: boolean;
  variant: ButtonProps["variant"];
};

function LogoutActionButton({
  className,
  endpoint,
  errorMessage,
  icon: Icon,
  label,
  showLabel = true,
  variant,
}: LogoutActionButtonProps) {
  const dispatch = useAppDispatch();
  const router = useRouter();
  const [isPending, setIsPending] = useState(false);

  function clearLocalSession() {
    dispatch(clearCredentials());
    clearSessionMarker();
    publishAuthEvent("session-cleared");
    router.replace("/login");
  }

  async function handleLogout() {
    setIsPending(true);

    try {
      await api.post(endpoint);
      clearLocalSession();
    } catch (error) {
      toast.error(getApiErrorMessage(error, errorMessage));

      if (axios.isAxiosError(error) && error.response?.status === 401) {
        clearLocalSession();
      }
    } finally {
      setIsPending(false);
    }
  }

  return (
    <Button
      className={className}
      disabled={isPending}
      onClick={handleLogout}
      type="button"
      variant={variant}
    >
      <Icon aria-hidden="true" />
      <span className={showLabel ? undefined : "sr-only"}>{label}</span>
    </Button>
  );
}

type LogoutButtonProps = {
  className?: string;
  icon?: LucideIcon;
  showLabel?: boolean;
  variant?: ButtonProps["variant"];
};

export function LogoutButton({
  className,
  icon = LogOut,
  showLabel = true,
  variant = "secondary",
}: LogoutButtonProps) {
  return (
    <LogoutActionButton
      className={className}
      endpoint="/auth/logout"
      errorMessage="Unable to end the server session."
      icon={icon}
      label="Logout"
      showLabel={showLabel}
      variant={variant}
    />
  );
}

export function LogoutAllButton({
  className,
  showLabel = true,
}: {
  className?: string;
  showLabel?: boolean;
}) {
  return (
    <LogoutActionButton
      className={className}
      endpoint="/auth/logout-all"
      errorMessage="Unable to end all sessions."
      icon={ShieldOff}
      label="Logout all"
      showLabel={showLabel}
      variant="destructive"
    />
  );
}
