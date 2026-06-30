import { configureStore } from "@reduxjs/toolkit";

import authReducer from "@/store/slices/authSlice";
import filterReducer from "@/store/slices/filterSlice";
import jobReducer from "@/store/slices/jobSlice";

export const store = configureStore({
  reducer: {
    auth: authReducer,
    jobs: jobReducer,
    filters: filterReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
