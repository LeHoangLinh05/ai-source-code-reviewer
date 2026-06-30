import { createSlice } from "@reduxjs/toolkit";

type FilterState = Record<string, never>;

const initialState: FilterState = {};

export const filterSlice = createSlice({
  name: "filters",
  initialState,
  reducers: {},
});

export default filterSlice.reducer;
