import re

from config import settings
from errors import SqlUnsafeError

_BLOCKED = (
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "merge", "vacuum", "reindex", "cluster", "refresh",
    "listen", "notify", "prepare", "declare", "copy", "dblink",
    "pg_read_file", "pg_read_binary_file", "pg_read_server_files",
    "pg_write_server_files", "pg_ls_dir", "pg_ls_logdir", "pg_ls_waldir",
    "pg_stat_file", "pg_sleep", "pg_terminate_backend", "pg_cancel_backend",
    "lo_import", "lo_export", "lo_get", "set_config", "current_setting",
    "query_to_xml", "pg_authid", "pg_shadow", "postgres_fdw", "file_fdw",
)

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")


# Blanks out the content of a string literal, returns the position right after it
def _blank_quoted(sql, out, start, escaped):
    n = len(sql)
    j = start + 1
    while j < n:
        if escaped and sql[j] == "\\":
            j += 2
            continue
        if sql[j] == "'":
            if sql.startswith("''", j):
                j += 2
                continue
            for k in range(start + 1, j):
                out[k] = " "
            return j + 1
        j += 1
    raise SqlUnsafeError("Câu truy vấn có chuỗi không đóng.")


# Builds a SQL skeleton, replacing comments and string contents with spaces
def _blank(sql):
    out = list(sql)
    n = len(sql)
    i = 0
    while i < n:
        ch = sql[i]

        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
            continue

        if sql.startswith("/*", i):
            depth = 1
            j = i + 2
            while j < n and depth:
                if sql.startswith("/*", j):
                    depth += 1
                    j += 2
                elif sql.startswith("*/", j):
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                raise SqlUnsafeError("Câu truy vấn có comment không đóng.")
            for k in range(i, j):
                out[k] = " "
            i = j
            continue

        prev_is_word = i > 0 and (sql[i - 1].isalnum() or sql[i - 1] == "_")
        if ch in "Ee" and not prev_is_word and sql.startswith("'", i + 1):
            i = _blank_quoted(sql, out, i + 1, escaped=True)
            continue

        if ch == "'":
            i = _blank_quoted(sql, out, i, escaped=False)
            continue

        if ch == '"':
            j = i + 1
            while j < n:
                if sql[j] == '"':
                    if sql.startswith('""', j):
                        j += 2
                        continue
                    break
                j += 1
            if j >= n:
                raise SqlUnsafeError("Câu truy vấn có dấu nháy kép không đóng.")
            i = j + 1
            continue

        if ch == "$":
            tag = _DOLLAR_TAG.match(sql, i)
            if tag:
                close = sql.find(tag.group(0), tag.end())
                if close == -1:
                    raise SqlUnsafeError("Câu truy vấn có chuỗi $$ không đóng.")
                end = close + len(tag.group(0))
                for k in range(i, end):
                    out[k] = " "
                i = end
                continue

        i += 1
    return "".join(out)


# Enforces LIMIT at the outermost level, capped at DEFAULT_LIMIT
def _apply_limit(sql, skeleton):
    cap = settings.default_limit
    depth = 0
    for m in re.finditer(r"[()]|\blimit\b", skeleton, re.IGNORECASE):
        token = m.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            number = re.match(r"\s*(\d+)", skeleton[m.end():])
            if not number:
                return f"{sql[:m.start()].rstrip()}\nLIMIT {cap}"
            if int(number.group(1)) <= cap:
                return sql
            head = sql[: m.end() + number.start(1)]
            tail = sql[m.end() + number.end(1):]
            return f"{head}{cap}{tail}"
    return f"{sql}\nLIMIT {cap}"


# Validates the SQL statement and returns the exact string that will be executed
def sanitize(sql):
    sql = (sql or "").strip()
    if not sql:
        raise SqlUnsafeError("Câu truy vấn rỗng.")

    skeleton = _blank(sql)

    end = len(sql)
    while end and (skeleton[end - 1].isspace() or skeleton[end - 1] == ";"):
        end -= 1
    sql = sql[:end]
    skeleton = skeleton[:end]

    if not skeleton.strip():
        raise SqlUnsafeError("Câu truy vấn rỗng.")

    lowered = skeleton.lower()
    if not re.match(r"\s*(select|with)\b", lowered):
        raise SqlUnsafeError("Chỉ cho phép câu lệnh SELECT.")

    if ";" in skeleton:
        raise SqlUnsafeError("Không cho phép nhiều câu lệnh trong một truy vấn.")

    for word in _BLOCKED:
        if re.search(rf"\b{word}\b", lowered):
            raise SqlUnsafeError(f"Câu truy vấn chứa từ khoá bị cấm: {word}.")

    return _apply_limit(sql, skeleton)
