import { configureStore } from "@reduxjs/toolkit";

import authReducer from "@/store/slices/authSlice";
import filterReducer from "@/store/slices/filterSlice";
import jobReducer from "@/store/slices/jobSlice";
import repositoryReducer from "@/store/slices/repositorySlice";

export const store = configureStore({
  reducer: {
    auth: authReducer,
    filters: filterReducer,
    jobs: jobReducer,
    repositories: repositoryReducer,
  },
});

export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
