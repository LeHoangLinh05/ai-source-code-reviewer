import { z } from "zod";

const EMAIL_PATTERN =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;
const ASCII_PATTERN = /^[\x00-\x7F]+$/;
const PRINTABLE_ASCII_PATTERN = /^[\x20-\x7E]+$/;
const MAX_EMAIL_LENGTH = 255;
export const MAX_PASSWORD_LENGTH = 128;
const MIN_PASSWORD_LENGTH = 8;
const LEGACY_TEST_LOGIN_EMAIL = "user1@gmail.com";
const LEGACY_TEST_LOGIN_PASSWORD = "12345678";
const PASSWORD_SPECIAL_CHARACTERS = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/;

export const emailSchema = z
  .string()
  .min(1, "Email is required.")
  .max(MAX_EMAIL_LENGTH, "Email is too long.")
  .regex(EMAIL_PATTERN, "Enter a valid email address.")
  .refine((email) => email === email.trim(), {
    message: "Email must not contain leading or trailing spaces.",
  })
  .refine((email) => ASCII_PATTERN.test(email), {
    message: "Email must contain ASCII characters only.",
  })
  .refine((email) => {
    const [localPart] = email.toLowerCase().split("@", 1);
    return (
      !localPart.startsWith(".") &&
      !localPart.endsWith(".") &&
      !localPart.includes("..")
    );
  }, "Enter a valid email address.")
  .refine((email) => {
    const domain = email.toLowerCase().split("@").at(1);
    return domain
      ?.split(".")
      .every((label) => !label.startsWith("-") && !label.endsWith("-"));
  }, "Enter a valid email address.");

export const strongPasswordSchema = z
  .string()
  .min(MIN_PASSWORD_LENGTH, "Password must be at least 8 characters.")
  .max(MAX_PASSWORD_LENGTH, "Password is too long.")
  .refine((password) => password === password.trim(), {
    message: "Password must not contain leading or trailing spaces.",
  })
  .refine((password) => PRINTABLE_ASCII_PATTERN.test(password), {
    message: "Password must contain printable ASCII characters only.",
  })
  .refine((password) => !/\s/.test(password), {
    message: "Password must not contain whitespace.",
  })
  .refine((password) => /[a-z]/.test(password), {
    message: "Password must contain a lowercase letter.",
  })
  .refine((password) => /[A-Z]/.test(password), {
    message: "Password must contain an uppercase letter.",
  })
  .refine((password) => /\d/.test(password), {
    message: "Password must contain a number.",
  })
  .refine((password) => PASSWORD_SPECIAL_CHARACTERS.test(password), {
    message: "Password must contain a special character.",
  });

export function isLegacyTestLoginCredentials(email: string, password: string) {
  return (
    email.toLowerCase() === LEGACY_TEST_LOGIN_EMAIL &&
    password === LEGACY_TEST_LOGIN_PASSWORD
  );
}
