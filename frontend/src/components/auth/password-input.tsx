"use client";

import { Eye, EyeOff } from "lucide-react";
import { forwardRef, useState, type ComponentProps } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type PasswordInputProps = Omit<ComponentProps<typeof Input>, "type"> & {
  toggleLabel?: string;
};

export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ className, toggleLabel = "Toggle password visibility", ...props }, ref) => {
    const [isVisible, setIsVisible] = useState(false);
    const Icon = isVisible ? EyeOff : Eye;

    return (
      <div className="relative">
        <Input
          className={cn("pr-11", className)}
          ref={ref}
          type={isVisible ? "text" : "password"}
          {...props}
        />
        <button
          aria-label={toggleLabel}
          className="absolute right-2 top-1/2 inline-flex size-8 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onClick={() => {
            setIsVisible((currentValue) => !currentValue);
          }}
          type="button"
        >
          <Icon aria-hidden="true" className="size-4" />
        </button>
      </div>
    );
  },
);

PasswordInput.displayName = "PasswordInput";
