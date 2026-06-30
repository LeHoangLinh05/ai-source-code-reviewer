# Phân tích module Authentication

Tài liệu này phân tích riêng module Authentication của RepoGuard AI dựa trên source code hiện tại.

Mục tiêu:

- Trả lời rõ hệ thống auth đang có gì và chưa có gì.
- Giải thích luồng đăng ký, đăng nhập, refresh token, logout theo cách dễ hiểu.
- Làm rõ Access Token và Refresh Token được tạo, lưu, kiểm tra, revoke ra sao.
- Có câu trả lời ngắn gọn để mang đi bảo vệ trước hội đồng.
- Chỉ ra rủi ro bảo mật hiện tại và đề xuất nâng cấp để auth chuyên nghiệp hơn.

> Cách hiểu đơn giản:
>
> - **Access Token** giống như thẻ ra vào lớp học, dùng trong thời gian ngắn để gọi API.
> - **Refresh Token** giống như giấy xin cấp lại thẻ mới, sống lâu hơn Access Token.
> - **Database** giống như quyển sổ của nhà trường, ghi lại refresh token đã cấp, đã hết hạn hay đã bị huỷ.
> - **Redis blacklist** giống như bảng danh sách thẻ đã bị khoá tạm thời.

## 1. Kết luận nhanh

| Câu hỏi | Kết luận |
| --- | --- |
| Hệ thống có đăng ký không? | Có. Backend có `POST /api/auth/register`, frontend có form register. |
| Hệ thống có đăng nhập không? | Có. Backend có `POST /api/auth/login`, frontend có form login. |
| Có Access Token không? | Có. Access token là JWT, hạn mặc định 15 phút. |
| Có Refresh Token không? | Có. Refresh token là JWT, hạn mặc định 7 ngày. |
| Refresh Token có lưu database không? | Có. Lưu trong bảng `refresh_tokens`, nhưng chỉ lưu bản băm HMAC-SHA256, không lưu token gốc. |
| Access Token có lưu database không? | Không. Access token không lưu DB. Khi logout chỉ lưu `jti` của access token vào Redis blacklist. |
| Có refresh token cookie không? | Có. Backend set cookie tên `refreshToken`, `HttpOnly`, `Secure`, `SameSite=Lax`, path `/api/auth`. |
| Có auto refresh token không? | Có. Frontend có Axios interceptor: nếu API trả `401` thì gọi `/auth/refresh`, lấy access token mới, rồi gọi lại request cũ. |
| Có token rotation không? | Có. Mỗi lần refresh, hệ thống tạo refresh token mới, revoke refresh token cũ, và nối token cũ đến token mới bằng `replaced_by_token_id`. |
| Có revoke refresh token không? | Có. Logout revoke refresh token hiện tại nếu nhận được refresh token từ cookie hoặc request body. |
| Có blacklist access token không? | Có. Redis lưu blacklist theo `jti` của access token. |
| Có blacklist refresh token không? | Không theo nghĩa Redis blacklist. Refresh token được revoke bằng cột `revoked_at` trong database. |
| Có hỗ trợ đăng nhập nhiều thiết bị không? | Có về mặt kỹ thuật. Mỗi lần login tạo một refresh token record riêng. Nhưng chưa có quản lý thiết bị/session rõ ràng. |
| Có logout một thiết bị không? | Có một phần. Logout revoke refresh token hiện tại, nhưng đồng thời vô hiệu hoá các access token cũ của user trong Redis. |
| Có logout tất cả thiết bị không? | Chưa có endpoint đúng nghĩa. Chưa có API revoke tất cả refresh token của user. |
| Có role user/admin không? | Có enum `user`/`admin` và dependency `get_current_admin`. Nhưng admin router hiện đang rỗng và app hiện mới include auth router. |
| Có forgot password/change password/email verify/MFA không? | Chưa có trong source code hiện tại. |
| Rủi ro lớn nhất hiện tại? | Cookie `Secure=True` bị hardcode, register trả refresh token trong JSON, chưa rate limit login/register, chưa CSRF token, logout multi-device còn mơ hồ, JWT secret có default rỗng nếu quên cấu hình. |

## 2. Các file code liên quan

Backend:

- `backend/app/routers/auth.py`: định nghĩa API register, login, refresh, logout, me.
- `backend/app/services/auth_service.py`: xử lý nghiệp vụ auth.
- `backend/app/core/security.py`: hash password, tạo JWT, decode JWT, hash token để lưu DB.
- `backend/app/core/dependencies.py`: lấy current user, current admin, bearer token.
- `backend/app/services/token_blacklist.py`: Redis blacklist cho access token.
- `backend/app/models/user.py`: model user, role, is_active.
- `backend/app/models/refresh_token.py`: model refresh token.
- `backend/app/repositories/user_repository.py`: truy vấn user.
- `backend/app/repositories/refresh_token_repository.py`: truy vấn và revoke refresh token.
- `backend/alembic/versions/20260630_0001_initial_postgresql_schema.py`: migration tạo bảng `users` và `refresh_tokens`.

Frontend:

- `frontend/src/components/auth/register-form.tsx`: form đăng ký.
- `frontend/src/components/auth/login-form.tsx`: form đăng nhập.
- `frontend/src/components/auth/logout-button.tsx`: nút logout.
- `frontend/src/lib/api.ts`: Axios instance, gắn Authorization header, auto refresh khi gặp `401`.
- `frontend/src/store/slices/authSlice.ts`: lưu access token và user trong Redux memory.
- `frontend/src/components/providers/redux-provider.tsx`: khi load app thì thử refresh session.
- `frontend/src/middleware.ts`: chặn `/dashboard` dựa vào cookie marker `repoguard_session`.
- `frontend/src/lib/session-marker.ts`: tạo/xoá cookie marker cho frontend middleware.

## 3. Hệ thống đang có những chức năng auth nào?

Backend đang có các endpoint:

| Method | Endpoint | Chức năng |
| --- | --- | --- |
| `POST` | `/api/auth/register` | Tạo user mới, tạo access token và refresh token. |
| `POST` | `/api/auth/login` | Kiểm tra email/password, tạo access token và refresh token. |
| `POST` | `/api/auth/refresh` | Dùng refresh token để cấp access token mới và refresh token mới. |
| `POST` | `/api/auth/logout` | Logout session hiện tại: blacklist access token, revoke refresh token nếu có. |
| `GET` | `/api/auth/me` | Lấy thông tin user hiện tại từ access token. |

Frontend hiện có:

- Màn hình đăng ký.
- Màn hình đăng nhập.
- Nút logout.
- Lưu access token trong Redux memory.
- Tự động gắn `Authorization: Bearer <access_token>` vào request.
- Tự động refresh token khi API trả `401`.
- Middleware chặn route `/dashboard` dựa vào cookie marker `repoguard_session`.

Lưu ý:

- Cookie marker `repoguard_session` chỉ là dấu hiệu cho frontend biết "có vẻ user đã login".
- Cookie marker không phải token bảo mật.
- Backend vẫn là nơi quyết định user có được gọi API hay không.

## 4. Database auth đang có gì?

### 4.1. Bảng `users`

| Cột | Ý nghĩa |
| --- | --- |
| `id` | UUID của user. |
| `email` | Email, unique, có index. |
| `hashed_pw` | Mật khẩu đã hash bằng bcrypt. Không lưu mật khẩu gốc. |
| `full_name` | Tên đầy đủ, hiện tại có thể null. |
| `role` | Role của user: `user` hoặc `admin`. Mặc định là `user`. |
| `is_active` | User có đang active không. Nếu false thì login/token sẽ bị từ chối. |
| `created_at` | Thời điểm tạo user. |
| `updated_at` | Thời điểm cập nhật user. |

### 4.2. Bảng `refresh_tokens`

| Cột | Ý nghĩa |
| --- | --- |
| `id` | UUID của bản ghi refresh token. |
| `user_id` | User sở hữu refresh token. |
| `token_hash` | Bản băm của refresh token. Unique, có index. |
| `expires_at` | Thời điểm refresh token hết hạn. |
| `revoked_at` | Nếu khác null, refresh token đã bị revoke. |
| `replaced_by_token_id` | Nếu token cũ đã được rotate, cột này trỏ đến refresh token mới. |
| `created_at` | Thời điểm tạo bản ghi. |

Kết luận:

- Refresh token có lưu database.
- Hệ thống không lưu refresh token gốc.
- Hệ thống chỉ lưu `token_hash`.
- Nếu database bị lộ, kẻ tấn công không đọc được refresh token gốc trực tiếp từ bảng này.

## 5. Luồng đăng ký hoạt động như thế nào?

Endpoint:

```text
POST /api/auth/register
```

Request body:

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response backend khai báo:

```json
{
  "access_token": "jwt-access-token",
  "refresh_token": "jwt-refresh-token",
  "token_type": "Bearer",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "role": "user",
    "is_active": true,
    "created_at": "...",
    "updated_at": "..."
  }
}
```

Backend đồng thời set cookie:

```text
Set-Cookie: refreshToken=<refresh-token>; HttpOnly; Secure; SameSite=Lax; Path=/api/auth
```

Các bước đăng ký:

1. Frontend gửi email và password lên `/api/auth/register`.
2. `RegisterRequest` xử lý email:
   - `strip()` để bỏ khoảng trắng đầu/cuối.
   - `lower()` để đưa về chữ thường.
   - kiểm tra email có ký tự `@`.
3. Backend tìm user theo email trong database.
4. Nếu email đã tồn tại, trả lỗi `409 Conflict`: `Email is already registered`.
5. Nếu chưa tồn tại, backend hash password bằng bcrypt.
6. Tạo user mới trong bảng `users`.
7. Role mặc định là `user`.
8. Tạo access token mới.
9. Tạo refresh token mới.
10. Decode refresh token để lấy `expires_at`.
11. Hash refresh token bằng HMAC-SHA256 với JWT secret.
12. Lưu bản hash vào bảng `refresh_tokens`.
13. Trả token pair về client và set cookie `refreshToken`.

Frontend register hiện tại:

1. Validate email đúng định dạng.
2. Validate password ít nhất 8 ký tự.
3. Validate confirm password phải giống password.
4. Gọi `/auth/register`.
5. Nếu thành công, hiện toast `Account created. You can sign in now.`
6. Chuyển user về `/login`.

Điểm cần nhớ:

- Backend register đang tạo token và set refresh cookie, tức là backend có xu hướng "đăng ký xong là có token".
- Frontend lại không lưu access token sau register, mà bắt user đăng nhập lại.
- Đây là điểm chưa đồng nhất giữa backend và frontend.

## 6. Luồng đăng nhập hoạt động như thế nào?

Endpoint:

```text
POST /api/auth/login
```

Request body:

```json
{
  "email": "user@example.com",
  "password": "password123"
}
```

Response frontend nhận:

```json
{
  "access_token": "jwt-access-token",
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "role": "user",
    "is_active": true,
    "created_at": "...",
    "updated_at": "..."
  }
}
```

Backend đồng thời set cookie refresh token:

```text
Set-Cookie: refreshToken=<refresh-token>; HttpOnly; Secure; SameSite=Lax; Path=/api/auth
```

Các bước đăng nhập:

1. User nhập email/password trên frontend.
2. Frontend validate email và password không rỗng.
3. Frontend gọi `POST /auth/login`.
4. Backend normalize email bằng `strip().lower()`.
5. Backend tìm user theo email.
6. Nếu không tìm thấy user, trả lỗi `401 Invalid email or password`.
7. Nếu có user, backend dùng bcrypt để so password người dùng nhập với `hashed_pw` trong database.
8. Nếu password sai, trả lỗi `401 Invalid email or password`.
9. Nếu user bị `is_active = false`, trả lỗi user inactive.
10. Nếu đúng, backend tạo access token và refresh token mới.
11. Refresh token được hash rồi lưu vào bảng `refresh_tokens`.
12. Backend trả access token + user trong JSON.
13. Backend set refresh token vào cookie `refreshToken`.
14. Frontend lưu access token và user vào Redux memory.
15. Frontend tạo cookie marker `repoguard_session`.
16. Frontend chuyển user vào `/dashboard`.

## 7. Access Token được tạo, lưu, kiểm tra ra sao?

Access token là JWT dùng để gọi API cần đăng nhập.

Mặc định:

- Thời gian sống: `15` phút.
- Cấu hình: `jwt_access_token_expire_minutes = 15`.
- Algorithm: `HS256`.
- Secret: `jwt_secret_key` từ biến môi trường.

Payload của access token gồm:

| Claim | Ý nghĩa |
| --- | --- |
| `sub` | User ID. |
| `type` | Loại token, với access token là `access`. |
| `jti` | ID riêng của token, mỗi token có một UUID riêng. |
| `iat` | Thời điểm token được tạo. |
| `exp` | Thời điểm token hết hạn. |

Access token không được lưu trong database.

Frontend lưu access token trong Redux memory:

- Không thấy lưu vào `localStorage`.
- Không thấy lưu vào `sessionStorage`.
- Khi reload trang, Redux memory mất token.
- Lúc đó frontend gọi `/auth/refresh` để xin access token mới dựa vào refresh token cookie.

Frontend gửi access token lên backend bằng header:

```text
Authorization: Bearer <access_token>
```

Backend kiểm tra access token qua `get_current_user`:

1. Lấy bearer token từ header `Authorization`.
2. Decode JWT bằng secret và algorithm cấu hình.
3. Kiểm tra token chưa hết hạn.
4. Kiểm tra claim `type` phải là `access`.
5. Kiểm tra `sub`, `iat`, `exp`, `jti` hợp lệ.
6. Kiểm tra `jti` có nằm trong Redis blacklist không.
7. Kiểm tra token có bị vô hiệu hoá bởi mốc `user_invalid_after` trong Redis không.
8. Lấy user từ database theo `sub`.
9. Kiểm tra user tồn tại và `is_active = true`.
10. Nếu tất cả hợp lệ, cho request đi tiếp.

Khi logout, access token bị revoke/blacklist như sau:

1. Backend decode access token.
2. Lấy `jti`.
3. Tính thời gian còn lại của token.
4. Lưu key Redis:

```text
auth:blacklist:jti:<jti>
```

Giá trị lưu là `"1"`.

TTL của key bằng thời gian còn lại của access token.

Ví dụ:

- Access token còn 10 phút mới hết hạn.
- User logout lúc này.
- Redis blacklist giữ `jti` trong 10 phút.
- Sau 10 phút, token tự hết hạn nên Redis cũng không cần giữ nữa.

Ngoài ra logout còn set key:

```text
auth:blacklist:user_invalid_after:<user_id>
```

Key này lưu timestamp. Mọi access token của user có `iat <= timestamp` sẽ bị coi là đã revoke.

Đây là chi tiết quan trọng:

- Hệ thống không chỉ blacklist một access token theo `jti`.
- Hệ thống còn vô hiệu hoá các access token cũ của user tại thời điểm logout.

## 8. Refresh Token được tạo, lưu, kiểm tra ra sao?

Refresh token là JWT dùng để xin cấp access token mới.

Mặc định:

- Thời gian sống: `7` ngày.
- Cấu hình: `jwt_refresh_token_expire_days = 7`.
- Algorithm: `HS256`.
- Secret: cùng dùng `jwt_secret_key`.

Payload refresh token gồm:

| Claim | Ý nghĩa |
| --- | --- |
| `sub` | User ID. |
| `type` | Loại token, với refresh token là `refresh`. |
| `jti` | ID riêng của token. |
| `iat` | Thời điểm tạo token. |
| `exp` | Thời điểm hết hạn. |

Refresh token được trả về client như sau:

1. Register:
   - Backend trả refresh token trong JSON response.
   - Backend cũng set cookie `refreshToken`.
2. Login:
   - Backend không trả refresh token trong JSON response.
   - Backend set refresh token vào cookie `refreshToken`.
3. Refresh:
   - Backend không trả refresh token trong JSON response.
   - Backend set refresh token mới vào cookie `refreshToken`.

Cookie refresh token có các thuộc tính:

```text
HttpOnly
Secure
SameSite=Lax
Path=/api/auth
```

Ý nghĩa:

- `HttpOnly`: JavaScript không đọc được cookie này. Tốt cho bảo mật.
- `Secure`: trình duyệt chỉ gửi cookie qua HTTPS.
- `SameSite=Lax`: giảm rủi ro CSRF ở một mức độ nhất định.
- `Path=/api/auth`: cookie chỉ được gửi cho các API dưới `/api/auth`.

Refresh token có lưu trong database, nhưng không lưu token gốc.

Quy trình lưu:

1. Tạo refresh token JWT.
2. Lấy chuỗi refresh token gốc.
3. Hash bằng HMAC-SHA256:

```text
HMAC-SHA256(secret = jwt_secret_key, message = refresh_token)
```

4. Lưu kết quả hash vào cột `token_hash`.

Khi refresh token được gửi lên:

1. Backend decode JWT.
2. Kiểm tra token chưa hết hạn.
3. Kiểm tra claim `type` phải là `refresh`.
4. Lấy user id trong `sub`.
5. Hash refresh token vừa nhận.
6. Tìm bản ghi trong bảng `refresh_tokens` theo `token_hash`.
7. Nếu không thấy, coi như token đã bị revoke.
8. Kiểm tra `user_id` trong DB có khớp với `sub` trong token không.
9. Kiểm tra `revoked_at` phải là null.
10. Kiểm tra `expires_at` trong DB chưa quá hạn.
11. Lấy user từ database.
12. Kiểm tra user tồn tại và active.
13. Tạo access token mới.
14. Tạo refresh token mới.
15. Lưu hash của refresh token mới vào DB.
16. Revoke refresh token cũ bằng cách set `revoked_at`.
17. Ghi `replaced_by_token_id` của token cũ trỏ đến token mới.
18. Commit transaction.
19. Set cookie `refreshToken` mới.
20. Trả access token mới và user về frontend.

## 9. Token rotation có không?

Có.

Token rotation nghĩa là: mỗi lần dùng refresh token để xin token mới, refresh token cũ sẽ bị huỷ và thay bằng refresh token mới.

Trong code hiện tại:

1. User có refresh token cũ, gọi `/auth/refresh`.
2. Backend xác thực refresh token cũ.
3. Backend tạo refresh token mới.
4. Backend lưu refresh token mới vào bảng `refresh_tokens`.
5. Backend set `revoked_at` cho refresh token cũ.
6. Backend set `replaced_by_token_id` của token cũ bằng ID của token mới.

Ví dụ:

```text
RT1 đang hợp lệ
User gọi /auth/refresh bằng RT1
Backend tạo RT2
Backend revoke RT1
Backend lưu RT1.replaced_by_token_id = RT2.id
Từ lúc này RT1 không dùng được nữa, RT2 mới dùng được
```

Kết luận:

- Có token rotation.
- Có lưu quan hệ token cũ -> token mới.
- Chưa thấy cơ chế "reuse detection" nâng cao.

Reuse detection nâng cao nghĩa là:

- Nếu RT1 đã bị rotate thành RT2.
- Sau đó ai đó lại dùng RT1.
- Hệ thống nghi RT1 bị đánh cắp.
- Hệ thống revoke toàn bộ token family, bao gồm RT2, RT3...

Hiện tại source code chỉ trả lỗi "Refresh token has been revoked" khi dùng token đã revoke. Chưa thấy logic revoke cả token family.

## 10. Logout hoạt động như thế nào?

Endpoint:

```text
POST /api/auth/logout
```

Điều kiện:

- Bắt buộc có access token trong header `Authorization`.
- Refresh token là tuỳ chọn:
  - lấy từ cookie `refreshToken`, hoặc
  - lấy từ body `refresh_token`.

Các bước:

1. Frontend gọi `/auth/logout`.
2. Axios tự gắn `Authorization: Bearer <access_token>`.
3. Backend dùng `get_current_user` để xác thực access token.
4. Nếu access token hợp lệ, backend vào `AuthService.logout`.
5. Decode access token.
6. Lấy `jti` của access token.
7. Đưa `jti` vào Redis blacklist đến khi access token hết hạn.
8. Set Redis key `auth:blacklist:user_invalid_after:<user_id>`.
9. Nếu có refresh token, backend revoke refresh token trong DB.
10. Backend xoá cookie `refreshToken`.
11. Backend xoá cookie cũ `auth_token` nếu còn tồn tại.
12. Frontend xoá Redux credentials.
13. Frontend xoá cookie marker `repoguard_session`.
14. Frontend redirect về `/login`.

## 11. Có hỗ trợ đăng nhập nhiều thiết bị không?

Có về mặt kỹ thuật.

Lý do:

- Mỗi lần login, backend tạo một refresh token mới.
- Mỗi refresh token được lưu thành một dòng riêng trong bảng `refresh_tokens`.
- Bảng `refresh_tokens` không có unique constraint trên `user_id`.
- Vì vậy một user có thể có nhiều refresh token cùng lúc.

Ví dụ:

```text
User login trên laptop -> tạo RT_laptop
User login trên điện thoại -> tạo RT_phone
Database có 2 dòng refresh token cho cùng user
```

Nhưng hệ thống chưa có quản lý đa thiết bị chuyên nghiệp:

- Chưa lưu tên thiết bị.
- Chưa lưu user agent.
- Chưa lưu IP.
- Chưa có trang "các phiên đang đăng nhập".
- Chưa có nút "logout thiết bị này" theo session id.
- Chưa có nút "logout tất cả thiết bị".

## 12. Có logout một thiết bị hoặc logout tất cả thiết bị không?

### 12.1. Logout một thiết bị

Có một phần, nhưng chưa thật sự sạch.

Khi logout trên thiết bị hiện tại:

- Refresh token của thiết bị hiện tại sẽ bị revoke nếu backend nhận được refresh token từ cookie/body.
- Access token hiện tại bị blacklist theo `jti`.
- Tất cả access token cũ của user cũng bị vô hiệu hoá qua key `user_invalid_after`.

Tình huống ví dụ:

```text
Laptop đang có RT_laptop và AT_laptop
Điện thoại đang có RT_phone và AT_phone
User bấm logout trên laptop
```

Kết quả:

- `RT_laptop` bị revoke.
- `AT_laptop` bị blacklist.
- `AT_phone` có thể cũng bị coi là revoked vì có `iat` trước mốc logout.
- Nhưng `RT_phone` vẫn còn trong DB và chưa bị revoke.
- Điện thoại có thể gọi `/auth/refresh` bằng `RT_phone` để lấy access token mới và tiếp tục dùng.

Kết luận:

- Logout hiện tại revoke được refresh token của session hiện tại.
- Nhưng lại làm các access token cũ của user ở thiết bị khác bị mất hiệu lực.
- Refresh token của thiết bị khác vẫn còn.
- Vì vậy chưa phải "logout một thiết bị" chuẩn theo nghĩa session management.

### 12.2. Logout tất cả thiết bị

Chưa có endpoint logout tất cả thiết bị đúng nghĩa.

Một logout-all chuyên nghiệp cần:

1. Revoke tất cả refresh token đang active của user trong DB.
2. Vô hiệu hoá tất cả access token chưa hết hạn của user.
3. Xoá cookie trên thiết bị hiện tại.
4. Nếu có session table, đánh dấu tất cả session là revoked.

Source hiện tại chưa có repository method kiểu:

```text
revoke_all_by_user(user_id)
```

Và chưa có endpoint kiểu:

```text
POST /api/auth/logout-all
```

## 13. Có revoke token, blacklist token không?

### 13.1. Access token

Có blacklist access token bằng Redis.

Redis key:

```text
auth:blacklist:jti:<jti>
```

Và có cơ chế invalidate access token theo user:

```text
auth:blacklist:user_invalid_after:<user_id>
```

### 13.2. Refresh token

Có revoke refresh token bằng database.

Refresh token bị revoke khi:

- Logout và backend nhận được refresh token.
- Refresh token được rotate thành token mới.

Cột được set:

```text
refresh_tokens.revoked_at
```

Nếu rotate, thêm:

```text
refresh_tokens.replaced_by_token_id
```

### 13.3. Refresh token blacklist

Không có Redis blacklist riêng cho refresh token.

Hệ thống dùng database `refresh_tokens.revoked_at` để biết refresh token có bị revoke hay không.

Đây là cách hợp lý vì refresh token sống lâu hơn và cần trạng thái server-side.

## 14. Có phân quyền role/user/admin không?

Có.

Trong `UserRole` có 2 role:

```text
user
admin
```

Bảng `users.role` mặc định là `user`.

Backend có dependency:

```text
get_current_admin
```

Dependency này:

1. Lấy current user.
2. Kiểm tra `current_user.role`.
3. Nếu role khác `admin`, trả lỗi `403 Admin role is required`.

Nhưng cần nói rõ:

- Hiện tại `backend/app/routers/admin.py` đang rỗng.
- `backend/app/main.py` hiện mới include `auth_router`.
- Chưa thấy endpoint admin nào đang sử dụng `get_current_admin`.
- Chưa thấy API để user tự đổi role.
- Chưa thấy script tạo admin trong source hiện tại.

Kết luận:

> Dự án đã có nền tảng RBAC với role `user/admin` và dependency check admin, nhưng các API admin thực tế chưa được triển khai/ghép vào app trong source hiện tại.

## 15. Frontend đang giữ token như thế nào?

### 15.1. Access token

Frontend lưu access token trong Redux memory:

```text
state.auth.accessToken
```

Ưu điểm:

- Không ghi access token vào localStorage.
- Giảm rủi ro token bị đọc lâu dài nếu có XSS.

Nhược điểm:

- Reload trang sẽ mất access token.
- Vì vậy frontend cần gọi `/auth/refresh` khi app bootstrap.

### 15.2. Refresh token

Refresh token được browser giữ trong cookie `refreshToken`.

Vì cookie là `HttpOnly`, JavaScript frontend không đọc được refresh token.

Đây là điểm tốt.

### 15.3. Session marker

Frontend tạo thêm cookie:

```text
repoguard_session=1
```

Cookie này dùng cho Next middleware để chặn route `/dashboard`.

Cần nói rõ:

- Cookie này không phải token thật.
- Cookie này không chứng minh user hợp lệ với backend.
- Nó chỉ giúp UI redirect nhanh hơn.
- Backend vẫn phải kiểm tra access token/refresh token thật.

## 16. Auto refresh trên frontend hoạt động như thế nào?

Frontend Axios có response interceptor.

Luồng:

1. Frontend gọi API với access token.
2. Nếu API trả `401`, frontend nghi access token hết hạn hoặc bị revoke.
3. Frontend gọi:

```text
POST /auth/refresh
```

4. Browser tự gửi cookie `refreshToken` nếu cookie hợp lệ.
5. Backend rotate refresh token và trả access token mới.
6. Frontend cập nhật Redux `accessToken`.
7. Frontend gọi lại request ban đầu với access token mới.
8. Nếu refresh thất bại, frontend xoá credentials, xoá session marker, redirect về `/login`.

Frontend có biến `refreshRequest` để tránh nhiều request cùng lúc gọi refresh lặp lại nhiều lần.

## 17. Những điểm đang làm tốt

Hệ thống có nhiều điểm tốt:

- Password không lưu plain text, được hash bằng bcrypt.
- Login trả lỗi chung `Invalid email or password`, không nói rõ email hay password sai.
- Access token có `jti`, có thể blacklist riêng từng token.
- JWT có `type` để tách access token và refresh token.
- Refresh token được lưu server-side trong DB.
- Refresh token trong DB là hash, không phải token gốc.
- Có refresh token rotation.
- Có revoke refresh token bằng `revoked_at`.
- Có `is_active` để khoá user.
- Có nền tảng RBAC `user/admin`.
- Frontend không lưu access token vào localStorage.
- Refresh token cookie là `HttpOnly`.
- Redis blacklist fail closed: nếu Redis lỗi, backend trả 503 thay vì bỏ qua blacklist.

## 18. Rủi ro bảo mật và điểm cần lưu ý

### 18.1. Cookie `Secure=True` đang bị hardcode

Trong config có:

```text
refresh_cookie_secure = False
refresh_cookie_samesite = "lax"
```

Nhưng trong route auth, khi set cookie lại hardcode:

```text
secure=True
samesite="lax"
```

Rủi ro:

- Ở môi trường local HTTP, cookie `Secure` có thể không được browser lưu/gửi.
- Auto refresh có thể bị lỗi khi chạy local.
- Config `refresh_cookie_secure` và `refresh_cookie_samesite` gần như bị bỏ qua.

Nên sửa:

- Dùng `settings.refresh_cookie_secure`.
- Dùng `settings.refresh_cookie_samesite`.
- Production bắt buộc `Secure=True`.
- Development có thể `Secure=False` nếu chạy HTTP local.

### 18.2. Register trả refresh token trong JSON

Login và refresh chỉ trả access token trong JSON, refresh token nằm trong HttpOnly cookie.

Nhưng register lại trả cả:

```json
{
  "refresh_token": "..."
}
```

Rủi ro:

- Refresh token có thể bị JavaScript đọc nếu frontend xử lý response.
- Không nhất quán với login/refresh.

Nên sửa:

- Register cũng nên trả `AccessTokenResponse` giống login, hoặc frontend register xong không auto login thì không cần set token.
- Nếu dùng cookie HttpOnly làm nơi chứa refresh token, tránh trả refresh token trong JSON.

### 18.3. Logout một thiết bị chưa rõ nghĩa

Logout hiện tại revoke refresh token hiện tại, nhưng cũng set `user_invalid_after`, làm access token cũ của các thiết bị khác bị revoke.

Rủi ro:

- User logout laptop có thể làm điện thoại bị `401` tạm thời.
- Nhưng điện thoại vẫn refresh được vì refresh token điện thoại chưa bị revoke.
- Hành vi này không sai hoàn toàn, nhưng dễ gây nhầm lẫn khi giải thích "logout một thiết bị" hay "logout tất cả thiết bị".

Nên tách rõ:

- Logout current device: revoke refresh token/session hiện tại, blacklist access token hiện tại.
- Logout all devices: revoke tất cả refresh token của user và set user access-token cutoff.

### 18.4. Chưa có rate limiting cho login/register/refresh

Source hiện tại chưa thấy rate limit.

Rủi ro:

- Kẻ xấu có thể brute force password.
- Có thể spam register.
- Có thể spam refresh.

Nên thêm:

- Rate limit theo IP.
- Rate limit theo email.
- Tăng delay khi login sai nhiều lần.
- Lock tạm thời nếu sai quá nhiều.

### 18.5. Chưa có CSRF token cho cookie-based refresh/logout

Refresh token nằm trong cookie. Cookie có `SameSite=Lax`, giúp giảm CSRF, nhưng chưa phải cơ chế CSRF đầy đủ.

Rủi ro:

- Các endpoint dùng cookie để xác thực nên cân nhắc CSRF token, đặc biệt nếu sau này SameSite=None hoặc cross-site deployment.

Nên thêm:

- CSRF double-submit token cho `/auth/refresh` và `/auth/logout`.
- Kiểm tra `Origin`/`Referer` cho request nhạy cảm.

### 18.6. JWT secret có default rỗng

Trong settings:

```text
jwt_secret_key = ""
```

Rủi ro:

- Nếu quên cấu hình secret ở production, token được ký bằng secret rỗng, rất nguy hiểm.

Nên sửa:

- Validate startup: nếu production mà `jwt_secret_key` rỗng thì app không được chạy.
- Dùng secret dài, random, lưu trong environment/secret manager.

### 18.7. Chưa có refresh token reuse detection nâng cao

Hệ thống có rotation, nhưng nếu refresh token cũ đã revoke bị dùng lại, hiện tại chỉ trả lỗi.

Rủi ro:

- Nếu attacker lấy được refresh token cũ, việc dùng lại token cũ là tín hiệu token bị lộ.
- Hệ thống nên revoke cả token family để phòng attacker đã có token mới.

Nên thêm:

- `token_family_id`.
- `parent_token_id`.
- `rotated_at`.
- Nếu token đã revoke bị dùng lại, revoke toàn bộ family và bắt user login lại.

### 18.8. Register user và tạo refresh token chưa atomic hoàn toàn

Trong register:

- `UserRepository.create` commit user ngay.
- Sau đó `AuthService` mới tạo refresh token và commit lần nữa.

Rủi ro:

- Nếu user tạo thành công nhưng tạo refresh token thất bại, database có user mới nhưng client nhận lỗi.
- User thử đăng ký lại sẽ gặp email đã tồn tại.

Nên sửa:

- Dùng cùng một transaction cho tạo user và tạo refresh token.
- Chỉ commit một lần khi tất cả thành công.

### 18.9. Chưa có email verification

User đăng ký xong là active ngay.

Rủi ro:

- Email có thể không thuộc về người đăng ký.
- Dễ tạo tài khoản ảo.

Nên thêm:

- Email verification token.
- Chỉ active user sau khi confirm email.

### 18.10. Password policy còn cơ bản

Backend chỉ yêu cầu:

- Password tối thiểu 8 ký tự.
- Tối đa 128 ký tự.

Frontend cũng yêu cầu tối thiểu 8 ký tự và confirm password.

Chưa có:

- Kiểm tra password phổ biến.
- Kiểm tra password đã bị lộ.
- Hướng dẫn mật khẩu mạnh.
- Change password.
- Forgot password.

### 18.11. Chưa có audit log auth

Source hiện tại chủ yếu log lỗi Redis. Chưa thấy audit log cho:

- Login thành công.
- Login thất bại.
- Logout.
- Refresh token reuse.
- Admin access.
- Đổi mật khẩu.

Nên thêm audit log để sau này điều tra sự cố.

### 18.12. Chưa có quản lý session/thiết bị

Refresh token table chưa có:

- `device_name`.
- `user_agent`.
- `ip_address`.
- `last_used_at`.
- `revoked_reason`.
- `created_by_login_id`.

Nên thêm nếu muốn có trang "Quản lý thiết bị đang đăng nhập".

### 18.13. Role admin có nền tảng nhưng chưa dùng nhiều

Có `UserRole.ADMIN` và `get_current_admin`.

Nhưng:

- Admin router rỗng.
- Main app mới include auth router.
- Chưa có script tạo admin.
- Chưa có policy chi tiết theo permission.

Nên nói với hội đồng:

> Dự án đã có nền tảng RBAC, nhưng admin feature thực tế chưa được hoàn thiện trong source hiện tại.

### 18.14. Chưa có cleanup expired refresh token

Refresh token hết hạn được từ chối khi dùng.

Nhưng chưa thấy job xoá refresh token hết hạn khỏi database.

Rủi ro:

- Bảng `refresh_tokens` phình to theo thời gian.

Nên thêm scheduled job xoá token hết hạn và token revoked quá lâu.

## 19. Nếu hỏi: "Access Token và Refresh Token khác nhau thế nào?"

Trả lời ngắn gọn:

> Access token là thẻ vào API, sống ngắn, frontend gửi trong header `Authorization`.
>
> Refresh token là giấy xin cấp thẻ mới, sống lâu hơn, nằm trong cookie `HttpOnly`, được lưu hash trong database và dùng để xin access token mới.

| Tiêu chí | Access Token | Refresh Token |
| --- | --- | --- |
| Mục đích | Gọi API cần đăng nhập. | Xin access token mới. |
| Thời gian sống | 15 phút. | 7 ngày. |
| Định dạng | JWT. | JWT. |
| Claim `type` | `access`. | `refresh`. |
| Lưu ở frontend | Redux memory. | Cookie `HttpOnly`. |
| Gửi lên server | Header `Authorization`. | Cookie `refreshToken` hoặc body. |
| Lưu database | Không. | Có, lưu hash trong `refresh_tokens`. |
| Revoke | Redis blacklist theo `jti`. | Set `revoked_at` trong DB. |
| Rotation | Không rotate. | Có rotate mỗi lần refresh. |

## 20. Nếu hỏi: "Refresh Token có lưu database không?"

Trả lời:

> Có, refresh token có lưu database, nhưng không lưu chuỗi token gốc.

Hệ thống lưu:

```text
HMAC-SHA256(refresh_token, jwt_secret_key)
```

vào cột:

```text
refresh_tokens.token_hash
```

Khi user gửi refresh token lên:

1. Backend hash token vừa nhận.
2. So với `token_hash` trong DB.
3. Nếu khớp và chưa revoke/chưa hết hạn thì chấp nhận.
4. Sau đó rotate sang refresh token mới.

## 21. Nếu hỏi: "Có hỗ trợ nhiều thiết bị không?"

Trả lời nên nói:

> Có về mặt kỹ thuật, vì mỗi lần login hệ thống tạo một refresh token record riêng cho user. Một user có thể có nhiều refresh token active trong bảng `refresh_tokens`. Tuy nhiên hệ thống chưa có session management chuyên nghiệp, chưa lưu thông tin thiết bị, IP, user agent, và chưa có API liệt kê/logout từng thiết bị.

## 22. Nếu hỏi: "Có logout một thiết bị và logout tất cả thiết bị không?"

Trả lời nên nói:

> Logout hiện tại là logout session hiện tại ở mức refresh token: nếu request có refresh token, backend revoke refresh token đó. Access token hiện tại bị blacklist. Tuy nhiên code cũng đặt mốc invalidate tất cả access token cũ của user, nên nó có ảnh hưởng tới access token trên thiết bị khác. Chưa có endpoint logout-all đúng nghĩa vì chưa revoke tất cả refresh token của user.

## 23. Nếu hỏi: "Có token rotation, revoke, blacklist không?"

Trả lời:

- Token rotation: có, áp dụng cho refresh token.
- Revoke refresh token: có, bằng cột `revoked_at`.
- Blacklist access token: có, bằng Redis key theo `jti`.
- Blacklist refresh token bằng Redis: không. Refresh token revoke bằng database.
- Reuse detection nâng cao: chưa có.

## 24. Nếu hỏi: "Có phân quyền user/admin không?"

Trả lời:

> Có nền tảng role `user` và `admin`. User model có field `role`, default là `user`. Backend có dependency `get_current_admin` để chặn route admin nếu user không phải admin. Tuy nhiên các API admin thực tế chưa được triển khai trong router admin, và main app hiện mới include auth router.

## 25. Nếu muốn auth chuyên nghiệp hơn thì nên bổ sung gì?

Nên ưu tiên theo thứ tự:

### 25.1. Sửa cookie config

- Không hardcode `secure=True`.
- Dùng `settings.refresh_cookie_secure`.
- Dùng `settings.refresh_cookie_samesite`.
- Production bắt buộc HTTPS và `Secure=True`.
- Cân nhắc cookie prefix `__Host-refreshToken` nếu phù hợp.

### 25.2. Bỏ refresh token khỏi JSON response register

- Register nên nhất quán với login.
- Refresh token nên chỉ nằm trong HttpOnly cookie.

### 25.3. Tách rõ logout current device và logout all devices

Thêm API:

```text
POST /api/auth/logout
POST /api/auth/logout-all
GET  /api/auth/sessions
DELETE /api/auth/sessions/{session_id}
```

Nên thêm thông tin session:

```text
session_id
user_id
refresh_token_hash
device_name
user_agent
ip_address
last_used_at
revoked_at
revoked_reason
```

### 25.4. Thêm refresh token family và reuse detection

Thêm các cột:

```text
token_family_id
parent_token_id
rotated_at
reuse_detected_at
```

Nếu token cũ đã revoke bị dùng lại:

- Đánh dấu reuse detected.
- Revoke toàn bộ token family.
- Bắt user đăng nhập lại.
- Ghi audit log.

### 25.5. Thêm rate limiting và brute-force protection

Áp dụng cho:

- `/auth/login`
- `/auth/register`
- `/auth/refresh`
- `/auth/forgot-password`

Nên limit theo:

- IP.
- Email.
- User id nếu biết.

### 25.6. Validate secret khi startup

Nếu production mà:

```text
jwt_secret_key = ""
```

thì app phải fail startup.

Nên dùng secret dài, random, ít nhất 32 bytes.

### 25.7. Thêm email verification

Luồng đề xuất:

1. Register tạo user `is_active=false` hoặc `email_verified=false`.
2. Gửi email verify.
3. User bấm link verify.
4. Mới cho login/đầy đủ tính năng.

### 25.8. Thêm forgot password và change password

Cần có:

- `POST /auth/forgot-password`
- `POST /auth/reset-password`
- `POST /auth/change-password`
- Revoke tất cả refresh token sau khi đổi password.

### 25.9. Thêm MFA/2FA nếu cần bảo mật cao

Có thể dùng:

- TOTP app.
- Email OTP.
- Recovery codes.

### 25.10. Thêm audit log

Ghi lại:

- Login thành công.
- Login thất bại.
- Logout.
- Refresh thành công.
- Refresh token reuse.
- Đổi password.
- Admin action.

### 25.11. Hoàn thiện RBAC

Nên có:

- Admin provisioning script.
- Endpoint admin được protect bằng `get_current_admin`.
- Permission chi tiết nếu sau này role nhiều hơn `user/admin`.

### 25.12. Thêm test auth

Cần test:

- Register thành công.
- Register trùng email.
- Login đúng/sai password.
- Login user inactive.
- Refresh token thành công.
- Refresh token bị revoke.
- Refresh token hết hạn.
- Rotation: token cũ không dùng lại được.
- Logout revoke access token.
- Logout revoke refresh token.
- Role admin bị chặn nếu user thường.

## 26. Sơ đồ tóm tắt flow

### 26.1. Login

```text
User nhập email/password
        |
        v
POST /api/auth/login
        |
        v
Backend tìm user theo email
        |
        v
Kiểm tra bcrypt password
        |
        v
Kiểm tra user active
        |
        v
Tạo Access Token + Refresh Token
        |
        v
Lưu hash Refresh Token vào DB
        |
        v
Trả Access Token trong JSON
Set Refresh Token vào HttpOnly cookie
        |
        v
Frontend lưu Access Token vào Redux
        |
        v
User vào dashboard
```

### 26.2. Gọi API bình thường

```text
Frontend có Access Token
        |
        v
Gắn Authorization: Bearer <access_token>
        |
        v
Backend decode JWT
        |
        v
Kiểm tra type=access, exp, jti
        |
        v
Kiểm tra Redis blacklist
        |
        v
Lấy user từ DB
        |
        v
Cho request đi tiếp
```

### 26.3. Refresh

```text
Access Token hết hạn
        |
        v
API trả 401
        |
        v
Frontend gọi POST /api/auth/refresh
        |
        v
Browser gửi cookie refreshToken
        |
        v
Backend decode Refresh Token
        |
        v
Hash token và tìm trong DB
        |
        v
Kiểm tra chưa revoke, chưa hết hạn
        |
        v
Tạo Access Token mới + Refresh Token mới
        |
        v
Lưu hash Refresh Token mới
Revoke Refresh Token cũ
        |
        v
Set cookie refreshToken mới
Trả Access Token mới
        |
        v
Frontend gọi lại request cũ
```

### 26.4. Logout

```text
User bấm Logout
        |
        v
POST /api/auth/logout
        |
        v
Backend kiểm tra Access Token
        |
        v
Blacklist Access Token jti trong Redis
        |
        v
Set user_invalid_after trong Redis
        |
        v
Nếu có Refresh Token thì revoke trong DB
        |
        v
Xoá refreshToken cookie
        |
        v
Frontend xoá Redux token và session marker
        |
        v
Về trang login
```

## 27. Câu trả lời mẫu ngắn gọn trước hội đồng

Nếu cần trả lời nhanh:

> Module Authentication hiện tại có register, login, refresh token, logout và `/me`. Password được hash bằng bcrypt. Hệ thống dùng JWT gồm access token và refresh token. Access token sống ngắn, mặc định 15 phút, frontend gửi bằng header Bearer và backend kiểm tra signature, expiry, type, blacklist Redis và user active. Refresh token sống 7 ngày, được set vào HttpOnly cookie, và có lưu trong PostgreSQL dưới dạng hash HMAC-SHA256 trong bảng `refresh_tokens`, không lưu token gốc. Mỗi lần refresh có token rotation: tạo refresh token mới, revoke token cũ bằng `revoked_at` và nối sang token mới bằng `replaced_by_token_id`. Logout blacklist access token trong Redis và revoke refresh token hiện tại nếu có. Hệ thống có nền tảng role `user/admin` và dependency check admin, nhưng admin endpoint chưa hoàn thiện. Đăng nhập nhiều thiết bị có về mặt kỹ thuật vì mỗi login tạo refresh token riêng, nhưng chưa có session management chuyên nghiệp, chưa có logout-all đúng nghĩa. Các điểm cần nâng cấp là rate limit, CSRF, email verification, forgot/change password, MFA, session/device management, refresh token reuse detection, audit log, và sửa cookie config để không hardcode Secure.

## 28. Trạng thái cuối cùng

Đánh giá ngắn:

- Auth core: đã có và khá tốt cho MVP.
- Refresh token server-side: đã có.
- Token rotation: đã có.
- Redis blacklist: đã có.
- RBAC nền tảng: đã có.
- Session management chuyên nghiệp: chưa có.
- Logout all devices: chưa có.
- Reuse detection nâng cao: chưa có.
- Rate limit/email verification/forgot password/MFA/audit log: chưa có.

Nếu trình bày trước hội đồng, nên nói dự án đã có nền móng tốt, nhưng vẫn là mức MVP/early production. Để lên mức production chuyên nghiệp cần bổ sung các mục ở phần 25.
