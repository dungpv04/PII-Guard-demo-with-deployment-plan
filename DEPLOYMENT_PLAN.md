# PII Guard: hướng dẫn cài đặt và triển khai

|  |  |
| --- | --- |
| **Mục đích** | Cài đặt, cấu hình và vận hành PII Guard: lớp che thông tin nhạy cảm đặt giữa Claude Code / GitHub Copilot và nhà cung cấp mô hình |
| **Đối tượng** | Đội hạ tầng/DevOps triển khai, đội bảo mật duyệt chính sách, lập trình viên cấu hình máy của mình |
| **Phạm vi** | Claude Code (đăng nhập bằng gói Claude), GitHub Copilot (đăng nhập GitHub), trên Linux với Docker Compose |
| **Quy ước** | **\[Cần xác nhận\]**: chi tiết phụ thuộc phiên bản hoặc chính sách của nhà cung cấp, cần thử lại trên phiên bản bạn triển khai |

**Mục lục**

1. [Tổng quan](#1-tổng-quan)
2. [Yêu cầu](#2-yêu-cầu)
3. [Cài đặt nhanh trên một máy](#3-cài-đặt-nhanh-trên-một-máy)
4. [Tham chiếu cấu hình `.env`](#4-tham-chiếu-cấu-hình-env)
5. [Chính sách phát hiện và cách tinh chỉnh](#5-chính-sách-phát-hiện-và-cách-tinh-chỉnh)
6. [Claude Code](#6-claude-code)
7. [GitHub Copilot](#7-github-copilot)
8. [File, PDF và OCR](#8-file-pdf-và-ocr)
9. [Tầng tùy chọn: SLM, NER, ML cổ điển](#9-tầng-tùy-chọn-slm-ner-ml-cổ-điển)
10. [Triển khai trên máy chủ cho cả tổ chức](#10-triển-khai-trên-máy-chủ-cho-cả-tổ-chức)
11. [Kiểm thử nghiệm thu](#11-kiểm-thử-nghiệm-thu)
12. [Vận hành](#12-vận-hành)
13. [Xử lý sự cố](#13-xử-lý-sự-cố)
14. [Giới hạn đã biết](#14-giới-hạn-đã-biết)
15. [Phụ lục](#15-phụ-lục)

---

## 1. Tổng quan

### 1.1 PII Guard làm gì

PII Guard che thông tin cá nhân và thông tin nội bộ **trước khi** nội dung rời khỏi mạng của tổ chức, trong khi lập trình viên vẫn dùng **gói subscription của chính họ**:

- **Claude Code**: dev đăng nhập tài khoản Claude (Pro/Max/Team/Enterprise) như bình thường. Không cần API key hay tài khoản API Platform. Chi phí tính vào gói của dev.
- **GitHub Copilot**: dev đăng nhập GitHub như bình thường. Bao phủ chat, agent và gợi ý code inline.

Những gì được che (mặc định): số điện thoại (VN và quốc tế), CCCD, mã số thuế, email, số thẻ thanh toán, tên miền/hostname/tên dự án nội bộ trong danh sách cấm, và khi bật SLM thêm địa chỉ, số tài khoản, bí mật (mật khẩu, khóa). Bí mật thì **chặn cả request** thay vì che.

### 1.2 Kiến trúc

```
                       ┌──────────────────────── máy chủ PII Guard (Docker Compose) ───────────────────────┐
 Claude Code ─────────►│ claude-proxy :4000 ──────────────────────────────────────────────────────────────┼──► api.anthropic.com
 (OAuth của dev)       │   │  ├─ ảnh/PDF ─► extractor (marker-pdf, tùy chọn)                               │
                       │   │  └─ văn bản ─► presidio-analyzer (analyzer_plus) ─► presidio-analyzer-core    │
                       │   │                    └─► ollama / Ollama trên máy chủ (SLM, tùy chọn)           │
 Copilot ─────────────►│ copilot-proxy :8080 (mitmproxy) ────────────────────────────────────────────────┼──► GitHub Copilot
 (đăng nhập GitHub)    │   └─ văn bản ─► presidio-analyzer ─► presidio-anonymizer                          │
                       └───────────────────────────────────────────────────────────────────────────────────┘
```

| Service | Cổng | Vai trò | Bắt buộc |
| --- | --- | --- | --- |
| `claude-proxy` | 4000 | Reverse proxy cho Claude Code: che PII, chuyển tiếp nguyên vẹn mọi thứ khác, khôi phục placeholder trong câu trả lời | Có (cho Claude Code) |
| `copilot-proxy` | 8080 | HTTP forward proxy (mitmproxy) chỉ giải mã host inference của Copilot | Có (cho Copilot) |
| `presidio-analyzer` | nội bộ | Lớp bọc Presidio: rule tiếng Việt, danh sách cấm/cho phép, SLM tùy chọn | Có |
| `presidio-analyzer-core` | nội bộ | Presidio Analyzer gốc (regex, checksum, spaCy) | Có |
| `presidio-anonymizer` | nội bộ | Thay thế văn bản cho đường Copilot | Có (cho Copilot) |
| `extractor` | nội bộ | Trích văn bản từ PDF (marker-pdf), chạy trong sandbox | Khi `FILE_POLICY=extract` |
| `ollama` | nội bộ | Máy chủ SLM đóng gói kèm | Khi dùng SLM trong stack |
| `upstream-recorder` | 8089 | Upstream giả để kiểm thử | Chỉ ở chế độ test |

### 1.3 Luồng xử lý Claude Code

1. Dev đặt `ANTHROPIC_BASE_URL` trỏ vào `claude-proxy`. Claude Code vẫn dùng login claude.ai và gửi `Authorization: Bearer <OAuth token của dev>`, kèm header `x-pii-guard-key` để xác thực với gateway.
2. `claude-proxy` kiểm tra `x-pii-guard-key`, rồi xử lý request `/v1/messages`:
   - Ảnh/PDF xử lý theo `FILE_POLICY` (mục 8).
   - Mọi đoạn văn bản hội thoại (system, tin nhắn, kết quả tool, input của tool call cũ) được gửi tới analyzer. Phát hiện thuộc chính sách được thay bằng placeholder đánh số, ví dụ `<VN_PHONE_1>`. Cùng một giá trị luôn ra cùng một placeholder.
   - Phát hiện thuộc nhóm BLOCK (mặc định `SECRET`) thì trả lỗi `400` cho Claude Code, không gửi gì đi.
3. Request được chuyển tiếp lên `api.anthropic.com` **nguyên vẹn**: header OAuth, `anthropic-beta`, user-agent, query string, định nghĩa tool, khối `thinking`. Chỉ các đoạn chữ PII bị thay. Request không có PII thì được gửi đi đúng từng byte gốc.
4. Câu trả lời (stream SSE) được chuyển về ngay khi nhận, placeholder được **khôi phục về giá trị thật**, kể cả trong lệnh tool (Bash, Edit, Write). Nhờ vậy Claude Code chạy lệnh và ghi file với dữ liệu đúng.
5. Analyzer hoặc extractor lỗi thì request bị từ chối (`503`), không bao giờ gửi bản gốc (fail-closed).

Không giữ nguyên request như vậy thì Anthropic có thể treo hoặc từ chối request đăng nhập bằng subscription. Đó là lý do proxy không dùng một gateway đa năng (vốn viết lại request theo định dạng riêng của nó).

### 1.4 Luồng xử lý Copilot

1. Editor đặt HTTP proxy là `copilot-proxy`, và máy dev tin CA của proxy.
2. Proxy **chỉ giải mã** `*.githubcopilot.com` và `copilot-proxy.githubusercontent.com`. Đăng nhập GitHub (`github.com`, `api.github.com`) và mọi host khác đi xuyên qua nguyên vẹn, không giải mã.
3. Với request inference, proxy quét mọi chuỗi trong JSON nằm dưới các khóa chứa văn bản (`content`, `text`, `prompt`, `suffix`, `input`, `instructions`, `system`) và thay PII bằng `<EMAIL_ADDRESS>`, `<VN_PHONE>`… Có `SECRET` thì trả `403`. Analyzer lỗi thì trả `503`.
4. Câu trả lời **không** được khôi phục (giao thức Copilot không công khai): model chỉ thấy placeholder và có thể viết placeholder vào code gợi ý.

---

## 2. Yêu cầu

### 2.1 Phần mềm

| Thành phần | Phiên bản | Ghi chú |
| --- | --- | --- |
| Linux x86_64 | Ubuntu 22.04+/Debian 12+ hoặc tương đương; WSL2 dùng được cho máy cá nhân | |
| Docker Engine + Compose v2 | Engine 24+, Compose 2.20+ | `docker compose version` |
| Python 3 | 3.10+ | Chỉ để chạy script kiểm thử trên máy chủ |
| Claude Code | Đã kiểm thử với 2.1.295 | Trên máy dev |
| VS Code / JetBrains / Copilot CLI | Bản hiện hành | Trên máy dev |
| NVIDIA Container Toolkit | Tùy chọn | Chỉ khi chạy SLM trong container có GPU |

### 2.2 Tài nguyên máy chủ (điểm khởi đầu)

| Cấu hình | CPU | RAM | Đĩa | Ghi chú |
| --- | --- | --- | --- | --- |
| Cơ bản (Claude Code + Copilot, `FILE_POLICY=block`) | 4 vCPU | 8 GB | 20 GB | Presidio core có spaCy, image khoảng 2 GB |
| + trích PDF (`FILE_POLICY=extract`) | +4 vCPU | +8 GB | +10 GB | Extractor có PyTorch CPU; lần trích đầu mỗi file khoảng 20–30 giây |
| + SLM | GPU NVIDIA ≥ 4 GB VRAM | +4 GB | +2 GB | Không dùng SLM trên CPU (mục 9.1) |

### 2.3 Mạng

| Từ | Tới | Mục đích |
| --- | --- | --- |
| Máy dev | Máy chủ PII Guard `:4000` (hoặc `:443` qua edge TLS) | Claude Code |
| Máy dev | Máy chủ PII Guard `:8080` | Copilot |
| Máy dev | Các endpoint đăng nhập của Anthropic (claude.ai) và GitHub | Đăng nhập, làm mới token (không chứa nội dung prompt) |
| Máy chủ | `api.anthropic.com:443` | Upstream của Claude Code |
| Máy chủ | `*.githubcopilot.com:443`, `copilot-proxy.githubusercontent.com:443`, `github.com`, `api.github.com` | Upstream và tunnel của Copilot |
| Máy chủ (chỉ lúc cài) | Docker registry, PyPI, Hugging Face, ollama.com | Tải image, gói, model |

### 2.4 Tài khoản

- **Claude**: mỗi dev có tài khoản Claude, nên thuộc tổ chức Team/Enterprise của công ty (mục 6.4 cách ép đăng nhập đúng tổ chức). Gói cá nhân Pro/Max về kỹ thuật vẫn chạy, nhưng việc dùng gói cá nhân cho mã nguồn công ty cần bộ phận pháp chế quyết định.
- **Copilot**: license Copilot Business/Enterprise hoặc Individual như bình thường.

---

## 3. Cài đặt nhanh trên một máy

Dùng cho một dev tự cài trên máy mình, hoặc cho bước thử trước khi triển khai lên máy chủ.

**Bước 1. Lấy mã nguồn và tạo `.env`**

```bash
cd ~/code/pii-guard            # thư mục repo
cp .env.example .env
# đổi PII_GUARD_KEY thành chuỗi ngẫu nhiên nếu máy dùng chung:
#   sed -i "s/^PII_GUARD_KEY=.*/PII_GUARD_KEY=sk-$(openssl rand -hex 32)/" .env
```

**Bước 2. Khởi động**

```bash
./scripts/up.sh
```

`up.sh` làm theo thứ tự:

1. Kiểm tra trước khi chạy (preflight): có `.env`, có `PII_GUARD_KEY`, Docker đang chạy, cổng 4000/8080 không bị stack khác chiếm, `FILE_POLICY` hợp lệ.
2. Bật thêm `extractor` nếu `FILE_POLICY=extract`, bật `ollama` nếu dùng SLM đóng gói.
3. Build image cục bộ, khởi động, chờ mọi service healthy.
4. Nếu dùng `ollama`: tải model `SLM_MODEL` (chỉ lần đầu).

Lần đầu mất vài phút để tải khoảng 2–3 GB image. Kết thúc sẽ in:

```
PII Guard is up (mode=run).
  Claude Code : ANTHROPIC_BASE_URL=http://localhost:4000  (README: Claude Code)
  Files       : FILE_POLICY=block
  SLM gate    : off
  Copilot     : HTTP proxy http://localhost:8080, CA copilot_proxy/ca/mitmproxy-ca-cert.pem  (README: Copilot)
```

**Bước 3. Kiểm tra nhanh**

```bash
./scripts/test.sh                                   # unit test, không cần mạng
./scripts/analyze.sh "SĐT 0912345678, email an@example.com, thẻ 4111 1111 1111 1111"
docker compose ps                                   # mọi service "healthy"/"Up"
```

**Bước 4. Cấu hình client**: Claude Code theo mục 6.2, Copilot theo mục 7.2–7.4.

**Dừng / khởi động lại**

```bash
docker compose down        # dừng toàn bộ (giữ CA Copilot và model SLM)
./scripts/up.sh            # khởi động lại; cũng là lệnh áp dụng thay đổi trong .env
```

> Mỗi máy chỉ chạy được **một** stack dùng cổng 4000. Nếu đang chạy một gateway khác (ví dụ một bản PoC cũ), dừng nó trước; `up.sh` sẽ báo tên container đang giữ cổng.

---

## 4. Tham chiếu cấu hình `.env`

Áp dụng thay đổi bằng `./scripts/up.sh`. Biến đặt sẵn trong shell được ưu tiên hơn `.env`, nên có thể thử nhanh: `FILE_POLICY=extract ./scripts/up.sh`.

### 4.1 Xác thực

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `PII_GUARD_KEY` | `sk-local-dev` | Khóa dev phải gửi trong header `x-pii-guard-key`. **Đổi** khi dùng chung. Rỗng = không kiểm tra (chỉ dùng trên máy cá nhân) |

### 4.2 Chính sách phát hiện (Claude Code)

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `MASK_ENTITIES` | `PHONE_NUMBER,EMAIL_ADDRESS,CREDIT_CARD,VN_PHONE,VN_CCCD,VN_TAX_ID,INTERNAL_TERM,ADDRESS,BANK_ACCOUNT` | Loại entity được thay bằng placeholder |
| `BLOCK_ENTITIES` | `SECRET` | Loại entity khiến request bị từ chối |
| `SCORE_THRESHOLD` | `0.75` | Điểm tối thiểu để một phát hiện được áp dụng |
| `INTERNAL_DENY_LIST` | `corp.local,internal.example.com,db-prod-01` | Chuỗi nội bộ luôn bị che (`INTERNAL_TERM`), cách nhau bằng dấu phẩy |
| `ALLOW_LIST` | `noreply@anthropic.com` | Chuỗi không bao giờ bị che. **Không xóa** `noreply@anthropic.com` (mục 5.4) |
| `VN_PHONE_SCORE` | `0.72` | Điểm gốc rule SĐT di động VN (mục 5.3) |
| `VN_CCCD_SCORE` | `0.55` | Điểm gốc rule CCCD |
| `VN_TAX_ID_SCORE` | `0.48` | Điểm gốc rule MST |
| `COPILOT_SCORE_THRESHOLD` | `0.75` | Ngưỡng cho đường Copilot |

### 4.3 File

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `FILE_POLICY` | `block` | `block`, `extract` hoặc `pass` (mục 8) |
| `EXTRACT_OCR` | `off` | Giữ `off` (mục 8.6) |
| `EXTRACT_MAX_BYTES` | `20971520` | Kích thước file tối đa (byte) |
| `EXTRACT_MAX_PAGES` | `100` | Số trang PDF tối đa |
| `EXTRACT_TIMEOUT_S` | `120` | Thời gian trích tối đa mỗi file |
| `EXTRACT_MEMORY_LIMIT` / `EXTRACT_CPU_LIMIT` | `8g` / `4` | Giới hạn tài nguyên container extractor |

### 4.4 SLM

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `ENABLE_SLM_GATE` | `0` | `1` để bật SLM |
| `SLM_BASE_URL` | `http://host.docker.internal:11434/v1` | Endpoint tương thích OpenAI. `http://ollama:11434/v1` = dùng container đóng gói |
| `SLM_MODEL` | `qwen2.5:1.5b` | Tên model |
| `SLM_TIMEOUT_S` | `4` | Quá thời gian thì SLM không thêm gì (các rule khác giữ nguyên) |
| `SLM_MAX_WINDOWS` | `3` | Số đoạn tối đa gửi cho SLM mỗi văn bản |
| `SLM_MAX_ADDED_SPANS` | `8` | Số phát hiện tối đa SLM được thêm |

### 4.5 Copilot và image

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `COPILOT_HOSTS` | `*.githubcopilot.com`, `copilot-proxy.githubusercontent.com` (regex) | Host được giải mã. **Không** thêm host đăng nhập GitHub |
| `PRESIDIO_ANALYZER_IMAGE`, `PRESIDIO_ANONYMIZER_IMAGE` | `ghcr.io/data-privacy-stack/presidio-*:2.2.362` | Image Presidio |
| `MITMPROXY_IMAGE` | `mitmproxy/mitmproxy:12.2.1` | Image proxy Copilot |
| `OLLAMA_IMAGE` | `ollama/ollama:0.12.6` | Image SLM đóng gói |
| `TORCH_VERSION` | `2.14.1+cpu` | PyTorch cho extractor |

---

## 5. Chính sách phát hiện và cách tinh chỉnh

### 5.1 Các loại entity

| Entity | Bộ phát hiện | Điểm | Khi nào bị che với ngưỡng 0.75 |
| --- | --- | --- | --- |
| `VN_PHONE` | Regex di động VN: 10 số, đầu số 03x/05x/07x/08x/09x, chấp nhận `+84`, dấu cách/chấm/gạch | 0.72, tăng khi gần từ khóa | Chỉ khi gần `sđt`, `sdt`, `phone`, `hotline`, `zalo` (mặc định) |
| `VN_CCCD` | 12 số, bắt đầu `0`, chữ số thứ 4 từ 0–3 | 0.55, tăng khi gần từ khóa | Chỉ khi gần `cccd`, `cmnd`, `căn cước`, `số định danh` |
| `VN_TAX_ID` | 10 số (có thể thêm `-xxx`) | 0.48, tăng khi gần từ khóa | Chỉ khi gần `mst`, `mã số thuế`, `tax id` |
| `EMAIL_ADDRESS` | Presidio | ~1.0 | Luôn |
| `CREDIT_CARD` | Presidio, có kiểm tra Luhn | ~1.0 | Khi qua Luhn |
| `PHONE_NUMBER` | Presidio (định dạng quốc tế) | thường 0.4 | Hiếm khi; dùng `VN_PHONE` cho số VN |
| `INTERNAL_TERM` | Danh sách `INTERNAL_DENY_LIST` (+ SLM) | 1.0 | Luôn |
| `SECRET` | **Chỉ SLM** | 0.76 | Khi bật SLM → chặn request |
| `ADDRESS`, `BANK_ACCOUNT` | **Chỉ SLM** | 0.76 | Khi bật SLM |
| `PERSON`, `DATE_TIME`, `LOCATION`… | spaCy (tiếng Anh) | | Không nằm trong chính sách: che nhầm định danh trong code, và kém với tên tiếng Việt |

Từ khóa ngữ cảnh được so khớp theo **từng chữ**. Cụm hai chữ như "điện thoại" hay "căn cước" không làm tăng điểm; các từ một chữ như "sđt", "cccd" thì có.

### 5.2 Xem analyzer quyết định thế nào

```bash
./scripts/analyze.sh "Gọi 0912345678 hoặc sđt 0987654321, CCCD 079204859403"
```

```
threshold=0.75  (MASK/BLOCK = what the Claude Code proxy does)
ignore (below threshold)   VN_PHONE        0.72  '0912345678'
ignore (not in policy)     DATE_TIME       0.85  '0987654321'
MASK                       VN_PHONE        1.00  '0987654321'
MASK                       VN_CCCD         0.90  '079204859403'
```

Cột đầu là đúng hành vi của `claude-proxy`: `MASK`, `BLOCK`, hoặc lý do bỏ qua (`not in policy`, `below threshold`, `overlap lost`). Dùng script này mỗi khi đổi chính sách.

### 5.3 Tinh chỉnh thường gặp

| Muốn | Đặt trong `.env` | Đánh đổi |
| --- | --- | --- |
| Che mọi SĐT di động VN, kể cả không có từ khóa | `VN_PHONE_SCORE=0.8` | Có thể che nhầm dãy 10 số trong code/log |
| Che mọi số dạng CCCD | `VN_CCCD_SCORE=0.8` | Che nhầm nhiều ID 12 số trong code |
| Thêm tên dự án, khách hàng, hostname nội bộ | `INTERNAL_DENY_LIST=corp.local,du-an-x,khach-hang-y` | Cập nhật danh sách là việc vận hành thường xuyên |
| Không che một chuỗi cụ thể (ví dụ email hỗ trợ công khai) | `ALLOW_LIST=noreply@anthropic.com,support@company.vn` | |
| Chặn thay vì che số thẻ | Chuyển `CREDIT_CARD` từ `MASK_ENTITIES` sang `BLOCK_ENTITIES` | Chặn nhầm khi code chứa số thẻ test |
| Che rộng hơn | `SCORE_THRESHOLD=0.6` | Nhiều phát hiện sai hơn, code dễ bị hỏng |

Sau mỗi thay đổi: `./scripts/up.sh`, rồi kiểm tra lại bằng `./scripts/analyze.sh`.

### 5.4 Chuỗi không được che

Claude Code tự chèn một số nội dung vào request. Nếu bị sửa, Anthropic có thể treo hoặc trả `429` cho request đăng nhập bằng subscription. Nội dung đã biết:

- `noreply@anthropic.com` trong dòng `Co-Authored-By: Claude …`, được chèn khi thư mục làm việc là repo git.

Giữ chuỗi này trong `ALLOW_LIST`. Nếu thấy Claude Code treo **chỉ** trong một số thư mục, xem mục 13.

### 5.5 Chồng lấn giữa các phát hiện

Analyzer trả về mọi phát hiện, kể cả chồng nhau. `claude-proxy` lọc theo chính sách **trước**, rồi mới giữ một phát hiện cho mỗi vùng chồng lấn, theo thứ tự ưu tiên: entity BLOCK, điểm cao hơn, đoạn dài hơn. Nhờ vậy một nhận diện nhầm không nằm trong chính sách (ví dụ spaCy coi số điện thoại là `DATE_TIME`) không thể làm mất một `VN_PHONE`.

---

## 6. Claude Code

### 6.1 Nguyên lý

Theo docs Claude Code ("Other LLM gateways", mục *Subscriptions and gateways*): nếu chỉ đặt `ANTHROPIC_BASE_URL` mà **không** đặt biến credential của gateway, request đi qua gateway nhưng credential vẫn là login claude.ai, nên giới hạn sử dụng và chi phí tính theo gói của dev. Vì vậy:

- **Không** đặt `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` hay `apiKeyHelper` trên máy dev. Đặt một trong ba là Claude Code bỏ login subscription.
- Khóa của gateway đi qua header riêng: `ANTHROPIC_CUSTOM_HEADERS="x-pii-guard-key: Bearer <khóa>"`. Proxy dùng header này để xác thực rồi bỏ nó đi, không gửi lên Anthropic.

### 6.2 Cấu hình trên máy dev

**Cách 1: biến môi trường** (thử nhanh, chỉ có hiệu lực trong terminal đó)

```bash
unset ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN
export ANTHROPIC_BASE_URL=http://localhost:4000              # máy chủ chung: https://pii-guard.corp.example
export ANTHROPIC_CUSTOM_HEADERS="x-pii-guard-key: Bearer sk-local-dev"
claude
```

**Cách 2: `~/.claude/settings.json`** (lâu dài, áp dụng cho mọi phiên của user, kể cả extension VS Code)

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "http://localhost:4000",
    "ANTHROPIC_CUSTOM_HEADERS": "x-pii-guard-key: Bearer sk-local-dev"
  }
}
```

**Cách 3: managed settings** cho cả tổ chức (mục 6.4).

> Biến `export` trong terminal **không** áp dụng cho extension Claude Code trong VS Code. Muốn extension cũng đi qua guard, dùng cách 2 hoặc 3.

### 6.3 Đăng nhập và kiểm tra

1. Chạy `claude`. Nếu chưa đăng nhập: `/login` → **Claude account with subscription** → đăng nhập bằng trình duyệt. Bước đăng nhập đi thẳng tới Anthropic, không qua guard.
2. Gõ `/status` và kiểm tra:
   - phương thức đăng nhập là **tài khoản Claude**, không phải API key;
   - "Anthropic base URL" là địa chỉ PII Guard.
3. Kiểm tra PII đã bị che. Proxy khôi phục giá trị thật trong câu trả lời, nên hỏi bình thường bạn sẽ thấy lại số gốc. Để thấy đúng thứ model nhận được, hãy nhờ model "phá" placeholder:

   ```
   SĐT của tôi là 0912345678, email an@example.com.
   Chép lại đúng đoạn bạn nhận được, nhưng thay mọi ký tự < bằng [
   ```

   Thấy dạng `[VN_PHONE_3>` và `[EMAIL_ADDRESS_4>` là guard đang hoạt động (số thứ tự tùy request, vì ngữ cảnh Claude Code tự chèn cũng được đánh số).
4. Xem log phía máy chủ: `docker compose logs -f claude-proxy`. Mỗi request có dòng `action=mask {...}` hoặc `action=pass` (mục 12.2).

### 6.4 Triển khai cho tổ chức bằng managed settings

Managed settings có độ ưu tiên cao nhất và dev không ghi đè được. Đường dẫn: `/etc/claude-code/managed-settings.json` (Linux), `/Library/Application Support/ClaudeCode/managed-settings.json` (macOS); Windows phân phối qua MDM/registry theo docs Claude Code. Phân phối file bằng công cụ quản lý thiết bị (MDM, Ansible…).

```json
{
  "env": {
    "ANTHROPIC_BASE_URL": "https://pii-guard.corp.example",
    "ANTHROPIC_CUSTOM_HEADERS": "x-pii-guard-key: Bearer <khóa của tổ chức>",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "forceLoginMethod": "claudeai",
  "forceLoginOrgUUID": "<UUID tổ chức Claude Team/Enterprise>",
  "allowedProviders": ["customEndpoint"],
  "permissions": {
    "deny": [
      "Read(./.env)", "Read(./.env.*)", "Read(**/*.pem)", "Read(**/*.key)",
      "Read(~/.ssh/**)", "Read(~/.aws/**)"
    ]
  },
  "hooks": {
    "PostToolUse": [
      { "matcher": "Edit|Write|MultiEdit|NotebookEdit",
        "hooks": [ { "type": "command", "command": "/opt/pii-guard/hooks/pii-placeholder-check.sh" } ] }
    ]
  }
}
```

| Khóa | Tác dụng |
| --- | --- |
| `env.ANTHROPIC_BASE_URL` | Mọi phiên đi qua PII Guard |
| `env.ANTHROPIC_CUSTOM_HEADERS` | Khóa gateway. Dev đọc được file này, nên coi đây là khóa theo tổ chức/thiết bị để xác thực và thu hồi, không phải bí mật với dev |
| `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` | Giảm traffic phụ (telemetry, cập nhật) |
| `forceLoginMethod`, `forceLoginOrgUUID` | Ép đăng nhập bằng tài khoản Claude thuộc tổ chức, không dùng gói cá nhân **\[Cần xác nhận\]** trên phiên bản triển khai |
| `allowedProviders: ["customEndpoint"]` | Claude Code ≥ 2.1.285 từ chối phiên trỏ đi nơi khác, chỉ chấp nhận đúng `ANTHROPIC_BASE_URL` trong file này **\[Cần xác nhận\]** khi kết hợp với login subscription |
| `permissions.deny` | Lớp phòng thủ thứ hai: không cho đọc file bí mật |
| `hooks` | Cảnh báo khi placeholder bị ghi xuống file (mục 6.5) |

Đặt hook ở thư mục do IT quản lý (ví dụ copy `hooks/` sang `/opt/pii-guard/hooks/`), không đặt trong repo của dev.

### 6.5 Hook cảnh báo placeholder bị ghi xuống file

Nếu model viết `<VN_PHONE_1>` vào file (do proxy khôi phục không được, hoặc model tự sửa định dạng placeholder), hook `hooks/pii-placeholder-check.sh` sẽ phát hiện.

- Cài cho user hiện tại: `./scripts/install-hook.sh` (thêm vào `~/.claude/settings.json`, có backup). Gỡ: `./scripts/install-hook.sh --remove`.
- Mặc định **cảnh báo**: ghi vào `logs/hook-violations.log`, in một dòng. Đặt `PII_HOOK_BLOCK=1` để **chặn** (exit 2, lỗi trả lại cho model).
- Cần `jq` trên máy dev.

### 6.6 Lưu ý riêng cho Claude Code

- **Auto mode**: qua guard có thể bị treo giữa chừng (mục 14). Khuyến nghị dùng chế độ thường hoặc accept-edits (Shift+Tab) khi đi qua guard.
- **Ứng dụng Claude desktop** không đi theo cơ chế này.
- Bước kiểm tra fast mode và kiểm tra an toàn domain của WebFetch gọi thẳng `api.anthropic.com`, không qua guard (theo docs Claude Code). Các request này không chứa nội dung hội thoại.
- Sau mỗi lần nâng Claude Code, chạy lại `scripts/test_claude_subscription.sh` (mục 11.2).

---

## 7. GitHub Copilot

### 7.1 Nguyên lý

Copilot không có biến kiểu `ANTHROPIC_BASE_URL`, nhưng hỗ trợ HTTP proxy và đọc chứng chỉ tùy chỉnh từ kho tin cậy của hệ điều hành (docs GitHub: *Configuring network settings*). `copilot-proxy` là một forward proxy giải mã TLS **chỉ** cho host inference của Copilot. Đăng nhập GitHub đi xuyên qua mà không bị giải mã. Dev vẫn dùng license Copilot của mình.

### 7.2 Bước 1: tin CA của proxy

Lần khởi động đầu, proxy sinh CA trong `copilot_proxy/ca/` (đã nằm trong `.gitignore`): `mitmproxy-ca-cert.pem` (PEM), `mitmproxy-ca-cert.cer` (cho Windows), cùng khóa riêng `mitmproxy-ca.pem` (**giữ bí mật**).

Copy **chỉ file chứng chỉ** (không copy khóa) tới máy dev và cài vào kho tin cậy:

```bash
# Linux / WSL
sudo cp mitmproxy-ca-cert.pem /usr/local/share/ca-certificates/pii-guard.crt
sudo update-ca-certificates

# macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain mitmproxy-ca-cert.pem
```

```powershell
# Windows (PowerShell chạy quyền admin), hoặc phân phối bằng GPO/Intune
certutil -addstore -f Root mitmproxy-ca-cert.cer
```

> VS Code chạy trên Windows trong khi stack chạy trong WSL: cài CA vào **Windows**, dùng `http://localhost:8080` (WSL2 chuyển tiếp localhost).

Cảnh báo: CA này cho phép giải mã traffic HTTPS của máy đã tin nó. Bảo vệ khóa riêng như bí mật cấp cao. Production nên dùng CA trung gian của tổ chức (mục 10.4).

### 7.3 Bước 2: trỏ editor vào proxy

URL proxy phải là `http://` (Copilot không hỗ trợ proxy `https://`).

| Client | Cách cấu hình |
| --- | --- |
| **VS Code** | Settings → **Http: Proxy** = `http://<máy chủ>:8080`, giữ **Http: Proxy Strict SSL** bật. Hoặc trong `settings.json`: `{ "http.proxy": "http://<máy chủ>:8080", "http.proxyStrictSSL": true }` |
| **JetBrains** | Settings → Appearance & Behavior → System Settings → HTTP Proxy → Manual proxy configuration → HTTP, host và cổng `8080` |
| **Visual Studio** | Copilot đọc cấu hình proxy của Windows (Settings → Network & Internet → Proxy) |
| **Copilot CLI** | `export HTTPS_PROXY=http://<máy chủ>:8080` và `export NODE_EXTRA_CA_CERTS=/đường/dẫn/mitmproxy-ca-cert.pem` |

Khởi động lại editor sau khi cài CA và đặt proxy.

### 7.4 Bước 3: kiểm tra

1. Đăng nhập GitHub trong editor như bình thường.
2. Mở Copilot Chat, gửi một câu có email giả (`liên hệ an@example.com`). Gõ vài dòng code để nhận gợi ý inline.
3. Trên máy chủ:

   ```bash
   docker compose logs -f copilot-proxy | grep copilot-pii
   # copilot-pii host=api.githubcopilot.com path=/chat/completions action=mask entities={'EMAIL_ADDRESS': 1}
   ```

4. Kiểm tra từ máy dev rằng chỉ host Copilot bị giải mã:

   ```bash
   curl -sv -o /dev/null -x http://<máy chủ>:8080 https://github.com/login 2>&1 | grep issuer
   # issuer phải là CA thật của GitHub (ví dụ Sectigo), KHÔNG phải mitmproxy
   ```

Gỡ trên máy dev: xóa cấu hình proxy trong editor, gỡ CA khỏi kho tin cậy.

### 7.5 Ép buộc bằng mạng

Muốn chắc Copilot chỉ đi qua guard: trên tường lửa của mạng dev, **chặn** đường ra trực tiếp tới `*.githubcopilot.com` và `copilot-proxy.githubusercontent.com`, chỉ cho phép máy chủ PII Guard ra các host này. Các domain đăng nhập và quản lý của GitHub vẫn để mở. Lấy danh sách domain hiện hành từ trang **Copilot allowlist reference** của GitHub tại thời điểm triển khai.

### 7.6 Phạm vi bao phủ

| Bề mặt | Qua guard? |
| --- | --- |
| Copilot Chat, Agent, Edit trong VS Code/JetBrains | Có |
| Gợi ý inline, next edit suggestions | Có |
| Copilot CLI | Có, khi đặt `HTTPS_PROXY` và CA |
| Copilot coding agent (chạy trên hạ tầng GitHub) | Không |
| Ảnh đính kèm trong Copilot Chat | Không được quét (mục 14) |

---

## 8. File, PDF và OCR

### 8.1 Vấn đề

Claude Code gửi file dưới dạng khối nội dung: ảnh dán vào hoặc ảnh mở bằng tool Read là khối `image`; PDF mở bằng Read là khối `document` (thường nằm trong `tool_result`). Presidio chỉ quét được văn bản, nên `claude-proxy` xử lý file **trước** khi che chữ, theo `FILE_POLICY`.

### 8.2 Ba chính sách

| `FILE_POLICY` | Hành vi | Khi nào dùng |
| --- | --- | --- |
| `block` (mặc định) | Thay mỗi ảnh/PDF bằng dòng `[PII guard: image removed before sending (FILE_POLICY=block)]`. File không bao giờ được gửi | Mặc định an toàn; không cần thêm tài nguyên |
| `extract` | Gửi file tới `extractor`, thay bằng văn bản trích được (sau đó văn bản này được che PII như mọi văn bản khác). Trích lỗi thì thay bằng thông báo, file không được gửi (fail-closed) | Dev cần Claude đọc PDF |
| `pass` | Gửi file nguyên vẹn, **không quét** | Không khuyến nghị |

File được **thay thế** chứ không trả lỗi, vì file nằm lại trong lịch sử hội thoại: trả lỗi sẽ chặn mọi lượt sau của phiên đó. Tài liệu dạng văn bản thuần (`source.type = text`) được che như văn bản thường ở mọi chính sách. Mỗi file chỉ được trích một lần; các lượt sau dùng kết quả cache theo SHA-256 của nội dung.

### 8.3 Bật trích PDF

```bash
# trong .env
FILE_POLICY=extract
EXTRACT_OCR=off
```

```bash
./scripts/up.sh          # lần đầu build image extractor (PyTorch CPU + marker-pdf, vài phút)
docker compose --profile ocr ps extractor     # healthy
```

Kiểm tra: mở một PDF có text layer bằng Claude Code (`Đọc file hop-dong.pdf và tóm tắt`). Log proxy có `files {'document:extracted': 1}`. Lần trích đầu mất khoảng 20–30 giây mỗi file.

Đổi về `FILE_POLICY=block` rồi chạy `./scripts/up.sh`: `extractor` tự dừng.

### 8.4 Định dạng hỗ trợ

| Định dạng | `extract` với `EXTRACT_OCR=off` |
| --- | --- |
| PDF có text layer | Trích được |
| PDF scan (chỉ là ảnh) | Không trích được chữ → file bị loại kèm thông báo |
| Ảnh PNG/JPEG | Bị loại (`blocked_unsupported`) |
| GIF/WebP | Bị loại |
| Office (DOCX/XLSX/PPTX) | Không hỗ trợ trong bản build này (cần `marker-pdf[full]`) → bị loại |
| PDF mã hóa, hỏng, quá kích thước hoặc quá số trang | Bị loại (`blocked_encrypted`, `blocked_malformed`, `blocked_limits`) |

Extractor nhận diện loại file bằng magic bytes (không tin phần mở rộng hay `media_type`), chặn zip bomb và archive lồng nhau.

### 8.5 Sandbox của extractor

Container `extractor` chạy user không đặc quyền, filesystem chỉ đọc, `tmpfs` cho thư mục tạm, bỏ mọi capability, `no-new-privileges`, giới hạn PID/RAM/CPU. Chạy offline (`HF_HUB_OFFLINE=1`) và không cần Internet lúc chạy. Production nên đặt nó trong một mạng Docker `internal` không có đường ra.

**Không bao giờ** bật cờ `--use_llm` của marker: cờ này gửi nội dung chưa che tới một LLM đám mây.

**Giấy phép**: mã nguồn marker theo Apache 2.0; **trọng số mô hình** theo giấy phép riêng của Datalab (miễn phí dưới một ngưỡng doanh thu/vốn, vượt ngưỡng cần license thương mại). Pháp chế của tổ chức cần xác nhận trước khi dùng.

### 8.6 OCR (ảnh, bản scan)

Image `extractor` hiện chỉ đọc text layer. Để OCR ảnh và bản scan, marker 2.x cần một inference server cho mô hình thị giác (surya) và trọng số tải trước. Bản build này **không có** các thành phần đó. Đặt `EXTRACT_OCR=auto|force` mà thiếu chúng thì extractor sẽ báo lỗi, và file bị loại (fail-closed).

Nếu tổ chức cần OCR, các lựa chọn cắm được vào cùng hợp đồng `POST /extract` (nhận bytes, trả `{status, segments: [{text, page, bbox}]}`):

| Công cụ | Đặc điểm | Giấy phép |
| --- | --- | --- |
| marker-pdf + surya inference server | Giữ bố cục; nên có GPU | Xem 8.5 |
| Tesseract (`tesseract-ocr-vie`) | Nhẹ, chạy CPU; không phân tích bố cục | Apache 2.0 |
| PaddleOCR | Chính xác tốt với ảnh chụp; phụ thuộc nặng | Apache 2.0 |
| docling | Nhiều định dạng, giữ bảng; engine OCR thay được | MIT |

Với OCR, cần chuẩn hóa ký tự hay bị nhầm (`O↔0`, `l↔1`, `B↔8`) trước khi chạy regex, và đo recall PII sau OCR trên tài liệu tiếng Việt của tổ chức trước khi bật.

---

## 9. Tầng tùy chọn: SLM, NER, ML cổ điển

| Tầng | Có trong repo | Tác dụng |
| --- | --- | --- |
| SLM second opinion | **Có**, tắt mặc định | Thêm `SECRET`, `ADDRESS`, `BANK_ACCOUNT`, `INTERNAL_TERM`, `PERSON` ở vùng nghi vấn |
| NER chuyên tiếng Việt (GLiNER, PhoBERT) | Không | Tên người, tổ chức tiếng Việt |
| ML cổ điển (CRF tự huấn luyện) | Không | Entity đặc thù của tổ chức |

Tổ chức chọn tầng phù hợp. Tầng nào bật cũng nên đo trên dữ liệu thật của tổ chức trước khi đưa vào dùng chung.

### 9.1 SLM: nguyên tắc

- SLM **chỉ được thêm** phát hiện, không bao giờ gỡ hay hạ một phát hiện của rule. Lý do: SLM đọc nội dung do người khác kiểm soát nên có thể bị prompt injection.
- Kiểm soát trong `analyzer_plus/slm_gate.py`: văn bản đặt trong thẻ `<doc>` và được coi là dữ liệu; đầu ra bắt buộc JSON theo schema, nhiệt độ 0; nhãn chỉ nằm trong danh sách cho phép; mỗi span phải là **chuỗi con nguyên văn** của văn bản gửi đi; có trần số span (`SLM_MAX_ADDED_SPANS`); lỗi, rác hoặc timeout thì không thêm gì.
- Chỉ gửi tối đa `SLM_MAX_WINDOWS` đoạn ngắn (~560 ký tự) quanh từ khóa gợi ý (mật khẩu, token, api key, khách hàng, hợp đồng, dự án, tài khoản, địa chỉ…) hoặc quanh phát hiện có điểm 0.35–0.75.
- **Cần GPU**. Model 1.5B chạy CPU mất khoảng 25 giây mỗi lần gọi, vượt xa `SLM_TIMEOUT_S`, nên trên thực tế SLM sẽ không thêm được gì. Trên GPU 4 GB, mỗi lần gọi khoảng 0,3–0,6 giây.
- `SECRET` hiện **chỉ** đến từ SLM: không bật SLM thì `BLOCK_ENTITIES=SECRET` không có tác dụng.

### 9.2 Cách A: Ollama trên máy chủ (dùng GPU sẵn có)

1. Cài Ollama trên máy chủ theo hướng dẫn của ollama.com, tải model:

   ```bash
   ollama pull qwen2.5:1.5b
   ```

2. Cho Ollama nghe trên mọi địa chỉ (để container gọi được) và giữ model luôn nạp sẵn:

   ```bash
   sudo systemctl edit ollama
   # thêm:
   # [Service]
   # Environment="OLLAMA_HOST=0.0.0.0:11434"
   # Environment="OLLAMA_KEEP_ALIVE=-1"
   sudo systemctl restart ollama
   ```

   Chặn cổng 11434 từ bên ngoài máy chủ bằng tường lửa. Nếu không đặt `OLLAMA_KEEP_ALIVE=-1`, Ollama giải phóng model sau 5 phút không dùng, và lần gọi kế tiếp sẽ chạm timeout (SLM bỏ qua lần đó).

3. Trong `.env`:

   ```bash
   ENABLE_SLM_GATE=1
   SLM_BASE_URL=http://host.docker.internal:11434/v1
   SLM_MODEL=qwen2.5:1.5b
   ```

4. `./scripts/up.sh`. Dòng `SLM gate : on (...)` phải xuất hiện.

### 9.3 Cách B: Ollama đóng gói trong stack

```bash
ENABLE_SLM_GATE=1
SLM_BASE_URL=http://ollama:11434/v1
SLM_MODEL=qwen2.5:1.5b
```

`./scripts/up.sh` tự bật service `ollama` và tải model ở lần chạy đầu (khoảng 1 GB, lưu trong volume `pii-guard_ollama-models`). Container chỉ dùng được GPU khi máy chủ có **NVIDIA Container Toolkit**; khi đó thêm vào service `ollama` trong `docker-compose.yml` (cần Compose ≥ 2.30):

```yaml
    gpus: all
```

Không có GPU thì dùng cách A với một máy có GPU, hoặc để SLM tắt.

### 9.4 Kiểm tra SLM

```bash
docker compose exec presidio-analyzer python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:3000/health').read())"
# {"status":"ok","slm_enabled":true}
./scripts/analyze.sh "Thông tin đăng nhập: mật khẩu tài khoản quản trị là Hoangsa@2026"
# BLOCK  SECRET  0.76  '...'
```

Gọi lần đầu sau khi khởi động có thể chậm hơn vì model đang nạp; gọi lại để đo.

**Độ trễ**: SLM chạy đồng bộ trong mỗi lần phân tích, nên mỗi văn bản mới cộng thêm tối đa `SLM_MAX_WINDOWS × SLM_TIMEOUT_S`. Claude Code gửi lại cả lịch sử mỗi lượt, nhưng `claude-proxy` cache kết quả theo nội dung, nên chỉ văn bản mới phải chờ SLM.

### 9.5 Thay máy chủ SLM

`slm_gate.py` gọi `POST {SLM_BASE_URL}/chat/completions` với `response_format` kiểu `json_schema`. Mọi máy chủ tương thích OpenAI có hỗ trợ structured output (vLLM, llama.cpp server) đều dùng được, chỉ cần đổi `SLM_BASE_URL` và `SLM_MODEL`. Chọn model nhỏ (1–4B), hỗ trợ tiếng Việt, giấy phép cho phép dùng thương mại.

### 9.6 Thêm NER hoặc ML cổ điển

Không có sẵn. Nếu tổ chức cần, hướng tích hợp là thêm một bộ nhận diện vào `analyzer_plus/app.py`: sau khi gọi Presidio core, trả thêm các phát hiện cùng định dạng `{entity_type, start, end, score}`, rồi đưa entity mới vào `MASK_ENTITIES`. Chạy model offline, ghim phiên bản/hash, và chỉ đưa vào dùng chung sau khi đo precision/recall theo từng entity trên dữ liệu của tổ chức.

---

## 10. Triển khai trên máy chủ cho cả tổ chức

### 10.1 Chuẩn bị máy chủ

1. Linux LTS, Docker Engine + Compose v2, đồng bộ giờ (NTP).
2. Tạo user vận hành riêng; chỉ cho SSH từ mạng quản trị.
3. Đặt repo tại `/opt/pii-guard`, `.env` quyền `600`:

   ```bash
   sudo mkdir -p /opt/pii-guard && sudo chown "$USER" /opt/pii-guard
   cp -r ~/code/pii-guard/. /opt/pii-guard/ && cd /opt/pii-guard
   cp .env.example .env && chmod 600 .env
   sed -i "s/^PII_GUARD_KEY=.*/PII_GUARD_KEY=sk-$(openssl rand -hex 32)/" .env
   ```

4. Chỉnh `.env` (chính sách, `INTERNAL_DENY_LIST`, `FILE_POLICY`, SLM), rồi `./scripts/up.sh`.

### 10.2 HTTPS cho Claude Code (edge TLS)

Dev ở máy khác nên truy cập `claude-proxy` qua HTTPS. Ví dụ với Caddy chạy trên cùng máy chủ (cài từ gói của hệ điều hành), file `/etc/caddy/Caddyfile`:

```caddyfile
pii-guard.corp.example {
    tls /etc/caddy/certs/server.crt /etc/caddy/certs/server.key

    # Chỉ các path Claude Code cần
    @claude path /v1/messages /v1/messages/count_tokens
    handle @claude {
        reverse_proxy 127.0.0.1:4000 {
            flush_interval -1                       # không buffer stream SSE
            transport http {
                response_header_timeout 10m
            }
        }
    }
    handle {
        respond 404
    }
}
```

Sau đó:

- Ràng buộc `claude-proxy` chỉ nghe trên localhost: trong `docker-compose.yml` đổi `ports: ["4000:4000"]` thành `ports: ["127.0.0.1:4000:4000"]`.
- Dev đặt `ANTHROPIC_BASE_URL=https://pii-guard.corp.example`.
- Nếu dùng CA nội bộ cho chứng chỉ máy chủ, máy dev cần tin CA đó (Claude Code là ứng dụng Node.js: thêm vào kho tin cậy của hệ điều hành, hoặc `NODE_EXTRA_CA_CERTS`).
- Không bật WAF kiểm tra body trên đường này: prompt chứa mã nguồn và thẻ kiểu XML dễ bị rule XSS chặn nhầm.

### 10.3 Proxy cho Copilot

Copilot chỉ hỗ trợ proxy `http://`, nên `copilot-proxy :8080` được mở trực tiếp cho mạng dev:

- Trên tường lửa máy chủ, chỉ cho dải IP của mạng dev vào cổng 8080.
- Không mở 8080 ra Internet.

### 10.4 CA cho Copilot ở production

Thay CA tự sinh bằng một CA trung gian của tổ chức:

1. Phát hành một CA trung gian dành riêng cho PII Guard. Nếu PKI hỗ trợ, giới hạn bằng *name constraints* chỉ cho `githubcopilot.com` và `copilot-proxy.githubusercontent.com` **\[Cần xác nhận\]** khả năng của PKI.
2. Ghép chứng chỉ và khóa riêng thành một file PEM, đặt tại `copilot_proxy/ca/mitmproxy-ca.pem` (quyền `600`); đặt chứng chỉ (không có khóa) tại `copilot_proxy/ca/mitmproxy-ca-cert.pem`.
3. `docker compose restart copilot-proxy`.
4. Phân phối chứng chỉ trung gian (hoặc root của tổ chức) tới máy dev bằng MDM.

### 10.5 Mạng của máy chủ

| Hướng | Cho phép |
| --- | --- |
| Vào | `443` (edge Claude Code) và `8080` (Copilot) từ mạng dev; SSH từ mạng quản trị |
| Ra | `api.anthropic.com:443`; các host Copilot và GitHub (mục 2.3); NTP/DNS nội bộ |
| Ra, chỉ lúc cài/cập nhật | Docker registry, PyPI, Hugging Face, ollama.com |

`presidio-analyzer`, `presidio-analyzer-core`, `presidio-anonymizer` và `extractor` không cần ra Internet lúc chạy.

### 10.6 Bảo vệ máy chủ

Máy chủ PII Guard thấy toàn bộ nội dung chưa che, OAuth token Claude và token Copilot của dev, nên phải được bảo vệ như một tài sản trọng yếu:

- Không cho dev đăng nhập vào máy chủ; tách mạng quản trị.
- Bảo vệ `.env` và `copilot_proxy/ca/mitmproxy-ca.pem`.
- Vá hệ điều hành, Docker và image định kỳ.
- Log của guard không chứa nội dung prompt (mục 12.2). Không bật log debug của mitmproxy hay uvicorn ở production.

### 10.7 Chạy nhiều node

`claude-proxy` và `copilot-proxy` không giữ trạng thái giữa các request (cache chỉ để tăng tốc), nên có thể chạy nhiều node giống hệt nhau sau một load balancer:

- Claude Code: load balancer HTTP(S), health check `GET /__health` trên cổng 4000.
- Copilot: load balancer TCP cho cổng 8080; mọi node dùng **cùng** CA.
- Nâng cấp cuốn chiếu: rút một node khỏi load balancer → `./scripts/up.sh` → chạy kiểm thử (mục 11) → trả lại → lặp với node tiếp theo.

---

## 11. Kiểm thử nghiệm thu

### 11.1 Unit test

```bash
./scripts/test.sh
```

Kiểm tra analyzer (rule, SLM gate, allow list), che PII và khôi phục placeholder của `claude-proxy`, xử lý file, proxy Copilot và extractor. Không cần Docker hay mạng.

### 11.2 Kiểm thử Claude Code không tốn quota

```bash
./scripts/up.sh test                    # upstream của Claude Code là mock local
./scripts/test_claude_subscription.sh   # kỳ vọng: toàn bộ PASS (36 dòng)
./scripts/up.sh                         # quay lại chế độ thật
```

Script chạy **Claude Code thật** ở chế độ OAuth với token **giả** và thư mục cấu hình tạm (không đụng login thật), qua hai lượt hội thoại. Nó so sánh request đi thẳng với request qua guard:

- OAuth token được chuyển tiếp, gateway key không lọt lên upstream;
- `anthropic-beta`, user-agent, path và query giống từng byte;
- `tools`, `safeguards`, `thinking`, `output_config`, `context_management`, mảng `system` không đổi;
- PII bị che, placeholder đúng định dạng;
- placeholder bị cắt giữa các mẩu stream được khôi phục, trong cả văn bản lẫn input của tool.

Chạy lại sau **mỗi** lần nâng Claude Code.

### 11.3 Checklist thủ công

**Claude Code**
- [ ] `/status` hiện tài khoản Claude và base URL của guard
- [ ] Prompt "thay `<` bằng `[`" (mục 6.3) cho thấy placeholder
- [ ] Trong repo git, prompt đơn giản trả lời bình thường (không treo)
- [ ] Prompt có mật khẩu bị chặn khi bật SLM (`Blocked by PII guard: SECRET`)
- [ ] Không có `x-pii-guard-key` hoặc khóa sai → `401`
- [ ] Dừng analyzer (`docker compose stop presidio-analyzer`) → Claude Code báo lỗi, log có `action=fail-closed`; bật lại (`docker compose start presidio-analyzer`)

**Copilot**
- [ ] Chat và gợi ý inline chạy; log có `action=mask`
- [ ] `github.com` qua proxy giữ chứng chỉ gốc (mục 7.4)
- [ ] Dừng analyzer → Copilot báo lỗi, không gửi bản gốc

**File**
- [ ] `FILE_POLICY=block`: ảnh/PDF bị thay bằng thông báo (log `files {'image:blocked': 1}`)
- [ ] `FILE_POLICY=extract`: PDF có text layer được trích và che (log `document:extracted`)

**Vận hành**
- [ ] Log không chứa nội dung prompt
- [ ] Máy dev không ra được `*.githubcopilot.com` trực tiếp (nếu áp dụng mục 7.5)

---

## 12. Vận hành

### 12.1 Lệnh thường dùng

| Việc | Lệnh |
| --- | --- |
| Khởi động / áp dụng `.env` | `./scripts/up.sh` |
| Dừng | `docker compose down` |
| Trạng thái | `docker compose ps` |
| Log Claude Code | `docker compose logs -f claude-proxy` |
| Log Copilot | `docker compose logs -f copilot-proxy \| grep copilot-pii` |
| Thử chính sách | `./scripts/analyze.sh "văn bản"` |
| Kiểm thử | `./scripts/test.sh`; `./scripts/up.sh test && ./scripts/test_claude_subscription.sh` |

### 12.2 Đọc log

Log chỉ ghi siêu dữ liệu: path, trạng thái, loại và số lượng entity, số byte, thời gian. **Không** ghi nội dung, header hay token.

`claude-proxy`:

```
recv POST /v1/messages bytes=188340
masked /v1/messages in 2361ms
POST /v1/messages status=200 action=mask {'EMAIL_ADDRESS': 1, 'VN_PHONE': 1} files {'image:blocked': 1} ttfb_ms=3724
stream /v1/messages done received=3737 sent=3726 held=0 secs=4.6 events=...
path=/v1/messages action=block entities=SECRET
path=/v1/messages action=fail-closed error=ConnectError
```

| Trường | Ý nghĩa |
| --- | --- |
| `action=pass` | Không có PII, request đi nguyên byte |
| `action=mask {...}` | Số đoạn đã che theo loại; `files {...}` là cách xử lý từng file |
| `status` | Mã trạng thái Anthropic trả về (`429` = giới hạn của gói Claude) |
| `ttfb_ms` | Thời gian tới byte đầu tiên của upstream, gồm cả thời gian che |
| `stream ... done/aborted` | `aborted` = Claude Code hủy hoặc stream bị ngắt; `held` > 0 kéo dài là bất thường |

`copilot-proxy`: `copilot-pii host=… path=… action=mask|pass|block|fail-closed entities={…}`.

Đẩy log về SIEM bằng logging driver của Docker. Nên cảnh báo khi: số `action=block` tăng đột biến, có `fail-closed`, có nhiều `401`.

### 12.3 Định kỳ

| Việc | Tần suất |
| --- | --- |
| Cập nhật `INTERNAL_DENY_LIST` (dự án, khách hàng, hostname mới) | Khi có thay đổi |
| Đổi `PII_GUARD_KEY`: sửa `.env` → `./scripts/up.sh` → cập nhật managed settings trên máy dev | Theo chính sách khóa của tổ chức, và khi nghi lộ |
| Đổi CA của Copilot | Theo hạn chứng chỉ; phân phối CA mới **trước** khi thay trên máy chủ |
| Cập nhật image (Presidio, mitmproxy, Ollama): đổi tag trong `.env` → `./scripts/up.sh` → kiểm thử mục 11 | Hàng tháng / khi có bản vá bảo mật |
| Chạy `test_claude_subscription.sh` | Sau mỗi lần nâng Claude Code |
| Xem lại log `block`/`mask` để phát hiện che nhầm hoặc bỏ sót | Hàng tuần |

### 12.4 Sao lưu

Chỉ cần sao lưu `.env` và `copilot_proxy/ca/` (khóa CA). Guard không lưu dữ liệu người dùng. Volume `ollama-models` có thể tải lại.

---

## 13. Xử lý sự cố

| Hiện tượng | Nguyên nhân | Cách xử lý |
| --- | --- | --- |
| `up.sh`: `port 4000 is used by container(s): …` | Stack khác đang giữ cổng | Dừng stack đó (`docker compose down` trong thư mục của nó) |
| `up.sh`: `.env not found` / `PII_GUARD_KEY is empty` | Chưa tạo `.env` | `cp .env.example .env` |
| Service `unhealthy` lâu ở lần đầu | Presidio core nạp spaCy (30–60 giây) | Chờ; `docker compose logs presidio-analyzer-core` |
| Claude Code `401 PII guard: missing or invalid x-pii-guard-key` | Thiếu hoặc sai khóa trong `ANTHROPIC_CUSTOM_HEADERS` | So với `PII_GUARD_KEY` trong `.env` |
| `/status` hiện API key thay vì tài khoản Claude | Máy dev đặt `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` hoặc `apiKeyHelper` | Gỡ chúng (cả trong `settings.json`) |
| Claude Code không đi qua guard | Biến chỉ đặt trong terminal khác, hoặc dùng extension VS Code | Dùng `~/.claude/settings.json` (mục 6.2) |
| Treo **chỉ** trong một số thư mục (thường là repo git) | Guard che nhầm nội dung Claude Code tự chèn | Kiểm tra `ALLOW_LIST` còn `noreply@anthropic.com`; dùng `./scripts/analyze.sh` để tìm chuỗi bị che |
| Treo hoặc "Simmering" lâu khi bật auto mode | Giới hạn đã biết (mục 14) | Tắt auto mode khi đi qua guard |
| `429 rate_limit_error` | Giới hạn của gói Claude (hay gặp khi nhiều phiên Opus chạy song song) | Chờ, hoặc đổi model bằng `/model` |
| Claude Code báo `PII guard unavailable; request not sent` | Analyzer không phản hồi | `docker compose ps`; `docker compose logs presidio-analyzer` |
| `Blocked by PII guard: SECRET` | Prompt chứa bí mật (SLM phát hiện) | Bỏ bí mật khỏi prompt; nếu che nhầm, kiểm tra bằng `analyze.sh` |
| Số điện thoại không bị che | Không có từ khóa gần đó (mục 5.1) | `VN_PHONE_SCORE=0.8` |
| Claude thấy `[PII guard: image removed …]` | `FILE_POLICY=block` | Đúng thiết kế; dùng `extract` cho PDF |
| `[PII guard: document removed: extractor unavailable …]` | Extractor chưa chạy | `FILE_POLICY=extract ./scripts/up.sh`; chờ extractor healthy |
| Copilot lỗi chứng chỉ | CA chưa được tin, hoặc editor chưa khởi động lại | Mục 7.2; khởi động lại editor |
| Copilot không đăng nhập được | `COPILOT_HOSTS` bị mở rộng sang host GitHub | Bỏ `COPILOT_HOSTS` khỏi `.env` |
| Copilot `503 PII guard unavailable` | Analyzer/anonymizer chưa healthy | `docker compose ps` |
| SLM bật nhưng không phát hiện gì; mỗi lần phân tích chậm đúng `SLM_TIMEOUT_S` | SLM chạy CPU, model đang nạp, hoặc container không gọi được Ollama | Mục 9.2–9.4: GPU, `OLLAMA_HOST=0.0.0.0`, `OLLAMA_KEEP_ALIVE=-1` |

---

## 14. Giới hạn đã biết

1. **Auto mode của Claude Code** có thể bị treo giữa chừng khi đi qua guard (Anthropic ngừng gửi dữ liệu giữa stream), trong khi chế độ thường ổn định. Nguyên nhân chưa xác định. Khuyến nghị tắt auto mode khi dùng guard.
2. **Phát hiện dựa trên rule có bỏ sót.** SĐT và CCCD không có từ khóa ở gần thì không bị che với cấu hình mặc định (mục 5.3). Tên người tiếng Việt, địa chỉ và thông tin nội bộ không có trong danh sách cấm chỉ được bắt khi bật SLM, và cũng không đầy đủ.
3. **Không có rule `SECRET`** ngoài SLM. Không bật SLM thì mật khẩu, khóa API không bị chặn.
4. **Copilot chỉ che một chiều**: model thấy placeholder và có thể viết placeholder vào code gợi ý. Giao thức Copilot không công khai nên có thể hỏng khi GitHub thay đổi; nếu Copilot áp dụng certificate pinning thì đường Copilot ngừng hoạt động. Ảnh trong Copilot Chat không được quét.
5. **File**: chỉ trích được PDF có text layer; ảnh, bản scan và file Office bị loại khi `FILE_POLICY=extract`.
6. **Ngoài phạm vi**: Copilot coding agent (chạy trên hạ tầng GitHub), ứng dụng Claude desktop, và các kênh khác (git push, chat, trình duyệt). Các kênh này cần DLP riêng.
7. **Máy chủ guard thấy toàn bộ nội dung và token** của dev: phải được bảo vệ như tài sản trọng yếu (mục 10.6).
8. Hành vi của Claude Code, Anthropic và Copilot thay đổi theo phiên bản. Mọi mục **\[Cần xác nhận\]** và kiểm thử mục 11 phải chạy lại khi nâng cấp.

---

## 15. Phụ lục

### 15.1 Cấu trúc repo

```
docker-compose.yml           các service; extractor (profile ocr), ollama (profile slm), upstream-recorder (profile test)
.env.example                 mẫu cấu hình
claude_proxy/                reverse proxy cho Claude Code
  app.py                     xác thực, chuyển tiếp nguyên vẹn, log
  mask.py                    che PII trong request, chính sách, gộp chồng lấn
  files.py                   xử lý ảnh/PDF theo FILE_POLICY
  unmask.py                  khôi phục placeholder trong stream SSE và JSON
copilot_proxy/               addon mitmproxy cho Copilot (addon.py, scrub.py); ca/ = CA sinh ra (không commit)
analyzer_plus/               lớp bọc Presidio: rule VN (rules.py), SLM gate và allow list (slm_gate.py)
extractor/                   dịch vụ trích văn bản (marker-pdf), kiểm tra file, sandbox
hooks/                       hook Claude Code cảnh báo placeholder bị ghi xuống file
scripts/
  up.sh                      preflight + khởi động (run | test)
  wait-health.sh             chờ service healthy
  analyze.sh                 xem phát hiện và quyết định chính sách cho một văn bản
  test.sh                    unit test
  test_claude_subscription.sh  kiểm thử Claude Code với upstream giả
  install-hook.sh            cài/gỡ hook cho user hiện tại
  mock-upstream.mjs          upstream giả cho chế độ test
```

### 15.2 Endpoint nội bộ

| Service | Endpoint |
| --- | --- |
| `claude-proxy` | `GET /__health`; mọi path khác chuyển tiếp lên Anthropic (chỉ `/v1/messages` và `/v1/messages/count_tokens` được quét) |
| `presidio-analyzer` | `GET /health` (`slm_enabled`), `POST /analyze` `{"text": "...", "language": "en"}` |
| `extractor` | `GET /health`, `POST /extract` (body là bytes của file) → `{status, kind, segments}` |

### 15.3 Nguồn tham khảo

- Claude Code docs: *Other LLM gateways* (mục *Subscriptions and gateways*), *Claude Code gateway compatibility guide*, *Roll out an LLM gateway for your organization*, *Managed settings* (code.claude.com)
- GitHub docs: *Configuring network settings for GitHub Copilot*, *Copilot allowlist reference*, *Content exclusion*
- Presidio docs (microsoft.github.io/presidio): analyzer, ad-hoc recognizers, anonymizer
- mitmproxy docs: options `allow_hosts`, certificates
- marker-pdf README (github.com/datalab-to/marker): chế độ, giấy phép trọng số
- Ollama docs: biến môi trường `OLLAMA_HOST`, `OLLAMA_KEEP_ALIVE`, endpoint tương thích OpenAI
