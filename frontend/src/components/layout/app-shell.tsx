"use client";

import {
  CirclePlay,
  GitFork,
  LayoutDashboard,
  LogOut,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  X,
  type LucideIcon,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { LogoutButton } from "@/components/auth/logout-button";
import { ThemeToggle } from "@/components/layout/theme-toggle";
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
  const [isDesktopNavCollapsed, setIsDesktopNavCollapsed] = useState(false);
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <DesktopSidebar
        isCollapsed={isDesktopNavCollapsed}
        onToggleCollapsed={() =>
          setIsDesktopNavCollapsed((isCollapsed) => !isCollapsed)
        }
        pathname={pathname}
      />

      {isMobileNavOpen ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            aria-label="Close navigation"
            className="absolute inset-0 bg-background/80 backdrop-blur-sm"
            onClick={() => setIsMobileNavOpen(false)}
            type="button"
          />
          <aside className="relative flex h-full w-72 flex-col border-r border-border bg-card shadow-2xl shadow-foreground/10">
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

          <div className="min-w-0 flex-1" />

          <div className="ml-auto flex items-center gap-2">
            <ThemeToggle />
          </div>
        </header>

        <main className="min-h-0 min-w-0 flex-1 overflow-x-hidden overflow-y-auto">
          <div className="mx-auto flex w-full min-w-0 max-w-[1600px] flex-col gap-6 px-4 py-7 text-[15px] sm:px-6 lg:px-8 xl:px-10">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}

function DesktopSidebar({
  isCollapsed,
  onToggleCollapsed,
  pathname,
}: {
  isCollapsed: boolean;
  onToggleCollapsed: () => void;
  pathname: string;
}) {
  return (
    <aside
      className={cn(
        "hidden shrink-0 border-r border-border bg-card transition-[width] duration-200 ease-in-out lg:flex lg:flex-col",
        isCollapsed ? "w-20" : "w-72",
      )}
    >
      <ShellBrand isCollapsed={isCollapsed} />
      <ShellNav isCollapsed={isCollapsed} pathname={pathname} />
      <div
        className={cn(
          "flex border-t border-border p-3",
          isCollapsed ? "justify-center" : "justify-end",
        )}
      >
        <Button
          aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="size-8 rounded-md border border-border/80 bg-background/70 p-0 text-muted-foreground shadow-sm shadow-foreground/[0.03] hover:border-primary/30 hover:bg-accent hover:text-accent-foreground"
          onClick={onToggleCollapsed}
          size="icon"
          title={isCollapsed ? "Expand panel" : "Collapse panel"}
          type="button"
          variant="ghost"
        >
          {isCollapsed ? (
            <PanelLeftOpen aria-hidden="true" />
          ) : (
            <PanelLeftClose aria-hidden="true" />
          )}
          <span className="sr-only">
            {isCollapsed ? "Expand panel" : "Collapse panel"}
          </span>
        </Button>
      </div>
      <ShellSessionActions isCollapsed={isCollapsed} />
    </aside>
  );
}

function ShellBrand({
  isCollapsed = false,
  onClose,
}: {
  isCollapsed?: boolean;
  onClose?: () => void;
}) {
  return (
    <div
      className={cn(
        "flex h-16 items-center border-b border-border px-4",
        isCollapsed ? "justify-center" : "justify-between",
      )}
    >
      <Link
        aria-label="RepoReview dashboard"
        className={cn(
          "flex min-w-0 items-center gap-3",
          isCollapsed && "justify-center",
        )}
        href="/dashboard"
      >
        <Image
          alt=""
          aria-hidden="true"
          className="size-10 shrink-0 rounded-[9px]"
          height={40}
          priority
          src="/repo-review-icon.svg"
          width={40}
        />
        <span className={cn("min-w-0", isCollapsed && "hidden")}>
          <span className="block truncate text-[15px] font-bold">
            RepoReview
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
  isCollapsed = false,
  onNavigate,
  pathname,
}: {
  isCollapsed?: boolean;
  onNavigate?: () => void;
  pathname: string;
}) {
  return (
    <nav
      className={cn(
        "flex flex-1 flex-col gap-1.5 px-3 py-4",
        isCollapsed && "items-center",
      )}
      aria-label="Primary"
    >
      {NAV_ITEMS.map((item) => {
        const isActive =
          pathname === item.href || pathname.startsWith(`${item.href}/`);
        const Icon = item.icon;

        return (
          <Link
            aria-current={isActive ? "page" : undefined}
            title={isCollapsed ? item.label : undefined}
            className={cn(
              "flex h-11 items-center gap-3 rounded-md px-3 text-[15px] font-semibold text-muted-foreground transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
              isCollapsed && "w-11 justify-center px-0",
              isActive
                ? "bg-accent text-accent-foreground"
                : "hover:bg-accent hover:text-accent-foreground",
            )}
            href={item.href}
            key={item.href}
            onClick={onNavigate}
          >
            <Icon aria-hidden="true" className="size-4" />
            <span className={isCollapsed ? "sr-only" : undefined}>
              {item.label}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

function ShellSessionActions({ isCollapsed = false }: { isCollapsed?: boolean }) {
  return (
    <section className="border-t border-border p-3" aria-label="Session">
      <LogoutButton
        className={cn(
          "w-full border border-border bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground",
          isCollapsed ? "justify-center px-0" : "justify-start",
        )}
        icon={LogOut}
        showLabel={!isCollapsed}
        variant="outline"
      />
    </section>
  );
}
