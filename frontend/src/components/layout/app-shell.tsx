"use client";

import {
  Bell,
  CirclePlay,
  GitFork,
  LayoutDashboard,
  LogOut,
  Menu,
  Search,
  Settings,
  ShieldCheck,
  X,
  type LucideIcon,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { LogoutButton } from "@/components/auth/logout-button";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV_ITEMS: Array<{
  href: string;
  icon: LucideIcon;
  label: string;
}> = [
  { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { href: "/repositories", icon: GitFork, label: "Repositories" },
  { href: "/reviews", icon: CirclePlay, label: "Reviews" },
  { href: "/settings", icon: Settings, label: "Settings" },
];

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname();
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <DesktopSidebar pathname={pathname} />

      {isMobileNavOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            aria-label="Close navigation"
            className="absolute inset-0 bg-background/80 backdrop-blur-sm"
            onClick={() => setIsMobileNavOpen(false)}
            type="button"
          />
          <aside className="relative flex h-full w-72 flex-col border-r border-border bg-card shadow-2xl shadow-black/40">
            <ShellBrand onClose={() => setIsMobileNavOpen(false)} />
            <ShellNav
              onNavigate={() => setIsMobileNavOpen(false)}
              pathname={pathname}
            />
            <ShellSessionActions />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border bg-card px-4 lg:px-6">
          <Button
            aria-label="Open navigation"
            className="lg:hidden"
            onClick={() => setIsMobileNavOpen(true)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Menu aria-hidden="true" />
          </Button>

          <div className="relative hidden min-w-0 flex-1 sm:block">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
            />
            <input
              aria-label="Search repositories, jobs, or files"
              className="h-9 w-full max-w-xl rounded-md border border-input bg-background pl-9 pr-3 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950"
              placeholder="Search repositories, jobs, or files"
              type="search"
            />
          </div>

          <div className="ml-auto flex items-center gap-2">
            <Button aria-label="Notifications" size="icon" variant="ghost">
              <Bell aria-hidden="true" />
            </Button>
          </div>
        </header>

        <main className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 px-4 py-6 sm:px-6 lg:px-8">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function DesktopSidebar({ pathname }: { pathname: string }) {
  return (
    <aside className="hidden w-64 shrink-0 border-r border-border bg-card lg:flex lg:flex-col">
      <ShellBrand />
      <ShellNav pathname={pathname} />
      <ShellSessionActions />
    </aside>
  );
}

function ShellBrand({ onClose }: { onClose?: () => void }) {
  return (
    <div className="flex h-16 items-center justify-between border-b border-border px-4">
      <Link className="flex min-w-0 items-center gap-3" href="/dashboard">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-background text-slate-200">
          <ShieldCheck aria-hidden="true" className="size-5" />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-semibold">
            RepoGuard AI
          </span>
          <span className="block truncate text-xs text-muted-foreground">
            Source security review
          </span>
        </span>
      </Link>
      {onClose ? (
        <Button
          aria-label="Close navigation"
          onClick={onClose}
          size="icon"
          type="button"
          variant="ghost"
        >
          <X aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  );
}

function ShellNav({
  onNavigate,
  pathname,
}: {
  onNavigate?: () => void;
  pathname: string;
}) {
  return (
    <nav className="flex flex-1 flex-col gap-1 px-3 py-4" aria-label="Primary">
      {NAV_ITEMS.map((item) => {
        const isActive =
          pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;

        return (
          <Link
            aria-current={isActive ? "page" : undefined}
            className={cn(
              "flex h-10 items-center gap-3 rounded-md px-3 text-sm font-medium text-muted-foreground transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 focus-visible:ring-offset-slate-950",
              isActive
                ? "bg-slate-800 text-slate-50"
                : "hover:bg-slate-800/80 hover:text-foreground",
            )}
            href={item.href}
            key={item.href}
            onClick={onNavigate}
          >
            <Icon aria-hidden="true" className="size-4" />
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}

function ShellSessionActions() {
  return (
    <section className="border-t border-border p-3" aria-label="Session">
      <LogoutButton
        className="w-full justify-start border border-border bg-background text-muted-foreground hover:bg-slate-800/80 hover:text-foreground"
        icon={LogOut}
        variant="outline"
      />
    </section>
  );
}
