"use client";

import { LogOut, Settings } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";

import { LogoutButton } from "@/components/auth/logout-button";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getUserDisplayName, getUserInitials } from "@/lib/user-display";
import { useAppSelector } from "@/store/hooks";

export function AccountMenu() {
  const menuId = useId();
  const user = useAppSelector((state) => state.auth.user);
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    function handlePointerDown(event: PointerEvent) {
      if (!menuRef.current?.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  return (
    <div className="relative" ref={menuRef}>
      <button
        aria-controls={isOpen ? menuId : undefined}
        aria-expanded={isOpen}
        aria-haspopup="menu"
        aria-label="Open account menu"
        className="flex size-10 items-center justify-center rounded-full border border-border bg-background text-sm font-bold text-foreground shadow-sm shadow-foreground/5 transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        onClick={() => setIsOpen((currentValue) => !currentValue)}
        title="Account"
        type="button"
      >
        {getUserInitials(user)}
      </button>

      {isOpen ? (
        <div
          className="absolute right-0 top-12 z-50 w-72 rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-xl shadow-foreground/10"
          id={menuId}
          role="menu"
        >
          <div className="flex items-center gap-3 border-b border-border px-2 pb-3 pt-1.5">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-bold text-primary-foreground">
              {getUserInitials(user)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">
                {getUserDisplayName(user)}
              </p>
              <p className="truncate text-xs text-muted-foreground">
                {user?.email ?? "Signed in"}
              </p>
            </div>
          </div>

          <div className="grid gap-1 py-2">
            <Button
              asChild
              className="h-10 w-full justify-start px-3"
              variant="ghost"
            >
              <Link href="/settings" onClick={() => setIsOpen(false)} role="menuitem">
                <Settings aria-hidden="true" />
                Settings
              </Link>
            </Button>
            <LogoutButton
              className={cn(
                "h-10 w-full justify-start px-3 text-muted-foreground",
                "hover:bg-accent hover:text-accent-foreground",
              )}
              icon={LogOut}
              variant="ghost"
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
