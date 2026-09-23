import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError

import security
from errors import AppError, LlmError, DbError, SqlUnsafeError
from schemas import QueryRequest, ExplainRequest, ExecuteRequest
from database import Database
from ollama_client import OllamaClient
from agent import TextToSqlAgent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

db = Database()
ollama = OllamaClient()
agent = TextToSqlAgent(ollama, db)


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@asynccontextmanager
async def lifespan(app):
    yield
    await ollama.aclose()
    db.close()


app = FastAPI(title="Text-to-SQL XKLĐ", version="2.1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)


# Maps application errors to HTTP status codes
@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    if isinstance(exc, LlmError):
        code = 502
    elif isinstance(exc, (DbError, SqlUnsafeError)):
        code = 400
    else:
        code = 500
    if exc.raw != exc.message:
        logger.warning("%s: %s", type(exc).__name__, exc.raw)
    return JSONResponse(status_code=code, content={"success": False, "detail": exc.message})


# List of tables and columns for the sidebar
@app.get("/api/v1/schema")
async def get_schema():
    tables = await asyncio.to_thread(db.get_tables)
    return {"success": True, "tables": tables}


# Generates SQL from the question, runs it, and returns the final result
@app.post("/api/v1/query")
async def run_query(payload: QueryRequest):
    result = await agent.answer(payload.question)
    return {
        "success": True,
        "question": payload.question,
        "sql_query": result["sql_query"],
        "row_count": result["row_count"],
        "data": result["data"],
        "attempts": result["attempts"],
        "exec_ms": result["exec_ms"],
    }


# Generates SQL and streams progress via SSE
@app.get("/api/v1/query/stream")
async def run_query_stream(question: str = ""):
    async def event_stream():
        try:
            payload = QueryRequest(question=question)
        except ValidationError:
            yield _sse({"type": "error", "detail": "Câu hỏi phải dài 5-500 ký tự."})
            return
        try:
            async for event in agent.run(payload.question):
                yield _sse(event)
        except AppError as e:
            if e.raw != e.message:
                logger.warning("%s: %s", type(e).__name__, e.raw)
            yield _sse({"type": "error", "detail": e.message})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Lỗi không lường trước khi xử lý câu hỏi")
            yield _sse({"type": "error", "detail": "Lỗi hệ thống khi xử lý câu hỏi."})

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=SSE_HEADERS
    )


def _sse(event):
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


# Runs a user-edited SQL statement, still through the validation layer
@app.post("/api/v1/execute")
async def execute_sql(payload: ExecuteRequest):
    safe_sql = security.sanitize(payload.sql)
    started = time.perf_counter()
    try:
        data = await asyncio.to_thread(db.run_select, safe_sql)
    except DbError as e:
        raise DbError(f"PostgreSQL báo lỗi: {e.raw}", raw=e.raw) from e
    return {
        "success": True,
        "sql_query": safe_sql,
        "row_count": len(data),
        "data": data,
        "exec_ms": round((time.perf_counter() - started) * 1000),
    }


# Explains a SQL statement in Vietnamese
@app.post("/api/v1/explain")
async def explain(payload: ExplainRequest):
    schema = await asyncio.to_thread(db.get_schema_text)
    explanation = await ollama.explain_sql(payload.sql, schema)
    return {"success": True, "sql": payload.sql, "explanation": explanation}
