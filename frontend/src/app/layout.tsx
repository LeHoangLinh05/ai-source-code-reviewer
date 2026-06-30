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

export default function RootLayout({ children }: RootLayoutProps) {
  return (
    <html lang="en" className="dark">
      <body>
        <ReduxProvider>
          {children}
          <Toaster />
        </ReduxProvider>
      </body>
    </html>
  );
}
