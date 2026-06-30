import { NextResponse, type NextRequest } from "next/server";

const SESSION_MARKER_COOKIE = "repoguard_session";

export function middleware(request: NextRequest) {
  const hasSessionMarker = request.cookies.has(SESSION_MARKER_COOKIE);

  if (hasSessionMarker) {
    return NextResponse.next();
  }

  const loginUrl = request.nextUrl.clone();
  loginUrl.pathname = "/login";
  loginUrl.searchParams.set("next", request.nextUrl.pathname);

  return NextResponse.redirect(loginUrl);
}

export const config = {
  matcher: ["/dashboard/:path*"],
};
