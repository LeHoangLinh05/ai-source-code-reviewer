export type RepositoryPlatform = "github" | "gitlab" | "other";

export type Repository = {
  id: string;
  user_id: string;
  name: string;
  url: string;
  platform: RepositoryPlatform | null;
  default_branch: string;
  description: string | null;
  last_reviewed_at: string | null;
  created_at: string;
};

export type CreateRepositoryPayload = {
  name: string;
  url: string;
  default_branch: string;
};

export type DeleteRepositoryResponse = {
  message: string;
};
