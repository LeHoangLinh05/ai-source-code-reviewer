import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from "axios";

import { store } from "@/store";
import { clearCredentials, setAccessToken } from "@/store/slices/authSlice";
import type { AuthResponse } from "@/types/auth";
import { clearSessionMarker } from "@/lib/session-marker";

type RetryableRequestConfig = InternalAxiosRequestConfig & {
  _retry?: boolean;
  skipAuthRefresh?: boolean;
};

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api";

export const api = axios.create({
  baseURL: API_BASE_URL,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

let refreshRequest: Promise<string> | null = null;

function redirectToLogin() {
  if (typeof window !== "undefined") {
    window.location.replace("/login");
  }
}

function getAccessToken() {
  return store.getState().auth.accessToken;
}

async function refreshAccessToken() {
  refreshRequest ??= api
    .post<AuthResponse>(
      "/auth/refresh",
      undefined,
      {
        skipAuthRefresh: true,
      } as AxiosRequestConfig,
    )
    .then((response) => {
      const accessToken = response.data.access_token;

      store.dispatch(setAccessToken(accessToken));
      return accessToken;
    })
    .finally(() => {
      refreshRequest = null;
    });

  return refreshRequest;
}

api.interceptors.request.use((config) => {
  const accessToken = getAccessToken();

  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }

  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config as RetryableRequestConfig | undefined;

    if (
      error.response?.status !== 401 ||
      !originalRequest ||
      originalRequest._retry ||
      originalRequest.skipAuthRefresh
    ) {
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    try {
      const accessToken = await refreshAccessToken();
      originalRequest.headers.Authorization = `Bearer ${accessToken}`;

      return api(originalRequest);
    } catch (refreshError) {
      store.dispatch(clearCredentials());
      clearSessionMarker();
      redirectToLogin();

      return Promise.reject(refreshError);
    }
  },
);
