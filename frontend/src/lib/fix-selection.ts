import type { ReviewIssue } from "@/types/issue";

const FIX_SELECTION_STORAGE_PREFIX = "review-fix-selection";

export type FixSelectionItem = {
  fixIssueIds: string[];
  groupId: string;
};

type FixSelectionStorage = Pick<Storage, "getItem" | "removeItem" | "setItem">;

export function buildFixSelectionItem(issue: ReviewIssue): FixSelectionItem {
  return {
    fixIssueIds:
      issue.fix_issue_ids.length > 0 ? issue.fix_issue_ids : [issue.id],
    groupId: issue.id,
  };
}

export function mergeFixSelections(
  currentItems: FixSelectionItem[],
  newItems: FixSelectionItem[],
): FixSelectionItem[] {
  const selectionsByGroupId = new Map(
    currentItems.map((item) => [item.groupId, item]),
  );

  for (const item of newItems) {
    selectionsByGroupId.set(item.groupId, item);
  }

  return Array.from(selectionsByGroupId.values());
}

export function removeFixSelections(
  currentItems: FixSelectionItem[],
  groupIds: Iterable<string>,
): FixSelectionItem[] {
  const removedGroupIds = new Set(groupIds);
  return currentItems.filter((item) => !removedGroupIds.has(item.groupId));
}

export function getFixIssueIds(items: FixSelectionItem[]): string[] {
  return Array.from(new Set(items.flatMap((item) => item.fixIssueIds)));
}

export function loadFixSelections(
  jobId: string,
  storage: FixSelectionStorage,
): FixSelectionItem[] {
  try {
    return parseFixSelections(storage.getItem(buildStorageKey(jobId)));
  } catch {
    return [];
  }
}

export function saveFixSelections(
  jobId: string,
  items: FixSelectionItem[],
  storage: FixSelectionStorage,
): void {
  try {
    storage.setItem(buildStorageKey(jobId), JSON.stringify(items));
  } catch {
    // Selection still works in memory when browser storage is unavailable.
  }
}

export function clearFixSelections(
  jobId: string,
  storage: FixSelectionStorage,
): void {
  try {
    storage.removeItem(buildStorageKey(jobId));
  } catch {
    // Selection still clears in memory when browser storage is unavailable.
  }
}

function buildStorageKey(jobId: string): string {
  return `${FIX_SELECTION_STORAGE_PREFIX}:${jobId}`;
}

function parseFixSelections(value: string | null): FixSelectionItem[] {
  if (value === null) {
    return [];
  }

  try {
    const parsedValue: unknown = JSON.parse(value);
    if (!Array.isArray(parsedValue)) {
      return [];
    }

    const validItems = parsedValue.filter(isFixSelectionItem);
    return mergeFixSelections([], validItems);
  } catch {
    return [];
  }
}

function isFixSelectionItem(value: unknown): value is FixSelectionItem {
  if (typeof value !== "object" || value === null) {
    return false;
  }

  const item = value as Record<string, unknown>;
  return (
    Array.isArray(item.fixIssueIds) &&
    item.fixIssueIds.length > 0 &&
    item.fixIssueIds.every((issueId) => typeof issueId === "string") &&
    typeof item.groupId === "string"
  );
}
