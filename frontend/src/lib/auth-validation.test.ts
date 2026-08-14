import { describe, expect, it } from "vitest";

import {
  emailSchema,
  loginPasswordSchema,
  strongPasswordSchema,
} from "@/lib/auth-validation";

describe("email validation", () => {
  it("normalizes a valid email address", () => {
    expect(emailSchema.parse("User.Example+test@example.com")).toBe(
      "user.example+test@example.com",
    );
  });

  it.each([
    " user@example.com",
    "user@example.com ",
    "user..name@example.com",
    `${"a".repeat(65)}@example.com`,
    `user@${"a".repeat(64)}.com`,
    "tên@example.com",
  ])("rejects an invalid address: %s", (email) => {
    expect(emailSchema.safeParse(email).success).toBe(false);
  });
});

describe("password validation", () => {
  it("requires strong passwords for account creation and password changes", () => {
    expect(strongPasswordSchema.safeParse("Valid@123").success).toBe(true);
    expect(strongPasswordSchema.safeParse("existing-password").success).toBe(
      false,
    );
  });

  it("accepts existing password formats at login", () => {
    expect(loginPasswordSchema.safeParse("existing-password").success).toBe(
      true,
    );
  });
});
