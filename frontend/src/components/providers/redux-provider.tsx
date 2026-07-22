"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { Provider } from "react-redux";

import { api } from "@/lib/api";
import { subscribeToAuthEvents } from "@/lib/auth-events";
import { clearSessionMarker, setSessionMarker } from "@/lib/session-marker";
import { store } from "@/store";
import { clearCredentials, setCredentials } from "@/store/slices/authSlice";
import type { AuthTokenResponse } from "@/types/auth";

type ReduxProviderProps = {
  children: ReactNode;
};

const AuthBootstrapContext = createContext(false);

export function useAuthBootstrap() {
  return useContext(AuthBootstrapContext);
}

export function ReduxProvider({ children }: ReduxProviderProps) {
  const [hasBootstrapped, setHasBootstrapped] = useState(false);

  useEffect(() => {
    async function refreshSession() {
      try {
        const response = await api.post<AuthTokenResponse>(
          "/auth/refresh",
          undefined,
          { skipAuthRefresh: true },
        );

        store.dispatch(
          setCredentials({
            user: response.data.user,
          }),
        );
        setSessionMarker();
      } catch {
        clearSessionMarker();
        // No refresh cookie is a normal anonymous state.
      } finally {
        setHasBootstrapped(true);
      }
    }

    void refreshSession();

    return subscribeToAuthEvents((eventType) => {
      if (eventType === "session-cleared") {
        store.dispatch(clearCredentials());
        clearSessionMarker();
        return;
      }

      void refreshSession();
    });
  }, []);

  return (
    <Provider store={store}>
      <AuthBootstrapContext.Provider value={hasBootstrapped}>
        {children}
      </AuthBootstrapContext.Provider>
    </Provider>
  );
}
