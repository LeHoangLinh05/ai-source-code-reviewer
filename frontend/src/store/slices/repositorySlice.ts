import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { Repository } from "@/types/repository";

type RepositoryState = {
  error: string | null;
  isLoading: boolean;
  isMutating: boolean;
  items: Repository[];
  selectedRepository: Repository | null;
};

const initialState: RepositoryState = {
  error: null,
  isLoading: false,
  isMutating: false,
  items: [],
  selectedRepository: null,
};

export const repositorySlice = createSlice({
  name: "repositories",
  initialState,
  reducers: {
    setRepositories: (state, action: PayloadAction<Repository[]>) => {
      state.items = action.payload;
      state.error = null;
    },
    upsertRepository: (state, action: PayloadAction<Repository>) => {
      const repositoryIndex = state.items.findIndex(
        (repository) => repository.id === action.payload.id,
      );

      if (repositoryIndex === -1) {
        state.items.unshift(action.payload);
        return;
      }

      state.items[repositoryIndex] = action.payload;
    },
    removeRepository: (state, action: PayloadAction<string>) => {
      state.items = state.items.filter(
        (repository) => repository.id !== action.payload,
      );

      if (state.selectedRepository?.id === action.payload) {
        state.selectedRepository = null;
      }
    },
    setSelectedRepository: (
      state,
      action: PayloadAction<Repository | null>,
    ) => {
      state.selectedRepository = action.payload;
      state.error = null;
    },
    setRepositoryLoading: (state, action: PayloadAction<boolean>) => {
      state.isLoading = action.payload;
    },
    setRepositoryMutating: (state, action: PayloadAction<boolean>) => {
      state.isMutating = action.payload;
    },
    setRepositoryError: (state, action: PayloadAction<string | null>) => {
      state.error = action.payload;
    },
  },
});

export const {
  removeRepository,
  setRepositories,
  setRepositoryError,
  setRepositoryLoading,
  setRepositoryMutating,
  setSelectedRepository,
  upsertRepository,
} = repositorySlice.actions;

export default repositorySlice.reducer;
