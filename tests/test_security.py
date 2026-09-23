# Tests the SQL validation layer in backend/security.py

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / "backend"))

import security  # noqa: E402
from config import settings  # noqa: E402
from errors import SqlUnsafeError  # noqa: E402


BLOCKED = [
    (
        "bypass bằng -- nằm trong chuỗi literal",
        "SELECT hometown FROM workers WHERE hometown = 'a--b'; "
        "SELECT pg_read_file('/etc/passwd')",
    ),
    (
        "bypass bằng /* */ nằm trong chuỗi literal",
        "SELECT full_name FROM workers WHERE hometown = 'a/*b'; DROP TABLE workers",
    ),
    ("nhiều câu lệnh ngăn bởi ;", "SELECT 1; SELECT 2"),
    ("câu lệnh ghi dữ liệu", "DELETE FROM workers"),
    ("DROP núp sau comment đầu câu", "/* xin chào */ DROP TABLE companies"),
    ("đọc file hệ thống", "SELECT pg_read_file('/etc/passwd')"),
    ("liệt kê thư mục", "SELECT * FROM pg_ls_dir('/')"),
    ("đọc bảng mật khẩu", "SELECT rolpassword FROM pg_authid"),
    ("làm treo kết nối", "SELECT pg_sleep(60)"),
    ("chuỗi dollar-quote giấu câu lệnh thứ hai", "SELECT $$a$$; DROP TABLE workers"),
    ("chuỗi không đóng", "SELECT * FROM workers WHERE hometown = 'abc"),
    ("comment khối không đóng", "SELECT * FROM workers /* chưa đóng"),
    ("ghi đè cấu hình phiên", "SELECT set_config('statement_timeout', '0', false)"),
    ("rò rỉ qua query_to_xml", "SELECT query_to_xml('SELECT 1', true, true, '')"),
    ("không phải câu SELECT", "UPDATE workers SET status = 'DEPLOYED'"),
    ("câu rỗng", "   "),
]

ALLOWED = [
    ("SELECT thường", "SELECT * FROM companies"),
    (
        "ILIKE có ký tự % và dấu tiếng Việt",
        "SELECT * FROM companies WHERE market_country ILIKE '%Nhật Bản%'",
    ),
    (
        "literal chứa hai dấu gạch ngang",
        "SELECT * FROM companies WHERE company_name ILIKE '%A--B%'",
    ),
    (
        "literal chứa dấu chấm phẩy",
        "SELECT * FROM workers WHERE hometown = 'Hà Nội; Việt Nam'",
    ),
    (
        "literal chứa từ khoá bị cấm",
        "SELECT * FROM recruitment_orders WHERE job_title ILIKE '%delete%'",
    ),
    ("nháy đơn lồng theo chuẩn SQL", "SELECT * FROM officers WHERE full_name = 'O''Brien'"),
    ("CTE dùng WITH", "WITH t AS (SELECT id FROM companies) SELECT * FROM t"),
    (
        "JOIN nhiều bảng",
        "SELECT c.company_name, sc.document_number FROM supply_contracts sc "
        "JOIN companies c ON sc.company_id = c.id WHERE sc.status = 'APPROVED'",
    ),
    ("có dấu ; ở cuối câu", "SELECT * FROM officers;"),
    ("comment cuối câu", "SELECT * FROM officers -- lấy hết cán bộ"),
]

LIMIT_CASES = [
    (
        "tự thêm LIMIT khi chưa có",
        "SELECT * FROM workers",
        lambda out: out.endswith(f"LIMIT {settings.default_limit}"),
    ),
    (
        "giữ nguyên LIMIT nhỏ hơn mức trần",
        "SELECT * FROM workers LIMIT 5",
        lambda out: out.strip().endswith("LIMIT 5"),
    ),
    (
        "hạ LIMIT vượt trần xuống mức trần",
        "SELECT * FROM workers LIMIT 10000000",
        lambda out: f"LIMIT {settings.default_limit}" in out and "10000000" not in out,
    ),
    (
        "LIMIT trong subquery không tính là LIMIT ngoài cùng",
        "SELECT * FROM (SELECT * FROM workers LIMIT 3) t",
        lambda out: out.rstrip().endswith(f"LIMIT {settings.default_limit}"),
    ),
    (
        "comment cuối câu không nuốt mất LIMIT",
        "SELECT * FROM workers -- ghi chú",
        lambda out: out.splitlines()[-1].strip() == f"LIMIT {settings.default_limit}",
    ),
    (
        "LIMIT ALL bị thay bằng mức trần",
        "SELECT * FROM workers LIMIT ALL",
        lambda out: out.rstrip().endswith(f"LIMIT {settings.default_limit}"),
    ),
]


def main():
    failed = []

    for label, sql in BLOCKED:
        try:
            out = security.sanitize(sql)
            failed.append(f"[CHẶN] {label}: lọt qua, trả về {out!r}")
        except SqlUnsafeError:
            pass
        except Exception as e:  # noqa: BLE001
            failed.append(f"[CHẶN] {label}: ném sai loại lỗi {type(e).__name__}: {e}")

    for label, sql in ALLOWED:
        try:
            security.sanitize(sql)
        except Exception as e:  # noqa: BLE001
            failed.append(f"[CHO QUA] {label}: bị chặn oan ({e})")

    for label, sql, ok in LIMIT_CASES:
        try:
            out = security.sanitize(sql)
        except Exception as e:  # noqa: BLE001
            failed.append(f"[LIMIT] {label}: ném lỗi {e}")
            continue
        if not ok(out):
            failed.append(f"[LIMIT] {label}: kết quả không đạt -> {out!r}")

    total = len(BLOCKED) + len(ALLOWED) + len(LIMIT_CASES)
    passed = total - len(failed)
    print(f"Kiểm duyệt SQL: {passed}/{total} ca đạt")
    for line in failed:
        print("  FAIL " + line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
