# Kiểm thử — Text-to-SQL XKLĐ

## 1. Cách chạy

```bash
python tests/test_security.py   # không cần backend/Ollama/DB, chạy trong ~1 giây
python tests/evaluate.py        # cần backend + Ollama + PostgreSQL đang chạy, mất ~15-20 phút
```

## 2. Bảo mật — `test_security.py`

```
Kiểm duyệt SQL: 32/32 ca đạt
```

- **16/16** ca tấn công bị chặn đúng: SQL injection núp trong chuỗi/comment, đọc file hệ thống
  (`pg_read_file`), liệt kê thư mục, đọc bảng mật khẩu (`pg_authid`), multi-statement, DROP/DELETE,
  làm treo kết nối (`pg_sleep`), rò rỉ qua `query_to_xml`, bypass bằng dollar-quote...
- **10/10** câu truy vấn hợp lệ (JOIN, CTE, ILIKE, literal chứa từ khoá cấm...) không bị chặn oan.
- **6/6** ca kiểm tra LIMIT (tự thêm, giữ nguyên, hạ mức vượt trần, không tính LIMIT trong subquery...)
  đều đúng.

## 3. Độ chính xác — `evaluate.py` (90 câu hỏi, `gold_queries.json`)

| Model | Accuracy | Số lần sinh SQL TB | Độ trễ p50 | Độ trễ p95 |
|---|:--:|:--:|:--:|:--:|
| Base (Qwen2.5-Coder-7B-Instruct, chưa fine-tune) | 66.7% (60/90) | 1.04 | 32.9s | 57.9s |
| **Fine-tuned** (QLoRA, model `text2sql`) | **74.4% (67/90)** | 1.0 | 35.8s | 58.3s |

Theo mức độ câu hỏi:

| Mức độ | Base | Fine-tuned |
|---|:--:|:--:|
| retrieval | 66.7% | 66.7% |
| filter | 80.0% | 66.7% |
| aggregate | 93.3% | 93.3% |
| join | 66.7% | **100.0%** |
| aggregate_join | 53.3% | 66.7% |
| subquery | 40.0% | 53.3% |

Fine-tune giúp tăng **+7.7 điểm %** độ chính xác tổng, cải thiện rõ nhất ở JOIN. Chi tiết từng câu sai
nằm ở `tests/report_base.json` và `tests/report_finetuned.json`.
