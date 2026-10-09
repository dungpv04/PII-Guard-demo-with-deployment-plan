# PII Guard cho Claude Code và GitHub Copilot

Che thông tin cá nhân và thông tin nội bộ (SĐT, CCCD, MST, email, số thẻ, tên miền/dự án nội bộ…) **trước khi** prompt rời khỏi mạng của tổ chức, trong khi lập trình viên vẫn dùng **gói subscription của chính họ**:

- **Claude Code**: đăng nhập tài khoản Claude (Pro/Max/Team/Enterprise) như bình thường. Không cần API key hay API Platform.
- **GitHub Copilot**: đăng nhập GitHub như bình thường. Bao phủ chat, agent và gợi ý code inline.

Hướng dẫn cài đặt và triển khai đầy đủ: **[DEPLOYMENT_DEPLOYMENT_PLAN.md](./DEPLOYMENT_DEPLOYMENT_PLAN.md)**.

```
Claude Code ──(OAuth của dev)──► claude-proxy :4000 ──────► api.anthropic.com
                                   ├─ ảnh/PDF ─► extractor (tùy chọn)
Copilot ─────(HTTP proxy)──────► copilot-proxy :8080 ─────► GitHub Copilot
                                   │
                                   ▼  dùng chung
               presidio-analyzer (rule VN, danh sách cấm/cho phép, SLM tùy chọn)
               + presidio-analyzer-core + presidio-anonymizer
```

| | Claude Code | Copilot |
| --- | --- | --- |
| Cách đi qua guard | `ANTHROPIC_BASE_URL` trỏ vào `claude-proxy` | Proxy HTTP của editor trỏ vào `copilot-proxy` |
| Ai trả tiền model | Gói Claude của dev (proxy chuyển tiếp nguyên OAuth token) | Gói Copilot của dev |
| Request gửi lên | Giữ nguyên từng byte, chỉ thay các đoạn PII | Chỉ thay các đoạn PII |
| PII trong câu trả lời | Được khôi phục về giá trị thật, kể cả trong lệnh tool | Không khôi phục: model chỉ thấy `<EMAIL_ADDRESS>` |
| Gặp bí mật (`SECRET`, khi bật SLM) | Từ chối request (`400`) | Từ chối request (`403`) |
| Ảnh/PDF | `FILE_POLICY`: loại bỏ (mặc định) hoặc trích chữ rồi che | Không quét |
| Guard lỗi | `503`, không gửi bản gốc | `503`, không gửi bản gốc |

## Cài đặt nhanh

Yêu cầu: Linux hoặc WSL2, Docker + Compose v2, Python 3.

```bash
cp .env.example .env        # đổi PII_GUARD_KEY nếu máy dùng chung
./scripts/up.sh             # lần đầu tải khoảng 2–3 GB image
./scripts/test.sh           # unit test
```

`up.sh` kiểm tra cấu hình, khởi động, và chờ mọi service healthy. Dừng bằng `docker compose down`. Mọi biến cấu hình được giải thích trong `.env.example` và [DEPLOYMENT_DEPLOYMENT_PLAN.md mục 4](./DEPLOYMENT_DEPLOYMENT_PLAN.md#4-tham-chiếu-cấu-hình-env).

## Claude Code

```bash
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN      # có một trong hai là mất login subscription
export ANTHROPIC_BASE_URL=http://localhost:4000
export ANTHROPIC_CUSTOM_HEADERS="x-pii-guard-key: Bearer sk-local-dev"     # = PII_GUARD_KEY trong .env
claude
```

1. Chưa đăng nhập thì gõ `/login` → **Claude account with subscription**.
2. Gõ `/status`: phương thức đăng nhập là tài khoản Claude, "Anthropic base URL" là `http://localhost:4000`.
3. Xem model thực sự nhận được gì:

   ```
   SĐT của tôi là 0912345678, email an@example.com.
   Chép lại đúng đoạn bạn nhận được, nhưng thay mọi ký tự < bằng [
   ```

   Thấy dạng `[VN_PHONE_3>` và `[EMAIL_ADDRESS_4>` là guard đang hoạt động (số thứ tự tùy request, vì ngữ cảnh Claude Code tự chèn cũng được đánh số). Hỏi bình thường thì bạn sẽ thấy lại số thật, vì proxy khôi phục giá trị trong câu trả lời.

Biến `export` chỉ có hiệu lực trong terminal đó, **không** áp dụng cho extension VS Code. Để dùng lâu dài, hoặc triển khai cho cả tổ chức bằng managed settings, xem [DEPLOYMENT_PLAN.md mục 6](./DEPLOYMENT_PLAN.md#6-claude-code).

Lưu ý: auto mode qua guard có thể bị treo giữa chừng. Nên dùng chế độ thường hoặc accept-edits (Shift+Tab).

## GitHub Copilot

1. Tin CA của proxy (sinh ở lần chạy đầu, `copilot_proxy/ca/mitmproxy-ca-cert.pem`):

   ```bash
   sudo cp copilot_proxy/ca/mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/pii-guard.crt
   sudo update-ca-certificates
   ```

   macOS, Windows, và trường hợp VS Code chạy trên Windows còn stack chạy trong WSL: xem [DEPLOYMENT_PLAN.md mục 7.2](./DEPLOYMENT_PLAN.md#72-bước-1-tin-ca-của-proxy).
2. Đặt proxy của editor là `http://localhost:8080` (VS Code: **Http: Proxy**, giữ **Proxy Strict SSL** bật; JetBrains: HTTP Proxy → Manual). Copilot CLI: `HTTPS_PROXY=http://localhost:8080` và `NODE_EXTRA_CA_CERTS=<đường dẫn CA>`.
3. Khởi động lại editor, đăng nhập GitHub, gửi một câu chat có email giả, rồi kiểm tra:

   ```bash
   docker compose logs -f copilot-proxy | grep copilot-pii
   # copilot-pii host=api.githubcopilot.com path=/chat/completions action=mask entities={'EMAIL_ADDRESS': 1}
   ```

## Tùy chọn

| Tính năng | Bật bằng | Chi tiết |
| --- | --- | --- |
| Trích chữ PDF rồi che (thay vì loại bỏ file) | `FILE_POLICY=extract` | [DEPLOYMENT_PLAN.md mục 8](./DEPLOYMENT_PLAN.md#8-file-pdf-và-ocr) |
| SLM phát hiện thêm bí mật, địa chỉ, số tài khoản (cần GPU) | `ENABLE_SLM_GATE=1` | [DEPLOYMENT_PLAN.md mục 9](./DEPLOYMENT_PLAN.md#9-tầng-tùy-chọn-slm-ner-ml-cổ-điển) |
| Che SĐT/CCCD kể cả khi không có từ khóa đi kèm | `VN_PHONE_SCORE=0.8`, `VN_CCCD_SCORE=0.8` | [DEPLOYMENT_PLAN.md mục 5](./DEPLOYMENT_PLAN.md#5-chính-sách-phát-hiện-và-cách-tinh-chỉnh) |
| Thêm tên dự án, khách hàng, hostname nội bộ | `INTERNAL_DENY_LIST=...` | [DEPLOYMENT_PLAN.md mục 5.3](./DEPLOYMENT_PLAN.md#53-tinh-chỉnh-thường-gặp) |
| Hook cảnh báo placeholder bị ghi xuống file | `./scripts/install-hook.sh` | [DEPLOYMENT_PLAN.md mục 6.5](./DEPLOYMENT_PLAN.md#65-hook-cảnh-báo-placeholder-bị-ghi-xuống-file) |

Xem guard sẽ xử lý một đoạn văn bản thế nào:

```bash
./scripts/analyze.sh "Gọi sđt 0912345678, CCCD 079204859403, thẻ 4111 1111 1111 1111"
```

## Kiểm thử

```bash
./scripts/test.sh                       # unit test, không cần Docker
./scripts/up.sh test                    # upstream Claude là mock local, không tốn quota
./scripts/test_claude_subscription.sh   # chạy claude thật với token giả, 2 lượt; kỳ vọng toàn PASS
./scripts/up.sh                         # quay lại chế độ thật
```

Chạy lại `test_claude_subscription.sh` sau mỗi lần nâng Claude Code. Checklist nghiệm thu: [DEPLOYMENT_PLAN.md mục 11](./DEPLOYMENT_PLAN.md#11-kiểm-thử-nghiệm-thu).

## Xử lý sự cố thường gặp

| Hiện tượng | Cách xử lý |
| --- | --- |
| `up.sh` báo cổng 4000/8080 đang bị container khác dùng | Dừng stack đó trước (`docker compose down` trong thư mục của nó) |
| `401 PII guard: missing or invalid x-pii-guard-key` | Sửa khóa trong `ANTHROPIC_CUSTOM_HEADERS` cho khớp `PII_GUARD_KEY` |
| `/status` hiện API key thay vì tài khoản Claude | Gỡ `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` / `apiKeyHelper` |
| Treo **chỉ** trong repo git | Giữ `noreply@anthropic.com` trong `ALLOW_LIST` |
| Treo khi bật auto mode | Tắt auto mode khi đi qua guard |
| `429 rate_limit_error` | Giới hạn của gói Claude; chờ hoặc đổi model |
| Số điện thoại không bị che | Không có từ khóa đi kèm: đặt `VN_PHONE_SCORE=0.8` |
| Copilot lỗi chứng chỉ | Cài CA vào kho tin cậy, khởi động lại editor |

Danh sách đầy đủ: [DEPLOYMENT_PLAN.md mục 13](./DEPLOYMENT_PLAN.md#13-xử-lý-sự-cố). Giới hạn đã biết: [DEPLOYMENT_PLAN.md mục 14](./DEPLOYMENT_PLAN.md#14-giới-hạn-đã-biết).

## Cấu trúc

```
docker-compose.yml     claude-proxy, copilot-proxy, presidio-*, extractor (ocr), ollama (slm), upstream-recorder (test)
claude_proxy/          reverse proxy Claude Code: che PII, xử lý file, khôi phục placeholder
copilot_proxy/         addon mitmproxy cho Copilot
analyzer_plus/         lớp bọc Presidio: rule tiếng Việt, allow list, SLM gate
extractor/             trích văn bản PDF (marker-pdf) trong sandbox
hooks/                 hook Claude Code cảnh báo placeholder bị ghi xuống file
scripts/               up.sh, analyze.sh, test.sh, test_claude_subscription.sh, install-hook.sh
DEPLOYMENT_PLAN.md                hướng dẫn cài đặt và triển khai chi tiết
```
