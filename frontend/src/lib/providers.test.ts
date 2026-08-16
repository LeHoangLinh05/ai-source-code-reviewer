import { describe, expect, it } from "vitest";

import {
  buildGitHubInstallUrl,
  buildGitHubInstallationManageUrl,
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

  it("builds personal GitHub installation management URLs", () => {
    expect(
      buildGitHubInstallationManageUrl({
        account_login: "octocat",
        account_type: "User",
        installation_id: "12345",
      }),
    ).toBe("https://github.com/settings/installations/12345");
  });

  it("builds organization GitHub installation management URLs", () => {
    expect(
      buildGitHubInstallationManageUrl({
        account_login: "example org",
        account_type: "Organization",
        installation_id: "67890",
      }),
    ).toBe(
      "https://github.com/organizations/example%20org/settings/installations/67890",
    );
  });
});
