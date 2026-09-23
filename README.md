# Text-to-SQL tiếng Việt — Quản lý Hợp đồng Xuất khẩu Lao động (XKLĐ)

Đồ án tốt nghiệp: nền tảng cho phép người dùng gõ câu hỏi bằng **tiếng Việt tự nhiên**, hệ thống tự
sinh câu lệnh SQL, kiểm duyệt, chạy trên PostgreSQL và trả về bảng kết quả, có phát tiến trình xử lý
theo thời gian thực trên giao diện web.

Model sinh SQL là **Qwen2.5-Coder-7B-Instruct** đã fine-tune bằng QLoRA, xuất ra định dạng
**GGUF (Q4_K_M)** và phục vụ hoàn toàn offline qua **Ollama** — không gọi bất kỳ API AI nào trên
mạng (OpenAI, Gemini, OpenRouter...).

**Stack:** React 19 + Vite · FastAPI · PostgreSQL · Ollama.

---

## 1. Kiến trúc & luồng dữ liệu

```
Trình duyệt (App.jsx)
      │  EventSource
      ▼
GET /api/v1/query/stream  (FastAPI, main.py)
      │
      ▼
TextToSqlAgent.run()  — async generator, agent.py
      │
      ├─ database.get_schema_text()   → đọc schema + value-hint từ PostgreSQL
      ├─ retriever.retrieve()         → chọn few-shot phù hợp (TF-IDF + cosine)
      ├─ prompts.build_sql_messages() → dựng prompt
      ├─ ollama_client.stream_chat()  → gọi model qua Ollama, stream token
      ├─ ollama_client.clean_sql()    → cắt lấy câu SQL từ output model
      ├─ security.sanitize()          → kiểm duyệt SQL (lớp phòng vệ 1)
      └─ database.run_select()        → chạy SELECT read-only (lớp phòng vệ 2 ở tầng DB)
      │
      ▼
yield {type: step | token | sql | result | error}  →  SSE  →  giao diện cập nhật trực tiếp
```

**Vòng lặp Self-Healing:** nếu bước kiểm duyệt hoặc chạy SQL lỗi, agent không dừng lại mà nhồi lỗi
vào prompt và thử sinh lại, tối đa `MAX_HEAL_ATTEMPTS` lượt (mặc định 3) trước khi báo lỗi thân thiện
cho người dùng.

**Hai lớp bảo mật** (bất biến, không được nới lỏng khi phát triển thêm):

1. `security.sanitize()` — dựng "skeleton" của câu SQL (che nội dung chuỗi/comment nhưng giữ nguyên
   độ dài), chỉ cho phép bắt đầu bằng `SELECT`/`WITH`, chặn multi-statement và danh sách từ khoá nguy
   hiểm, ép `LIMIT` ở cấp ngoài cùng.
2. Role PostgreSQL `textsql_readonly` — chỉ có quyền `SELECT`, đã thu hồi các hàm đọc file/ngủ/đọc
   bảng hệ thống; `database.run_select()` còn chạy trong transaction `readonly=True` kèm
   `statement_timeout`.

---

## 2. Cấu trúc thư mục

| Thư mục / file | Nội dung |
|---|---|
| `backend/` | API FastAPI, agent, kiểm duyệt SQL, kết nối PostgreSQL, prompt |
| `frontend/` | Giao diện React 19 + Vite (một component `App.jsx`) |
| `tests/` | Test bảo mật tự động, chấm độ chính xác, kịch bản kiểm thử thủ công |
| `models/Modelfile` | Cấu hình đăng ký model fine-tuned vào Ollama |
| `dataset.json` | Tập dữ liệu dùng để fine-tune model |
| `pipeline_training_kaggle.md` | Mô tả pipeline fine-tune (Unsloth + QLoRA, chạy trên Kaggle) |

> **Về `dataset.json`:** đây là dữ liệu **tổng hợp (synthetic)**, mỗi mẫu chỉ gồm mô tả schema (tên
> bảng/cột tự thiết kế), một câu hỏi tiếng Việt và câu SQL/giải thích tương ứng — không chứa bất kỳ bản ghi
> hay thông tin cá nhân thật nào của người lao động/doanh nghiệp. Tên cột như `id_card_number`, `phone`,
> `full_name`... chỉ là thiết kế schema, không phải giá trị dữ liệu thật.

---

## 3. Yêu cầu hệ thống

- Python 3.10+
- Node.js 18+
- PostgreSQL 14+
- [Ollama](https://ollama.com) đã cài đặt, chạy ở `localhost:11434`
- (Khuyến nghị, không bắt buộc) GPU rời ≥ 4GB VRAM — hệ thống có CPU offload nên vẫn chạy được trên
  máy yếu, nhưng mỗi lượt gọi model sẽ mất khoảng 20–60 giây.

---

## 4. Schema cơ sở dữ liệu

Repo không kèm sẵn file khởi tạo bảng — cần tự tạo một database PostgreSQL tên **`do_an`** với 8
bảng theo cấu trúc dưới đây (và tự nạp dữ liệu mẫu phù hợp), sau đó trỏ backend vào database này qua
`.env`. Backend đọc schema trực tiếp từ `information_schema` lúc chạy, không hardcode ở đâu trong mã
nguồn.

```
companies(
  id PK, short_name, company_name, email, license_number, website, phone, address,
  representative, category, created_at, license_date)

officers(
  id PK, position, hire_date, department, phone, created_at, email, full_name)

currencies(
  code PK, name, exchange_rate_to_vnd, updated_at)

supply_contracts(
  id PK, company_id FK->companies.id, officer_id FK->officers.id, created_at,
  document_date, status, valid_from, valid_to, document_number, receiving_country,
  partner_company_address, partner_representative, partner_company_name)

recruitment_orders(
  id PK, contract_id FK->supply_contracts.id, total_quantity, skilled_quantity,
  gender_requirement, female_quantity, job_industry, created_at, safety_policy,
  job_title, contract_duration, age_requirement, deductions, working_hours,
  allowance_and_bonus, insurance_policy, accommodation_policy, recruitment_period,
  work_location, rest_hours)

order_financials(
  order_id PK FK->recruitment_orders.id, currency_code FK->currencies.code,
  partner_training_fee, worker_passport_fee, partner_visa_medical_fee,
  partner_service_fee, worker_training_fee, partner_airfare_policy,
  total_worker_cost, base_salary, worker_service_fee, created_at,
  worker_visa_medical_fee, base_salary_usd)

workers(
  id PK, assigned_order_id FK->recruitment_orders.id, id_card_number, status,
  phone, gender, date_of_birth, full_name, created_at, hometown)

contract_approvals(
  id PK, officer_id FK->officers.id, contract_id FK->supply_contracts.id,
  created_at, decision_type, reason_description, decision_date)
```

**Quan hệ chính:**
- `companies` (1)–(n) `supply_contracts`, qua `supply_contracts.company_id`
- `officers` (1)–(n) `supply_contracts` (quản lý hợp đồng), qua `supply_contracts.officer_id`
- `supply_contracts` (1)–(n) `recruitment_orders`, qua `recruitment_orders.contract_id`
- `recruitment_orders` (1)–(1) `order_financials`, qua `order_financials.order_id`
- `recruitment_orders` (1)–(n) `workers` (lao động được gán), qua `workers.assigned_order_id`
- `currencies` (1)–(n) `order_financials`, qua `order_financials.currency_code`
- `supply_contracts` (1)–(n) `contract_approvals`, qua `contract_approvals.contract_id`
- `officers` (1)–(n) `contract_approvals` (người quyết định), qua `contract_approvals.officer_id`

Sau khi tạo xong 8 bảng và nạp dữ liệu, chạy `backend/setup_readonly_user.sql` trên database này để
tạo role `textsql_readonly` — role mà backend dùng để chạy câu SELECT (lớp bảo mật thứ 2).

---

## 5. Cài đặt & chạy hệ thống

### 5.1. Chuẩn bị model (Ollama)

1. Có sẵn file model đã fine-tune, định dạng `.gguf` (không kèm trong repo vì dung lượng lớn — xem
   `.gitignore`). Đặt file này vào thư mục `models/`, cùng cấp với `Modelfile`.
2. Mở `models/Modelfile`, kiểm tra dòng `FROM ./ten-file-cua-ban.gguf` trỏ đúng tên file `.gguf` vừa
   đặt vào.
3. Đăng ký model vào Ollama:
   ```bash
   ollama create text2sql -f models/Modelfile
   ```
4. Kiểm tra: `ollama list` phải thấy model `text2sql`.

### 5.2. Cơ sở dữ liệu

Tạo database `do_an` theo schema ở mục 4, rồi chạy `backend/setup_readonly_user.sql` để cấp quyền
đọc cho role `textsql_readonly`.

### 5.3. Backend (FastAPI)

```bash
cd backend
pip install -r requirements.txt
```

Tạo file `backend/.env` với nội dung tương tự (điền lại theo môi trường của bạn):

```env
# Ollama
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=text2sql
OLLAMA_KEEP_ALIVE=30m
OLLAMA_NUM_CTX=4096
OLLAMA_TEMPERATURE=0.1
OLLAMA_TIMEOUT_S=120

# Self-healing
MAX_HEAL_ATTEMPTS=3
SQL_TIMEOUT_MS=10000
DEFAULT_LIMIT=500

# CORS
FRONTEND_ORIGIN=http://localhost:5173

# PostgreSQL
DB_HOST=localhost
DB_NAME=do_an
DB_USER=textsql_readonly
DB_PASSWORD=<mật khẩu role readonly>
DB_PORT=5432
```

Chạy backend:

```bash
uvicorn main:app --port 8000
```

> Không thêm `--reload` khi cần phục vụ ổn định (ví dụ lúc chạy `tests/evaluate.py`) — watcher reload
> của uvicorn có thể để sót tiến trình con giữ cổng 8000 nếu bị dừng đột ngột.

Kiểm tra: mở `http://localhost:8000/api/v1/schema` phải thấy JSON danh sách bảng/cột.

### 5.4. Frontend (React + Vite)

```bash
cd frontend
npm install
npm run dev
```

Mở `http://localhost:5173`.

---

## 6. Endpoint chính

| Method | Path | Ghi chú |
|---|---|---|
| GET | `/api/v1/schema` | Danh sách bảng + cột cho sidebar |
| POST | `/api/v1/query` | Sinh + chạy SQL, trả kết quả cuối |
| GET | `/api/v1/query/stream` | Bản SSE (dùng bởi giao diện web) |
| POST | `/api/v1/execute` | Chạy SQL người dùng tự sửa tay, vẫn qua kiểm duyệt |
| POST | `/api/v1/explain` | Giải thích SQL bằng tiếng Việt |

---

## 7. Kiểm thử

| Việc | Lệnh | Ghi chú |
|---|---|---|
| Kiểm duyệt SQL | `python tests/test_security.py` | Không cần backend/Ollama/PostgreSQL, chạy được bất cứ lúc nào, rất nhanh |
| Độ chính xác (Execution Accuracy) | `python tests/evaluate.py` | Cần backend + Ollama + PostgreSQL đang chạy, mất ~15–20 phút |
| Lint frontend | `npm run lint` (trong `frontend/`) | |

Số liệu đánh giá mới nhất nằm ở `tests/report.json` (và lịch sử các lần chạy ở `tests/reports/`) —
xem file này để lấy con số hiện hành thay vì số cũ có thể đã lỗi thời. Tóm tắt độ chính xác (so với model
base), thời gian phản hồi và kết quả kiểm duyệt SQL nằm ở `tests/KIEM_THU.md`.

---

## 8. Hạn chế đã biết

Hạn chế lớn nhất hiện tại: vòng self-healing chỉ bắt lỗi cú pháp/tên cột, chưa bắt được trường hợp SQL
chạy được nhưng trả lời sai ý câu hỏi. Xem thống kê độ chính xác chi tiết theo từng mức độ câu hỏi ở
`tests/KIEM_THU.md`.

---

## License

Phát hành theo giấy phép [MIT](LICENSE).
