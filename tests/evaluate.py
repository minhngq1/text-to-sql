# Scores model accuracy by comparing result tables against the gold answers

import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx
import psycopg2

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE.parent / "backend"))


for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

import security  # noqa: E402
from config import settings  # noqa: E402

API_URL = "http://localhost:8000/api/v1/query"
REQUEST_TIMEOUT_S = 300


LEVEL_ORDER = ["retrieval", "filter", "aggregate", "join", "aggregate_join", "subquery"]


def _level_sort_key(level):
    return (LEVEL_ORDER.index(level) if level in LEVEL_ORDER else len(LEVEL_ORDER), level)


def db_connect():
    conn = psycopg2.connect(
        host=settings.db_host,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        port=settings.db_port,
    )
    conn.set_session(readonly=True)
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = %s", (settings.sql_timeout_ms,))
    conn.commit()
    return conn


def run_sql(conn, sql):
    try:
        safe_sql = security.sanitize(sql)
    except Exception as e:  # noqa: BLE001
        return None, f"kiểm duyệt từ chối: {e}"
    try:
        with conn.cursor() as cur:
            cur.execute(safe_sql)
            rows = cur.fetchall() if cur.description else []
        return rows, None
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        return None, str(e).strip()


def norm_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, (float, Decimal)):
        return f"{float(v):.1f}"
    return str(v).strip()


def norm(rows, ordered):
    table = [tuple(norm_val(v) for v in row) for row in rows]
    return table if ordered else sorted(table, key=repr)


def percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = min(len(ordered) - 1, int(pct / 100 * len(ordered)))
    return round(ordered[k], 1)


def ask_model(question, retries=1):
    started = time.perf_counter()
    try:
        resp = httpx.post(API_URL, json={"question": question}, timeout=REQUEST_TIMEOUT_S)
    except httpx.HTTPError as e:
        return None, 0, f"Không gọi được API: {e}", 0.0
    elapsed = time.perf_counter() - started

    try:
        body = resp.json()
    except ValueError:
        detail = f"HTTP {resp.status_code}, thân phản hồi không phải JSON: {resp.text[:200]}"
        return None, 0, detail, elapsed

    # Retry once if Ollama does not respond
    if resp.status_code == 502 and retries > 0:
        return ask_model(question, retries - 1)

    if resp.status_code != 200:
        return None, 0, body.get("detail", f"HTTP {resp.status_code}"), elapsed
    return body.get("sql_query"), body.get("attempts", 1), None, elapsed


def compare(conn, item, model_sql):
    ordered = bool(item.get("ordered"))

    gold_rows, gold_err = run_sql(conn, item["sql"])
    if gold_err:
        return False, f"ĐÁP ÁN LỖI (cần sửa gold_queries.json): {gold_err}"

    model_rows, model_err = run_sql(conn, model_sql)
    if model_err:
        return False, f"SQL model lỗi: {model_err}"

    if norm(model_rows, ordered) == norm(gold_rows, ordered):
        return True, None

    if len(model_rows) != len(gold_rows):
        return False, f"số dòng khác: model {len(model_rows)} vs đáp án {len(gold_rows)}"
    model_cols = len(model_rows[0]) if model_rows else 0
    gold_cols = len(gold_rows[0]) if gold_rows else 0
    if model_cols != gold_cols:
        return False, f"số cột khác: model {model_cols} vs đáp án {gold_cols}"
    if not ordered:
        return False, "cùng kích thước nhưng giá trị khác"
    if norm(model_rows, False) == norm(gold_rows, False):
        return False, "đúng tập dòng nhưng SAI THỨ TỰ sắp xếp"
    return False, "cùng kích thước nhưng giá trị khác"


def main():
    try:
        limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    except ValueError:
        print(f"Tham số phải là số nguyên, đang là {sys.argv[1]!r}.")
        return 2

    gold = json.loads((BASE / "gold_queries.json").read_text(encoding="utf-8"))
    if limit is not None:
        gold = gold[:limit]
    if not gold:
        print("Không có câu nào để chấm.")
        return 0

    conn = db_connect()
    passed = 0
    total_attempts = 0
    by_level = defaultdict(lambda: {"passed": 0, "total": 0})
    failures = []
    latencies = []

    try:
        for i, item in enumerate(gold, 1):
            question = item["question"]
            level = item.get("level", "khac")
            by_level[level]["total"] += 1

            model_sql, attempts, err, elapsed = ask_model(question)
            if model_sql is None:
                ok, reason = False, err
            else:
                total_attempts += attempts
                latencies.append(elapsed)
                ok, reason = compare(conn, item, model_sql)

            if ok:
                passed += 1
                by_level[level]["passed"] += 1
                status = "PASS"
            else:
                status = f"FAIL ({reason})"
                failures.append({
                    "id": item["id"], "level": level, "question": question,
                    "gold_sql": item["sql"], "model_sql": model_sql, "reason": reason,
                })

            print(f"[{i}/{len(gold)}] #{item['id']:>2} [{level:>7}] {status}  |  {question}")
    finally:
        conn.close()

    count = len(gold)
    accuracy = round(100.0 * passed / count, 1)
    answered = count - sum(1 for f in failures if f["model_sql"] is None)
    avg_attempts = round(total_attempts / answered, 2) if answered else 0
    p50 = percentile(latencies, 50)
    p95 = percentile(latencies, 95)

    print("\n" + "=" * 62)
    print(f"ĐỘ CHÍNH XÁC: {passed}/{count} = {accuracy}%")
    print(f"Số lần sinh SQL trung bình mỗi câu (1 = không phải tự sửa): {avg_attempts}")
    print(f"Thời gian trả lời mỗi câu: p50 = {p50}s · p95 = {p95}s")
    print("=" * 62)
    print(f"{'Mức độ':<10}{'Đúng':>6}{'Tổng':>6}{'Tỉ lệ':>9}")
    levels = {}
    for level, stat in sorted(by_level.items(), key=lambda pair: _level_sort_key(pair[0])):
        ratio = round(100.0 * stat["passed"] / stat["total"], 1) if stat["total"] else 0.0
        levels[level] = {**stat, "accuracy": ratio}
        print(f"{level:<10}{stat['passed']:>6}{stat['total']:>6}{ratio:>8}%")
    print("=" * 62)

    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": settings.ollama_model,
        "temperature": settings.temperature,
        "passed": passed,
        "total": count,
        "accuracy": accuracy,
        "avg_attempts": avg_attempts,
        "latency_p50_s": p50,
        "latency_p95_s": p95,
        "by_level": levels,
        "failures": failures,
    }
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (BASE / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    history = BASE / "reports"
    history.mkdir(exist_ok=True)
    (history / f"report_{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Chi tiết {len(failures)} câu sai: tests/report.json (bản lưu: tests/reports/report_{stamp}.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
