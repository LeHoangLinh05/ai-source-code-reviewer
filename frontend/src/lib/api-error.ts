import axios from "axios";

type ApiErrorDetailItem = {
  msg?: string;
  message?: string;
};

type ApiErrorBody = {
  detail?: string | ApiErrorDetailItem[] | Record<string, unknown>;
  message?: string;
};

export function getApiErrorMessage(error: unknown, fallback: string): string {
  if (!axios.isAxiosError<ApiErrorBody>(error)) {
    if (error instanceof Error && error.message) {
      return error.message;
    }
    return fallback;
  }

  const data = error.response?.data;
  if (!data) {
    return fallback;
  }

  const detail = data.detail;
  if (typeof detail === "string" && detail.trim().length > 0) {
    return detail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }
        if (item && typeof item === "object") {
          return item.msg || item.message || "";
        }
        return "";
      })
      .filter((msg) => msg.trim().length > 0);

    if (messages.length > 0) {
      return messages.join("; ");
    }
  }

  if (typeof data.message === "string" && data.message.trim().length > 0) {
    return data.message;
  }

  return fallback;
}
