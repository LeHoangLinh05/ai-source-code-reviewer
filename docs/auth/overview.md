# RepoGuard AI Auth Summary

## Scope

This document summarizes the authentication work completed for tasks P1.4, P1.5, P1.6, and the P3 refresh-token hardening update.

## Implemented Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/auth/register` | Create a user account and issue an access token plus refresh token. |
| `POST` | `/api/auth/login` | Verify email/password and issue an access token plus refresh token. |
| `POST` | `/api/auth/refresh` | Validate a refresh token and issue a new access token plus refresh token. |
| `POST` | `/api/auth/logout` | Blacklist the current access token in Redis until it expires. |
| `GET` | `/api/auth/me` | Return the current authenticated user. |

## User Model

`backend/app/models/user.py` defines the `User` SQLAlchemy model:

- `id`: UUID primary key.
- `email`: unique indexed email address.
- `hashed_password`: bcrypt password hash mapped to the `hashed_pw` database column.
- `full_name`: optional display name.
- `role`: `user` or `admin`.
- `is_active`: disables login/token usage when false.
- `created_at`, `updated_at`: database timestamps.

## JWT Behavior

- Access tokens use claim `type=access`.
- Refresh tokens use claim `type=refresh`.
- Both tokens include `sub` as the user UUID, `jti` as a token id, `iat`, and `exp`.
- Password hashing uses `passlib[bcrypt]`.
- JWT encoding/decoding uses `python-jose`.

## Refresh Token Storage

Refresh tokens are returned in the JSON response and also set as an HttpOnly cookie named `refreshToken`.

Refresh token server-side state is stored in PostgreSQL table `refresh_tokens`:

- The raw refresh token is never stored.
- `token_hash` stores an HMAC-SHA256 hash using the JWT secret.
- `expires_at` mirrors the JWT refresh token expiration.
- `revoked_at` marks explicit revocation.
- `replaced_by_token_id` links the old token to the replacement token during rotation.

Refresh flow:

1. Verify JWT signature, token type, and expiration.
2. Hash the presented refresh token.
3. Find the matching `refresh_tokens` row.
4. Reject if missing, expired, or already revoked.
5. Issue a new access token and refresh token.
6. Store the new refresh token hash.
7. Revoke the old refresh token and set `replaced_by_token_id`.

## Logout And Blacklist

Logout uses both Redis and PostgreSQL:

- Redis blacklists the current access token.
- PostgreSQL revokes the matching refresh token when it is available from the HttpOnly cookie or request body.
- The raw access token is not stored directly.
- A SHA-256 digest of the access token is used in the Redis key.
- TTL equals the remaining lifetime of the access token.
- `get_current_user` rejects blacklisted access tokens before loading the user.

## RBAC

RBAC is implemented in `backend/app/core/dependencies.py`:

- `get_current_user`: decodes the access token, checks Redis blacklist, and loads the active user from PostgreSQL.
- `get_current_admin`: wraps `get_current_user` and requires `role=admin`.

`GET /api/auth/me` uses `get_current_user`.

## Important Files

- `backend/app/core/security.py`
- `backend/app/core/dependencies.py`
- `backend/app/models/user.py`
- `backend/app/models/refresh_token.py`
- `backend/app/repositories/refresh_token_repository.py`
- `backend/app/repositories/user_repository.py`
- `backend/app/services/auth_service.py`
- `backend/app/schemas/auth.py`
- `backend/app/routers/auth.py`
- `backend/app/main.py`
