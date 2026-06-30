const SESSION_MARKER_COOKIE = "repoguard_session";
const REFRESH_TOKEN_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

export function setSessionMarker() {
  if (typeof document === "undefined") {
    return;
  }

  const secure = window.location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${SESSION_MARKER_COOKIE}=1; Path=/; Max-Age=${REFRESH_TOKEN_MAX_AGE_SECONDS}; SameSite=Lax${secure}`;
}

export function clearSessionMarker() {
  if (typeof document === "undefined") {
    return;
  }

  document.cookie = `${SESSION_MARKER_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
}

export { SESSION_MARKER_COOKIE };
