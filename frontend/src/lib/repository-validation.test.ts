import { describe, expect, it } from "vitest";

import { isSupportedRepositoryUrl } from "@/lib/repository-validation";

describe("repository URL validation", () => {
  it.each([
    "https://github.com/example/backend-api",
    "https://github.com/example/backend-api.git",
  ])("accepts a full supported URL: %s", (url) => {
    expect(isSupportedRepositoryUrl(url)).toBe(true);
  });

  it.each([
    "not-a-url",
    "http://github.com/example/backend-api",
    "https://github.com/example",
    "https://user:token@github.com/example/backend-api",
    "https://github.com:8443/example/backend-api",
    "https://github.com/example/backend-api?tab=readme",
    "https://github.com/example/%2Frepository",
    "https://github.com/example/.git",
    "https://example.com/example/backend-api",
  ])("rejects an unsafe or incomplete URL: %s", (url) => {
    expect(isSupportedRepositoryUrl(url)).toBe(false);
  });
});
