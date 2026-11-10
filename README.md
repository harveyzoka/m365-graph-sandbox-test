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

## Kiểm tra JSON

Kiểm tra cú pháp file batch trước khi gửi:

```bash
jq empty graph-batch-e5-renew.json
```

## Tenant test

Không lưu domain thật hoặc tài khoản thật trong repo public.

Ví dụ placeholder:

```text
<your-tenant>.onmicrosoft.com
```

## Weekly Microsoft Graph Testing

> **Lưu ý quan trọng:** Workflow `.github/workflows/graph-test-log.yml` hiện tại **chỉ ghi nhận kết quả kiểm thử thủ công** vào `test-log.md`, **chưa tự động gọi hay kiểm thử Microsoft Graph API**. Người vận hành cần thực hiện kiểm thử thực tế trên Microsoft Graph Explorer hoặc ứng dụng `Zoka Graph Test App` trước, sau đó mới chạy workflow để lưu nhật ký.

### 1. Cách mở GitHub Actions

1. Truy cập repository `m365-graph-sandbox-test` trên GitHub.
2. Chọn tab **Actions** ở thanh điều hướng phía trên.
3. Ở danh sách workflow bên trái, chọn **Microsoft Graph Test Log**.

### 2. Cách chạy workflow

1. Nhấn nút **Run workflow** (ở góc phải danh sách lần chạy).
2. Giữ nhánh mặc định là `main`.
3. Điền đầy đủ thông tin kết quả kiểm thử thực tế rồi nhấn nút **Run workflow** màu xanh để bắt đầu.

### 3. Cách nhập kết quả kiểm thử

Các tham số đầu vào của `workflow_dispatch`:

- **`feature`**: Tên tính năng hoặc kịch bản vừa kiểm thử (ví dụ: `Weekly Graph Sandbox Check`, `OneDrive Upload & Metadata`, v.v.).
- **`batch`**: Kết quả chạy batch request (`graph-batch-e5-renew.json`) qua endpoint `/$batch` (`PASS` / `FAIL` / `NOT_RUN`, mặc định: `NOT_RUN`).
- **`file_write`**: Kết quả kiểm thử ghi file lên thư mục OneDrive `Graph API Test` bằng phương thức `PUT` (`PASS` / `FAIL` / `NOT_RUN`, mặc định: `NOT_RUN`).
- **`metadata`**: Kết quả kiểm thử truy vấn metadata bằng phương thức `GET` (`PASS` / `FAIL` / `NOT_RUN`, mặc định: `NOT_RUN`).
- **`notes`**: Ghi chú ngắn gọn về kết quả hoặc mã lỗi HTTP nếu có. **Tuyệt đối không** dán Access Token, Refresh Token, Client Secret, hoặc URL tải file có chứa `tempauth` token.
- **`dry_run`**: Tùy chọn chạy thử nghiệm (`true` / `false`, mặc định: `false`). Khi bật `true`, workflow chỉ hiển thị bản xem trước trong Job Summary mà không ghi vào `test-log.md`.

**Quy tắc ghi nhận:**
- Mặc định tất cả trạng thái là `NOT_RUN`.
- Chỉ chọn `PASS` cho những hạng mục đã thực sự chạy và xác nhận thành công.
- Nếu cả 3 hạng mục (`batch`, `file_write`, `metadata`) đều để `NOT_RUN`, workflow sẽ **từ chối ghi log** và báo lỗi để tránh ghi nhận rỗng.

### 4. Cách xem lịch sử `test-log.md`

- Mở trực tiếp file [`test-log.md`](./test-log.md) tại thư mục gốc của repository để xem toàn bộ các mục nhật ký được sắp xếp nối tiếp theo thời gian (`Asia/Ho_Chi_Minh`).
- Hoặc nhấn vào **History** (lịch sử commit) của file `test-log.md` để đối chiếu từng lần ghi log do `github-actions[bot]` thực hiện.

### 5. Cách xử lý lỗi workflow

- **Lỗi tất cả bài test đều `NOT_RUN`**: Cần chọn ít nhất một hạng mục có kết quả thực tế (`PASS` hoặc `FAIL`) trước khi bấm chạy.
- **Lỗi phát hiện dữ liệu nhạy cảm**: Kiểm tra lại trường `feature` và `notes`, xóa mọi chuỗi JWT (`eyJ...`), `Bearer ...`, `tempauth=` hoặc `client_secret`.
- **Lỗi `Permission to ... denied to github-actions[bot]` (403)**:
  - Vào **Settings** → **Actions** → **General** → mục **Workflow permissions**, 확인 đã bật **Read and write permissions** (hoặc workflow đã khai báo `permissions: contents: write`).
- **Lỗi không thể push trực tiếp do Branch Protection trên nhánh `main`**:
  - Workflow sẽ tự động đẩy commit sang nhánh phụ `log/graph-test-YYYYMMDD-HHMMSS` để không làm mất log; sau đó bạn có thể tạo Pull Request vào `main`.
  - Để cho phép push tự động, có thể thêm `github-actions[bot]` vào danh sách **Allow specified actors to bypass required pull requests** trong cấu hình Branch Protection / Ruleset của nhánh `main`.

### 6. Báo cáo tổng hợp tự động hằng tuần (`logs/weekly-summary.md`)

Bên cạnh việc ghi nhật ký kiểm thử thủ công vào `test-log.md`, repository có thêm workflow `.github/workflows/weekly-activity-summary.yml` và script `scripts/generate_weekly_summary.py` để tổng hợp hoạt động hằng tuần vào [`logs/weekly-summary.md`](./logs/weekly-summary.md):

- **Giữ nguyên chế độ Public an toàn**:
  - Chỉ commit các con số thống kê tổng hợp (số lượng commit thực tế, số ngày có commit, số lần chạy workflow, số lượng đối tượng/sự kiện Microsoft Graph ở mức đếm tổng).
  - Tuyệt đối không commit dữ liệu gốc, API response, Access Token, Client Secret, Tenant ID, User ID, File ID, domain `.onmicrosoft.com` hoặc địa chỉ email.
  - Không in thông tin nhạy cảm hoặc nội dung phản hồi lỗi ra GitHub Actions logs.
- **Cơ chế chống vòng lặp & chống trùng lặp**:
  - Chỉ chạy trên nhánh mặc định `main` (`schedule` lúc 08:00 sáng Thứ Hai hằng tuần theo giờ `Asia/Ho_Chi_Minh` hoặc `workflow_dispatch`), không chạy trên Pull Request không tin cậy và bỏ qua nếu tác nhân là `github-actions[bot]`.
  - Sử dụng mã băm (`FINGERPRINT`) theo từng tuần ISO (`YYYY-Www`): chỉ tạo commit khi số liệu trong tuần có thay đổi thực tế và gắn cờ `[skip ci]`.
- **Xác thực tối thiểu & trung thực dữ liệu**:
  - Ưu tiên sử dụng **OIDC Federated Credentials** (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID` với quyền `id-token: write`) hoặc GitHub Secrets ở mức quyền đọc tối thiểu (`User.Read.All` / `Directory.Read.All`, `AuditLog.Read.All`).
  - Nếu chưa cấu hình xác thực hoặc chưa cấp quyền Microsoft Graph tương ứng, báo cáo hiển thị rõ `UNAVAILABLE`, tuyệt đối không sinh dữ liệu giả hay tạo hoạt động giả.


