import { describe, expect, it } from "vitest";

import {
  buildGitHubInstallUrl,
  getSafeProviderReturnPath,
} from "@/lib/providers";

describe("provider helpers", () => {
  it("appends state to the GitHub install URL", () => {
    expect(
      buildGitHubInstallUrl(
        "https://github.com/apps/reporeview-bot/installations/new",
        "/reviews/123/fixes",
      ),
    ).toBe(
      "https://github.com/apps/reporeview-bot/installations/new?state=%2Freviews%2F123%2Ffixes",
    );
  });

  it("rejects unsafe return paths", () => {
    expect(getSafeProviderReturnPath("/reviews/123/fixes")).toBe(
      "/reviews/123/fixes",
    );
    expect(getSafeProviderReturnPath("https://evil.example")).toBe("/reviews");
    expect(getSafeProviderReturnPath("//evil.example")).toBe("/reviews");
    expect(getSafeProviderReturnPath("")).toBe("/reviews");
  });
});
