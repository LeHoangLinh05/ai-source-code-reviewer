import { api } from "@/lib/api";
import type {
  ChangePasswordPayload,
  ChangePasswordResponse,
  UpdateProfilePayload,
  User,
} from "@/types/auth";

export async function getCurrentUserProfile() {
  const response = await api.get<User>("/users/me");
  return response.data;
}

export async function updateCurrentUserProfile(payload: UpdateProfilePayload) {
  const response = await api.patch<User>("/users/me", payload);
  return response.data;
}

export async function changeCurrentUserPassword(
  payload: ChangePasswordPayload,
) {
  const response = await api.post<ChangePasswordResponse>(
    "/users/me/password",
    payload,
  );
  return response.data;
}
