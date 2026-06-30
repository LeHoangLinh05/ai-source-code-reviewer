import { createSlice } from "@reduxjs/toolkit";

type JobState = Record<string, never>;

const initialState: JobState = {};

export const jobSlice = createSlice({
  name: "jobs",
  initialState,
  reducers: {},
});

export default jobSlice.reducer;
