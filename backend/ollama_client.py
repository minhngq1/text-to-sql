import json
import re

import httpx

from config import settings
from errors import LlmError
from prompts import build_explain_messages

_NO_RESPONSE = "Model Ollama không phản hồi. Kiểm tra 'ollama serve' và tên model text2sql."
_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_STMT_START = re.compile(r"^[ \t]*(select|with)\b", re.IGNORECASE | re.MULTILINE)
_ANY_START = re.compile(r"\b(select|with)\b", re.IGNORECASE)


# Cuts at the first ; that is outside a string literal
def _first_statement(text):
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if in_string and text.startswith("''", i):
                i += 2
                continue
            in_string = not in_string
        elif ch == ";" and not in_string:
            return text[:i]
        i += 1
    return text


# Extracts a clean SQL statement from the model's raw output
def clean_sql(text):
    text = text.strip()
    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()

    start = _STMT_START.search(text) or _ANY_START.search(text)
    if start:
        text = text[start.start():]

    return _first_statement(text).strip().rstrip(";").strip()


# Calls the text2sql model running on Ollama
class OllamaClient:
    def __init__(self):
        self.url = f"{settings.ollama_url}/api/chat"
        self._client = httpx.AsyncClient(timeout=settings.ollama_timeout)

    # Closes the HTTP client when the app shuts down
    async def aclose(self):
        await self._client.aclose()

    def _payload(self, messages, stream):
        return {
            "model": settings.ollama_model,
            "messages": messages,
            "stream": stream,
            "keep_alive": settings.ollama_keep_alive,
            "options": {
                "temperature": settings.temperature,
                "num_ctx": settings.num_ctx,
            },
        }

    async def _chat(self, messages):
        try:
            resp = await self._client.post(self.url, json=self._payload(messages, False))
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as e:
            raise LlmError(_NO_RESPONSE, raw=f"HTTP {e.response.status_code}: {e.response.text[:300]}") from e
        except (httpx.HTTPError, ValueError) as e:
            raise LlmError(_NO_RESPONSE, raw=str(e)) from e

        if body.get("error"):
            raise LlmError(_NO_RESPONSE, raw=str(body["error"]))
        try:
            return body["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LlmError(_NO_RESPONSE, raw=f"Phản hồi không đúng định dạng: {body!r:.300}") from e

    # Explains a SQL statement in Vietnamese
    async def explain_sql(self, sql, schema):
        text = await self._chat(build_explain_messages(sql, schema))
        return text.strip()

    # Calls Ollama in streaming mode, yielding text chunks one at a time
    async def stream_chat(self, messages):
        try:
            async with self._client.stream(
                "POST", self.url, json=self._payload(messages, True)
            ) as resp:
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", "replace")
                    raise LlmError(_NO_RESPONSE, raw=f"HTTP {resp.status_code}: {body[:300]}")

                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    frame = json.loads(line)

                    if frame.get("error"):
                        raise LlmError(_NO_RESPONSE, raw=str(frame["error"]))

                    delta = (frame.get("message") or {}).get("content", "")
                    if delta:
                        yield delta

                    if frame.get("done") and frame.get("done_reason") == "length":
                        raise LlmError(
                            "Model sinh quá dài và bị cắt giữa chừng. "
                            "Tăng OLLAMA_NUM_CTX trong .env rồi thử lại.",
                            raw=f"done_reason=length, num_ctx={settings.num_ctx}",
                        )
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            raise LlmError(_NO_RESPONSE, raw=str(e)) from e
