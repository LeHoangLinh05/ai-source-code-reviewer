"use client";

import axios from "axios";
import { LogOut, ShieldOff, type LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button, type ButtonProps } from "@/components/ui/button";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { clearSessionMarker } from "@/lib/session-marker";
import { useAppDispatch } from "@/store/hooks";
import { clearCredentials } from "@/store/slices/authSlice";

type LogoutActionButtonProps = {
  className?: string;
  endpoint: "/auth/logout" | "/auth/logout-all";
  errorMessage: string;
  icon: LucideIcon;
  label: string;
  variant: ButtonProps["variant"];
};

function LogoutActionButton({
  className,
  endpoint,
  errorMessage,
  icon: Icon,
  label,
  variant,
}: LogoutActionButtonProps) {
  const dispatch = useAppDispatch();
  const router = useRouter();
  const [isPending, setIsPending] = useState(false);

  function clearLocalSession() {
    dispatch(clearCredentials());
    clearSessionMarker();
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
      variant={variant}
    >
      <Icon aria-hidden="true" />
      {label}
    </Button>
  );
}

type LogoutButtonProps = {
  className?: string;
  icon?: LucideIcon;
  variant?: ButtonProps["variant"];
};

export function LogoutButton({
  className,
  icon = LogOut,
  variant = "secondary",
}: LogoutButtonProps) {
  return (
    <LogoutActionButton
      className={className}
      endpoint="/auth/logout"
      errorMessage="Unable to end the server session."
      icon={icon}
      label="Logout"
      variant={variant}
    />
  );
}

export function LogoutAllButton({ className }: { className?: string }) {
  return (
    <LogoutActionButton
      className={className}
      endpoint="/auth/logout-all"
      errorMessage="Unable to end all sessions."
      icon={ShieldOff}
      label="Logout all"
      variant="destructive"
    />
  );
}
