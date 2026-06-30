"use client";

import axios from "axios";
import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { clearSessionMarker } from "@/lib/session-marker";
import { useAppDispatch } from "@/store/hooks";
import { clearCredentials } from "@/store/slices/authSlice";

export function LogoutButton() {
  const dispatch = useAppDispatch();
  const router = useRouter();

  function clearLocalSession() {
    dispatch(clearCredentials());
    clearSessionMarker();
    router.replace("/login");
  }

  async function handleLogout() {
    try {
      await api.post("/auth/logout");
      clearLocalSession();
    } catch (error) {
      toast.error(getApiErrorMessage(error, "Unable to end the server session."));

      if (axios.isAxiosError(error) && error.response?.status === 401) {
        clearLocalSession();
      }
    }
  }

  return (
    <Button onClick={handleLogout} variant="secondary">
      <LogOut aria-hidden="true" />
      Logout
    </Button>
  );
}
