import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { JobProgressEvent, ReviewJob } from "@/types/review-job";

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

function jobWithProgressEvent(
  job: ReviewJob,
  event: JobProgressEvent,
): ReviewJob {
  const isTerminal = event.status === "COMPLETED" || event.status === "FAILED";
  return {
    ...job,
    status: event.status,
    started_at:
      event.status === "CLONING" && job.started_at === null
        ? event.timestamp
        : job.started_at,
    completed_at: isTerminal ? event.timestamp : job.completed_at,
    error_message:
      event.status === "FAILED" ? event.message : job.error_message,
  };
}

export const jobSlice = createSlice({
  name: "jobs",
  initialState,
  reducers: {
    applyJobProgress: (state, action: PayloadAction<JobProgressEvent>) => {
      const event = action.payload;
      const jobIndex = state.items.findIndex((job) => job.id === event.job_id);
      if (jobIndex !== -1) {
        state.items[jobIndex] = jobWithProgressEvent(
          state.items[jobIndex],
          event,
        );
      }

      if (state.currentJob?.id === event.job_id) {
        state.currentJob = jobWithProgressEvent(state.currentJob, event);
      }
    },
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
  applyJobProgress,
  removeJob,
  setCurrentJob,
  setJobError,
  setJobLoading,
  setJobMutating,
  setJobs,
  upsertJob,
} = jobSlice.actions;

export default jobSlice.reducer;
