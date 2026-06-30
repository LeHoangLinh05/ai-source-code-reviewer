import axios from "axios";

type ApiErrorBody = {
  detail?: string;
};

export function getApiErrorMessage(error: unknown, fallback: string) {
  if (!axios.isAxiosError<ApiErrorBody>(error)) {
    return fallback;
  }

  return error.response?.data.detail ?? fallback;
}
