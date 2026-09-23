import asyncio
import logging
import time
from pathlib import Path

import security
from config import settings
from errors import DbError, SqlUnsafeError
from prompts import build_sql_messages, build_heal_messages
from ollama_client import clean_sql
from retriever import ExampleRetriever

logger = logging.getLogger(__name__)

_EXAMPLES_PATH = Path(__file__).parent / "examples.json"


# Orchestrates SQL generation, validation, execution, and self-healing on error
class TextToSqlAgent:
    def __init__(self, ollama, database):
        self.ollama = ollama
        self.db = database
        self.retriever = ExampleRetriever(_EXAMPLES_PATH)

    # Process one question, yielding step-by-step events
    async def run(self, question):
        yield {"type": "step", "step": "schema", "message": "Đọc cấu trúc cơ sở dữ liệu"}
        schema = await asyncio.to_thread(self.db.get_schema_text)
        examples = self.retriever.retrieve(question)

        total = settings.max_heal_attempts
        last_error = None
        last_output = None

        for attempt in range(1, total + 1):
            if attempt == 1:
                yield {"type": "step", "step": "generating", "message": "Model đang sinh SQL"}
                messages = build_sql_messages(question, schema, examples)
            else:
                yield {
                    "type": "step", "step": "healing", "attempt": attempt,
                    "message": f"Chưa đạt, model tự sửa (lần {attempt}/{total})",
                }
                messages = build_heal_messages(
                    question, schema, last_output, last_error, attempt, total, examples
                )

            raw = ""
            async for delta in self.ollama.stream_chat(messages):
                raw += delta
                yield {"type": "token", "content": delta}
            sql = clean_sql(raw)
            last_output = sql
            yield {"type": "sql", "sql": sql}

            yield {"type": "step", "step": "security", "message": "Kiểm duyệt an toàn SQL"}
            try:
                safe_sql = security.sanitize(sql)
            except SqlUnsafeError as e:
                last_error = f"Lớp kiểm duyệt từ chối câu lệnh: {e.message}"
                logger.info("Kiểm duyệt chặn ở lần %s: %s", attempt, e.message)
                continue

            yield {"type": "step", "step": "executing", "message": "Chạy SQL trên PostgreSQL"}
            started = time.perf_counter()
            try:
                data = await asyncio.to_thread(self.db.run_select, safe_sql)
            except DbError as e:
                last_error = f"PostgreSQL báo lỗi: {e.raw[:500]}"
                logger.info("SQL lỗi ở lần %s: %s", attempt, e.raw)
                continue

            yield {
                "type": "result", "sql_query": safe_sql, "row_count": len(data),
                "data": data, "attempts": attempt,
                "exec_ms": round((time.perf_counter() - started) * 1000),
            }
            return

        logger.info("Bỏ cuộc sau %s lần thử. Lỗi cuối: %s", total, last_error)
        yield {
            "type": "error",
            "detail": (
                f"Không tạo được câu truy vấn hợp lệ sau {total} lần thử. "
                "Thử diễn đạt lại câu hỏi cụ thể hơn."
            ),
        }

    # Non-streaming variant, returns only the final result
    async def answer(self, question):
        async for event in self.run(question):
            if event["type"] == "result":
                return event
            if event["type"] == "error":
                raise DbError(event["detail"])
        raise DbError("Agent kết thúc mà không trả về kết quả.")
