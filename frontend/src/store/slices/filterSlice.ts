import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type {
  IssueCategory,
  IssueSeverity,
  IssueSort,
  IssueSource,
} from "@/types/issue";

type IssueTableFilters = {
  category: IssueCategory | null;
  filePath: string;
  page: number;
  perPage: number;
  severity: IssueSeverity | null;
  sort: IssueSort;
  source: IssueSource | null;
};

type FilterState = {
  issues: IssueTableFilters;
};

const initialIssueFilters: IssueTableFilters = {
  category: null,
  filePath: "",
  page: 1,
  perPage: 10,
  severity: null,
  sort: "-created_at",
  source: null,
};

const initialState: FilterState = {
  issues: initialIssueFilters,
};

export const filterSlice = createSlice({
  name: "filters",
  initialState,
  reducers: {
    resetIssueFilters: (state) => {
      state.issues = initialIssueFilters;
    },
    setIssueCategory: (
      state,
      action: PayloadAction<IssueCategory | null>,
    ) => {
      state.issues.category = action.payload;
      state.issues.page = 1;
    },
    setIssueFilePath: (state, action: PayloadAction<string>) => {
      state.issues.filePath = action.payload;
      state.issues.page = 1;
    },
    setIssuePage: (state, action: PayloadAction<number>) => {
      state.issues.page = action.payload;
    },
    setIssueSeverity: (
      state,
      action: PayloadAction<IssueSeverity | null>,
    ) => {
      state.issues.severity = action.payload;
      state.issues.page = 1;
    },
    setIssueSort: (state, action: PayloadAction<IssueSort>) => {
      state.issues.sort = action.payload;
      state.issues.page = 1;
    },
    setIssueSource: (state, action: PayloadAction<IssueSource | null>) => {
      state.issues.source = action.payload;
      state.issues.page = 1;
    },
  },
});

export const {
  resetIssueFilters,
  setIssueCategory,
  setIssueFilePath,
  setIssuePage,
  setIssueSeverity,
  setIssueSort,
  setIssueSource,
} = filterSlice.actions;

export default filterSlice.reducer;
