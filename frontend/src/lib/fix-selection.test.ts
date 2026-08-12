import { describe, expect, it } from "vitest";

import {
  buildFixSelectionItem,
  clearFixSelections,
  getFixIssueIds,
  loadFixSelections,
  mergeFixSelections,
  removeFixSelections,
  saveFixSelections,
  type FixSelectionItem,
} from "@/lib/fix-selection";
import type { ReviewIssue } from "@/types/issue";

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

const FIRST_SELECTION: FixSelectionItem = {
  fixIssueIds: ["raw-1", "raw-2"],
  groupId: "group-1",
};

const SECOND_SELECTION: FixSelectionItem = {
  fixIssueIds: ["raw-2", "raw-3"],
  groupId: "group-2",
};

describe("fix selection basket", () => {
  it("keeps selections from multiple result pages and deduplicates fix IDs", () => {
    const selections = mergeFixSelections(
      [FIRST_SELECTION],
      [SECOND_SELECTION],
    );

    expect(selections).toEqual([FIRST_SELECTION, SECOND_SELECTION]);
    expect(getFixIssueIds(selections)).toEqual(["raw-1", "raw-2", "raw-3"]);
  });

  it("updates an existing group without removing selections from other pages", () => {
    const updatedSelection = {
      ...FIRST_SELECTION,
      fixIssueIds: ["raw-4"],
    };

    expect(
      mergeFixSelections(
        [FIRST_SELECTION, SECOND_SELECTION],
        [updatedSelection],
      ),
    ).toEqual([updatedSelection, SECOND_SELECTION]);
  });

  it("removes only the requested groups", () => {
    expect(
      removeFixSelections(
        [FIRST_SELECTION, SECOND_SELECTION],
        [FIRST_SELECTION.groupId],
      ),
    ).toEqual([SECOND_SELECTION]);
  });

  it("persists a basket per review job and clears it after use", () => {
    const storage = new MemoryStorage();

    saveFixSelections("job-1", [FIRST_SELECTION], storage);
    saveFixSelections("job-2", [SECOND_SELECTION], storage);

    expect(loadFixSelections("job-1", storage)).toEqual([FIRST_SELECTION]);
    expect(loadFixSelections("job-2", storage)).toEqual([SECOND_SELECTION]);

    clearFixSelections("job-1", storage);
    expect(loadFixSelections("job-1", storage)).toEqual([]);
    expect(loadFixSelections("job-2", storage)).toEqual([SECOND_SELECTION]);
  });

  it("ignores invalid stored data", () => {
    const storage = new MemoryStorage();
    storage.setItem("review-fix-selection:job-1", "not-json");

    expect(loadFixSelections("job-1", storage)).toEqual([]);
  });

  it("builds compact selection metadata with a fallback fix ID", () => {
    const issue: ReviewIssue = {
      affected_files: ["src/main.py"],
      category: "bug",
      confidence: null,
      created_at: "2026-08-11T00:00:00Z",
      description: "Description",
      fix_issue_ids: [],
      group_key: null,
      id: "issue-1",
      job_id: "job-1",
      file_path: "src/main.py",
      line_end: 1,
      line_start: 1,
      occurrence_count: 1,
      occurrences: [],
      primary_issue_id: null,
      raw_issue_count: 1,
      raw_output: null,
      severity: "medium",
      source: "ai_review",
      suggestion: null,
      title: "Raw title",
    };

    expect(buildFixSelectionItem(issue)).toEqual({
      fixIssueIds: ["issue-1"],
      groupId: "issue-1",
    });
  });
});
