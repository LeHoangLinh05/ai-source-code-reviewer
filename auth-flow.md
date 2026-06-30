# JWT Authentication Flow (Next.js + Node/NestJS)

## 1. Login

```text
Client
  |
  | POST /auth/login
  |
Backend
  |
  | Verify username/password
  |
  | Generate:
  | - Access Token (15m)
  | - Refresh Token (7d hoặc 30d)
  |
  +--> Return Access Token
  +--> Set Refresh Token vào HttpOnly Cookie
```

Ví dụ response:

```json
{
  "accessToken": "eyJhbGciOi..."
}
```

Header:

```http
Set-Cookie:
refreshToken=abcxyz;
HttpOnly;
Secure;
SameSite=Lax;
```

---

## 2. Lưu Token ở Frontend

### Access Token

Thường lưu trong:

```text
Redux
Zustand
React Context
Memory
```

Hoặc:

```text
localStorage
```

Tuy nhiên nhiều team hiện nay ưu tiên lưu trong **memory** để giảm rủi ro XSS.

### Refresh Token

Không nên lưu:

```text
localStorage ❌
sessionStorage ❌
```

Nên lưu:

```text
HttpOnly Cookie ✅
```

Lý do: JavaScript không thể đọc được token này.

---

## 3. Gọi API Bình Thường

Axios Request Interceptor:

```ts
axios.interceptors.request.use((config) => {
  const accessToken = store.getState().auth.accessToken;

  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }

  return config;
});
```

Flow:

```text
Client
   |
   | Authorization: Bearer ACCESS_TOKEN
   |
Backend
```

---

## 4. Backend Verify Access Token

Middleware / Guard / Filter:

```text
Authorization Header
        |
        v
Bearer xxx.yyy.zzz
        |
        v
jwt.verify()
```

Kiểm tra:

### 1. Chữ ký hợp lệ

```text
secret đúng?
```

### 2. Token có bị chỉnh sửa không

```text
tampered?
```

### 3. Token hết hạn chưa

```text
exp
```

### 4. User còn tồn tại không

```text
userId
```

Nếu hợp lệ:

```text
next()
```

---

## 5. Access Token Hết Hạn

Backend nên trả:

```http
401 Unauthorized
```

Không nên:

```http
400 Bad Request
```

Ví dụ:

```json
{
  "message": "Access token expired"
}
```

---

## 6. Axios Response Interceptor

```ts
axios.interceptors.response.use(
  (res) => res,
  async (error) => {
    if (error.response.status === 401) {
      // refresh token
    }
  }
);
```

Flow:

```text
API
 |
 +--> 401
 |
Axios Response Interceptor
 |
 +--> gọi refresh API
```

---

## 7. Refresh Token Flow

Frontend gọi:

```http
POST /auth/refresh
```

Không cần gửi Refresh Token thủ công nếu dùng Cookie.

Browser tự động gửi:

```http
Cookie:
refreshToken=abcxyz
```

---

## 8. Backend Verify Refresh Token

```text
refreshToken
      |
      v
jwt.verify()
      |
      +--> hợp lệ?
      +--> hết hạn?
      +--> user tồn tại?
```

Nếu hợp lệ:

```text
Generate New Access Token
Generate New Refresh Token
```

---

## 9. Rotate Refresh Token (Khuyến Nghị)

Thay vì dùng mãi một Refresh Token:

```text
RT1
 |
refresh
 |
RT2
```

Backend:

```text
RT1 -> revoke
RT2 -> create
```

Giảm rủi ro khi Refresh Token bị lộ.

---

## 10. Trả Token Mới

Response:

```json
{
  "accessToken": "new-access-token"
}
```

Cookie mới:

```http
Set-Cookie:
refreshToken=new-refresh-token
```

---

## 11. Retry Request Cũ

Interceptor:

```ts
const newToken = refreshResponse.accessToken;

store.dispatch(setAccessToken(newToken));

originalRequest.headers.Authorization = `Bearer ${newToken}`;

return axios(originalRequest);
```

Flow:

```text
401
 |
refresh
 |
new AT
 |
retry request cũ
 |
success
```

Người dùng gần như không nhận ra Access Token đã được refresh.

---

# Flow Tổng Thể

```text
LOGIN
 |
 +--> Access Token
 |
 +--> Refresh Token Cookie
 |
 v
Call API
 |
Authorization: Bearer AT
 |
Backend Verify
 |
 +--> Valid
 |      |
 |      +--> Data
 |
 +--> Expired
        |
        +--> 401
                |
                v
      Axios Response Interceptor
                |
                +--> POST /auth/refresh
                |
                v
      Cookie gửi RT
                |
      Backend Verify RT
                |
        +-------+------+
        |              |
      Invalid       Valid
        |              |
      Logout      New AT + New RT
                       |
                 Retry API
```

---

# Redis Xuất Hiện Ở Đâu?

Redis thường được dùng để quản lý session hoặc Refresh Token.

Ví dụ:

```text
refresh:{userId}
```

```json
{
  "token": "RT_xyz",
  "expiresAt": "..."
}
```

Khi refresh:

```text
1. Verify JWT
2. Check Redis
3. Token trong Redis có khớp không
```

Nếu hacker lấy được RT cũ:

```text
JWT valid
nhưng
Redis không khớp
```

→ Từ chối refresh.

Flow:

```text
Refresh Token
      |
      v
Verify JWT
      |
      v
Check Redis
      |
      +--> Match -> cấp token mới
      |
      +--> Not Match -> logout
```

---

# Kiến Trúc Production Thường Gặp

```text
Next.js
   |
Axios Interceptor
   |
NestJS / Node.js / Spring Boot
   |
JWT Access Token
   |
Refresh Token Cookie
   |
Redis Session Store
```

---

# Recommendation

## Access Token

```text
- Expire: 15 phút
- Lưu trong Redux/Zustand (Memory)
```

## Refresh Token

```text
- HttpOnly Cookie
- Expire: 7 ~ 30 ngày
```

## Frontend

```text
- Axios Request Interceptor
  -> Gắn Authorization Bearer Token

- Axios Response Interceptor
  -> Tự động refresh token
```

## Backend

```text
- JWT Guard / Middleware
- Refresh Endpoint
- Rotate Refresh Token
```

## Redis

```text
- Lưu refresh session
- Validate refresh token
- Revoke token cũ
```

---

# Tóm Tắt

```text
Login
  ↓
AT + RT
  ↓
Call API bằng AT
  ↓
AT hết hạn
  ↓
401 Unauthorized
  ↓
Interceptor bắt lỗi
  ↓
/auth/refresh
  ↓
RT hợp lệ
  ↓
New AT + New RT
  ↓
Retry Request
  ↓
Success
```
