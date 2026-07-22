export type UserRole = "user" | "admin";

export type User = {
  id: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type AuthResponse = {
  user: User;
};

export type LoginPayload = {
  email: string;
  password: string;
};

export type RegisterPayload = LoginPayload;

export type UpdateProfilePayload = {
  full_name: string | null;
};

export type ChangePasswordPayload = {
  current_password: string;
  new_password: string;
};

export type ChangePasswordResponse = {
  message: string;
};
