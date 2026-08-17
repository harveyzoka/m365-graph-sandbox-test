# Microsoft 365 E5 Developer Sandbox Test

Repo này dùng để lưu ghi chú và script test cho Microsoft 365 E5 Developer Sandbox.

## Chuẩn bị

- Tài khoản Microsoft 365 Developer Sandbox.
- Quyền truy cập Microsoft Graph Explorer.

## Mục đích

- Test Microsoft Graph API bằng Graph Explorer.
- Lưu batch request dùng để kiểm tra tenant.
- Ghi lại hoạt động dev cơ bản với Microsoft 365:
  - Entra App Registration
  - OneDrive test files
  - Outlook mail test
  - Calendar event test
  - Teams / SharePoint test

## Chạy batch request

1. Mở Microsoft Graph Explorer và đăng nhập tenant test.
2. Chọn phương thức `POST` với endpoint `https://graph.microsoft.com/v1.0/$batch`.
3. Dùng nội dung `graph-batch-e5-renew.json` làm request body.

## Quyền Graph

Các request mẫu có thể cần `User.Read`, `User.ReadBasic.All`, `Group.Read.All`,
`Files.Read`, `Calendars.Read` và `Mail.Read`.

## Tenant test

Không lưu domain thật hoặc tài khoản thật trong repo public.

Ví dụ placeholder:

```text
<your-tenant>.onmicrosoft.com
```
