import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { ReviewJob } from "@/types/review-job";

type JobState = {
  currentJob: ReviewJob | null;
  error: string | null;
  isLoading: boolean;
  isMutating: boolean;
  items: ReviewJob[];
};

const initialState: JobState = {
  currentJob: null,
  error: null,
  isLoading: false,
  isMutating: false,
  items: [],
};

export const jobSlice = createSlice({
  name: "jobs",
  initialState,
  reducers: {
    removeJob: (state, action: PayloadAction<string>) => {
      state.items = state.items.filter((job) => job.id !== action.payload);

      if (state.currentJob?.id === action.payload) {
        state.currentJob = null;
      }
    },
    setCurrentJob: (state, action: PayloadAction<ReviewJob | null>) => {
      state.currentJob = action.payload;
      state.error = null;
    },
    setJobError: (state, action: PayloadAction<string | null>) => {
      state.error = action.payload;
    },
    setJobLoading: (state, action: PayloadAction<boolean>) => {
      state.isLoading = action.payload;
    },
    setJobMutating: (state, action: PayloadAction<boolean>) => {
      state.isMutating = action.payload;
    },
    setJobs: (state, action: PayloadAction<ReviewJob[]>) => {
      state.items = action.payload;
      state.error = null;
    },
    upsertJob: (state, action: PayloadAction<ReviewJob>) => {
      const jobIndex = state.items.findIndex((job) => job.id === action.payload.id);

      if (jobIndex === -1) {
        state.items.unshift(action.payload);
      } else {
        state.items[jobIndex] = action.payload;
      }

      if (state.currentJob?.id === action.payload.id) {
        state.currentJob = action.payload;
      }
    },
  },
});

export const {
  removeJob,
  setCurrentJob,
  setJobError,
  setJobLoading,
  setJobMutating,
  setJobs,
  upsertJob,
} = jobSlice.actions;

export default jobSlice.reducer;
