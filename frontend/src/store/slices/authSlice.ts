import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { User } from "@/types/auth";

type AuthState = {
  user: User | null;
  isAuthenticated: boolean;
};

const initialState: AuthState = {
  user: null,
  isAuthenticated: false,
};

type CredentialsPayload = {
  user: User;
};

export const authSlice = createSlice({
  name: "auth",
  initialState,
  reducers: {
    setCredentials: (state, action: PayloadAction<CredentialsPayload>) => {
      state.user = action.payload.user;
      state.isAuthenticated = true;
    },
    clearCredentials: (state) => {
      state.user = null;
      state.isAuthenticated = false;
    },
  },
});

export const { clearCredentials, setCredentials } = authSlice.actions;

export default authSlice.reducer;
