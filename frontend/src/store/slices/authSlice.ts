import { createSlice, type PayloadAction } from "@reduxjs/toolkit";

import type { User } from "@/types/auth";

type AuthState = {
  accessToken: string | null;
  user: User | null;
  isAuthenticated: boolean;
};

const initialState: AuthState = {
  accessToken: null,
  user: null,
  isAuthenticated: false,
};

type CredentialsPayload = {
  accessToken: string;
  user: User;
};

export const authSlice = createSlice({
  name: "auth",
  initialState,
  reducers: {
    setCredentials: (state, action: PayloadAction<CredentialsPayload>) => {
      state.accessToken = action.payload.accessToken;
      state.user = action.payload.user;
      state.isAuthenticated = true;
    },
    clearCredentials: (state) => {
      state.accessToken = null;
      state.user = null;
      state.isAuthenticated = false;
    },
    setAccessToken: (state, action: PayloadAction<string>) => {
      state.accessToken = action.payload;
      state.isAuthenticated = !!state.user;
    },
  },
});

export const { clearCredentials, setAccessToken, setCredentials } =
  authSlice.actions;

export default authSlice.reducer;
