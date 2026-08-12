import type { User } from "@/types/auth";

export function getUserDisplayName(user: User | null) {
  return user?.full_name?.trim() || "RepoReview user";
}

export function getUserInitials(user: User | null) {
  const displayName = user?.full_name?.trim();

  if (displayName) {
    const initials = displayName
      .split(/\s+/)
      .slice(0, 2)
      .map((namePart) => namePart[0])
      .join("");

    return initials.toUpperCase();
  }

  return (user?.email[0] ?? "U").toUpperCase();
}
