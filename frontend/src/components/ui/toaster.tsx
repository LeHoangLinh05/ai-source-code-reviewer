"use client";

import { Toaster as Sonner } from "sonner";

export function Toaster() {
  return (
    <Sonner
      closeButton
      richColors
      toastOptions={{
        classNames: {
          toast:
            "border border-border bg-card text-card-foreground shadow-lg shadow-black/20",
        },
      }}
    />
  );
}
