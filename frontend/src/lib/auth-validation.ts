import { z } from "zod";

const EMAIL_PATTERN =
  /^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$/;
const ASCII_PATTERN = /^[\x00-\x7F]+$/;
const PRINTABLE_ASCII_PATTERN = /^[\x20-\x7E]+$/;
const MAX_EMAIL_LENGTH = 254;
const MAX_EMAIL_LOCAL_PART_LENGTH = 64;
const MAX_EMAIL_DOMAIN_LENGTH = 253;
const MAX_EMAIL_DOMAIN_LABEL_LENGTH = 63;
export const MAX_PASSWORD_LENGTH = 128;
const MIN_PASSWORD_LENGTH = 8;
const PASSWORD_SPECIAL_CHARACTERS = /[!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~]/;

export const emailSchema = z
  .string()
  .min(1, "Email is required.")
  .max(MAX_EMAIL_LENGTH, "Email is too long.")
  .refine((email) => email === email.trim(), {
    message: "Email must not contain leading or trailing spaces.",
  })
  .refine((email) => ASCII_PATTERN.test(email), {
    message: "Email must contain ASCII characters only.",
  })
  .refine((email) => EMAIL_PATTERN.test(email), {
    message: "Enter a valid email address.",
  })
  .refine((email) => {
    const [localPart] = email.toLowerCase().split("@", 1);
    return (
      localPart.length <= MAX_EMAIL_LOCAL_PART_LENGTH &&
      !localPart.startsWith(".") &&
      !localPart.endsWith(".") &&
      !localPart.includes("..")
    );
  }, "Enter a valid email address.")
  .refine((email) => {
    const domain = email.toLowerCase().split("@").at(1);
    return (
      domain !== undefined &&
      domain.length <= MAX_EMAIL_DOMAIN_LENGTH &&
      domain.split(".").every(
        (label) =>
          label.length <= MAX_EMAIL_DOMAIN_LABEL_LENGTH &&
          !label.startsWith("-") &&
          !label.endsWith("-"),
      )
    );
  }, "Enter a valid email address.")
  .transform((email) => email.toLowerCase());

export const loginPasswordSchema = z
  .string()
  .min(1, "Password is required.")
  .max(MAX_PASSWORD_LENGTH, "Password is too long.");

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
