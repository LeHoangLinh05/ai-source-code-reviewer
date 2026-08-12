"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";

const MAX_VISIBLE_PAGE_BUTTONS = 7;
const ELLIPSIS = "ellipsis";

type PaginationControlsProps = {
  currentPage: number;
  pageSize: number;
  totalItems: number;
  onPageChange: (page: number) => void;
};

export function PaginationControls({
  currentPage,
  pageSize,
  totalItems,
  onPageChange,
}: PaginationControlsProps) {
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  const safePage = Math.min(Math.max(currentPage, 1), totalPages);
  const firstItem = totalItems === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const lastItem = Math.min(safePage * pageSize, totalItems);

  return (
    <div className="flex flex-col gap-3 border-t border-border px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
      <p className="text-sm text-muted-foreground">
        Showing {firstItem}-{lastItem} of {totalItems}
      </p>
      <nav aria-label="Pagination" className="flex items-center gap-2">
        <Button
          aria-label="Previous page"
          disabled={safePage === 1}
          onClick={() => onPageChange(safePage - 1)}
          size="icon"
          variant="secondary"
        >
          <ChevronLeft aria-hidden="true" />
        </Button>
        <div className="flex items-center gap-1">
          {getPageItems(safePage, totalPages).map((item, index) =>
            item === ELLIPSIS ? (
              <span
                aria-hidden="true"
                className="flex size-9 items-center justify-center text-sm text-muted-foreground"
                key={`${item}-${index}`}
              >
                …
              </span>
            ) : (
              <Button
                aria-current={item === safePage ? "page" : undefined}
                aria-label={`Go to page ${item}`}
                className="min-w-10 px-2"
                key={item}
                onClick={() => onPageChange(item)}
                size="sm"
                variant={item === safePage ? "default" : "secondary"}
              >
                {item}
              </Button>
            ),
          )}
        </div>
        <Button
          aria-label="Next page"
          disabled={safePage === totalPages}
          onClick={() => onPageChange(safePage + 1)}
          size="icon"
          variant="secondary"
        >
          <ChevronRight aria-hidden="true" />
        </Button>
      </nav>
    </div>
  );
}

function getPageItems(
  currentPage: number,
  totalPages: number,
): Array<number | typeof ELLIPSIS> {
  if (totalPages <= MAX_VISIBLE_PAGE_BUTTONS) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const visiblePages = new Set([1, totalPages, currentPage]);
  for (const offset of [-1, 1]) {
    const page = currentPage + offset;
    if (page > 1 && page < totalPages) {
      visiblePages.add(page);
    }
  }

  const sortedPages = Array.from(visiblePages).sort((left, right) => left - right);
  const pageItems: Array<number | typeof ELLIPSIS> = [];

  sortedPages.forEach((page, index) => {
    if (index > 0 && page - sortedPages[index - 1] > 1) {
      pageItems.push(ELLIPSIS);
    }
    pageItems.push(page);
  });

  return pageItems;
}
