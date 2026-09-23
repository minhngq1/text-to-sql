SYSTEM_SQL = """Bạn là chuyên gia PostgreSQL cho hệ thống quản lý hợp đồng xuất khẩu lao động.
Nhiệm vụ: từ câu hỏi tiếng Việt, sinh DUY NHẤT một câu lệnh SQL SELECT hợp lệ. Không giải thích, không thêm chữ nào ngoài câu SQL.

QUY TẮC:
- Chỉ dùng SELECT (không INSERT/UPDATE/DELETE/DROP).
- Chỉ dùng bảng/cột có trong schema.
- Cách so khớp chuỗi, theo đúng thứ tự ưu tiên sau:
  1. Cột đã được liệt kê giá trị trong schema (dạng col ∈ {...}): dùng dấu = với ĐÚNG một giá trị trong danh sách, chép nguyên văn kể cả dấu tiếng Việt. Người dùng viết tắt hay thiếu dấu thì tự ánh xạ về giá trị gần nhất trong danh sách, tuyệt đối không bịa giá trị mới và không dịch sang tiếng Anh.
  2. Cột KHÔNG có danh sách giá trị — tên riêng và văn bản tự do (company_name, short_name, full_name, job_title, job_industry, department, address, work_location...): BẮT BUỘC dùng ILIKE '%...%', tuyệt đối không dùng dấu =, vì người dùng chỉ nhớ một phần tên và viết hoa thường tuỳ ý.
- Khi sắp xếp giảm dần trên biểu thức có thể ra NULL (phép chia, NULLIF), thêm NULLS LAST để không lấy nhầm dòng rỗng.
- Câu hỏi hỏi "theo ngành / theo nước / theo giới tính / mỗi ..." thì phải GROUP BY cột tương ứng và dùng hàm tổng hợp, không lấy một dòng lẻ.

THUẬT NGỮ NGHIỆP VỤ:
- "hợp đồng đã được duyệt / phê duyệt" → supply_contracts.status = 'APPROVED'.
- "công ty phái cử" là mọi công ty trong bảng companies; KHÔNG lọc theo cột category.
- "công ty liên quan đến / thị trường <nước>" → companies.market_country; KHÔNG lọc theo company_name và KHÔNG lọc theo category.
- "hợp đồng đi / thị trường <nước>" → supply_contracts.receiving_country.
- "lao động đã phái cử / đã xuất cảnh" → workers.status = 'DEPLOYED'.
- "còn hiệu lực" → CURRENT_DATE BETWEEN valid_from AND valid_to.
- "quy đổi ra tiền Việt" → order_financials.base_salary * currencies.exchange_rate_to_vnd.

GHI CHÚ CỘT:
- companies.market_country: quốc gia thị trường của công ty (tiếng Việt) — dùng cột này khi lọc công ty theo nước.
- companies.category: mã thô lộn xộn, không lọc trực tiếp."""

_EXPLAIN_SYSTEM = (
    "Bạn là chuyên gia PostgreSQL. Hãy giải thích ngắn gọn bằng tiếng Việt "
    "câu lệnh SQL sau đây làm gì, tối đa 3-4 câu, không viết lại SQL."
)


# Converts examples into user/assistant conversation turns
def _few_shot(examples):
    turns = []
    for ex in examples or []:
        turns.append({"role": "user", "content": f"Câu hỏi: {ex['question']}"})
        turns.append({"role": "assistant", "content": ex["sql"]})
    return turns


# Prompt for generating SQL
def build_sql_messages(question, schema, examples=None):
    return [
        {"role": "system", "content": SYSTEM_SQL},
        *_few_shot(examples),
        {"role": "user", "content": f"Schema cơ sở dữ liệu:\n{schema}\n\nCâu hỏi: {question}"},
    ]


# Prompt for self-healing when the previous SQL failed
def build_heal_messages(question, schema, bad_sql, error, attempt, total, examples=None):
    user = (
        f"Schema cơ sở dữ liệu:\n{schema}\n\n"
        f"Lần thử: {attempt}/{total}\n"
        f"Câu hỏi: {question}\n\n"
        f"Câu SQL trước đó chưa dùng được:\n{bad_sql}\n\n"
        f"Nguyên nhân: {error}\n\n"
        "Sửa đúng nguyên nhân trên. Chỉ trả về đúng một câu lệnh SELECT hợp lệ, không kèm giải thích."
    )
    return [
        {"role": "system", "content": SYSTEM_SQL},
        *_few_shot(examples),
        {"role": "user", "content": user},
    ]


# Prompt for explaining SQL in Vietnamese
def build_explain_messages(sql, schema):
    return [
        {"role": "system", "content": _EXPLAIN_SYSTEM},
        {"role": "user", "content": f"Schema:\n{schema}\n\nCâu SQL:\n{sql}"},
    ]
