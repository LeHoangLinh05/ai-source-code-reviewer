import { describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { getRepositoryProviderStatus } from "@/lib/providers";

vi.mock("@/lib/api", () => ({
  api: {
    get: vi.fn(),
  },
}));

describe("getRepositoryProviderStatus", () => {
  it("fetches repository provider status", async () => {
    const mockStatus = {
      repository_id: "repo-123",
      provider: "github",
      is_connected: true,
      can_publish: true,
      account_login: "repoguard-bot",
      message: "GitHub bot @repoguard-bot is ready to publish.",
    };

    vi.mocked(api.get).mockResolvedValueOnce({ data: mockStatus });

    const result = await getRepositoryProviderStatus("repo-123");

    expect(api.get).toHaveBeenCalledWith(
      "/repositories/repo-123/provider-status",
    );
    expect(result).toEqual(mockStatus);
  });
});
