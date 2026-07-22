import type { Metadata } from "next";
import type { ReactNode } from "react";

import { Toaster } from "@/components/ui/toaster";
import { ReduxProvider } from "@/components/providers/redux-provider";

import "./globals.css";

export const metadata: Metadata = {
  title: "RepoGuard AI",
  description: "AI source code review dashboard",
};

type RootLayoutProps = {
  children: ReactNode;
};

const themeScript = `
(() => {
  try {
    const storageKey = "repoguard-theme";
    const storedTheme = window.localStorage.getItem(storageKey);
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const theme = storedTheme === "light" || storedTheme === "dark"
      ? storedTheme
      : prefersDark
        ? "dark"
        : "light";

    document.documentElement.classList.toggle("dark", theme === "dark");
    document.documentElement.style.colorScheme = theme;
  } catch {
    document.documentElement.style.colorScheme =
      document.documentElement.classList.contains("dark") ? "dark" : "light";
  }
})();
`;

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <ReduxProvider>
          {children}
          <Toaster />
        </ReduxProvider>
      </body>
    </html>
  );
}
