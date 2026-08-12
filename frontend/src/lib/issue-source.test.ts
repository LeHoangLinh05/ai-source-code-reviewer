import { describe, expect, it } from "vitest";

import { formatIssueSource, ISSUE_SOURCE_FILTERS } from "@/lib/issue-source";

describe("issue source presentation", () => {
  it("presents roadmap and general AI findings with one label", () => {
    expect(formatIssueSource("KB")).toBe("AI review");
    expect(formatIssueSource("ai_review")).toBe("AI review");
  });

  it("offers one combined AI review source filter", () => {
    expect(ISSUE_SOURCE_FILTERS).toContain("ai_review");
    expect(ISSUE_SOURCE_FILTERS).not.toContain("KB");
  });
});
