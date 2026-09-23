import logging
import threading

import psycopg2
from psycopg2 import pool as pgpool
from psycopg2 import sql as pgsql

from config import settings
from errors import DbError

logger = logging.getLogger(__name__)

CATEGORICAL_MAX_VALUES = 30
MAX_VALUE_LENGTH = 60

_FREETEXT_HINTS = (
    "name", "address", "email", "phone", "website", "reason", "policy",
    "title", "description", "allowance", "deduction", "note",
)

_CONNECT_FAILED = "Không kết nối được tới PostgreSQL. Kiểm tra database đã chạy và thông tin trong .env."
_SCHEMA_FAILED = "Không đọc được cấu trúc cơ sở dữ liệu."


# Rename duplicate columns to id, id_2...
def _dedupe(columns):
    seen = {}
    result = []
    for name in columns:
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return result


# Connects to PostgreSQL, reads the schema, and runs SELECT statements
class Database:
    def __init__(self):
        self._schema_text = None
        self._lock = threading.Lock()
        self._pool = None

    def _get_pool(self):
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = pgpool.ThreadedConnectionPool(
                        minconn=1,
                        maxconn=8,
                        host=settings.db_host,
                        dbname=settings.db_name,
                        user=settings.db_user,
                        password=settings.db_password,
                        port=settings.db_port,
                    )
        return self._pool

    def _acquire(self):
        try:
            return self._get_pool().getconn()
        except psycopg2.Error as e:
            logger.error("Không kết nối được PostgreSQL: %s", e)
            raise DbError(_CONNECT_FAILED, raw=str(e).strip()) from e

    def _release(self, conn):
        try:
            conn.rollback()
        except psycopg2.Error:
            pass
        self._pool.putconn(conn)

    # Closes all connections in the pool when the app shuts down
    def close(self):
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None

    def _read_tables(self, conn):
        list_tables = """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """
        list_columns = """
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
        """
        with conn.cursor() as cur:
            cur.execute(list_tables)
            names = [row[0] for row in cur.fetchall()]

            tables = []
            for name in names:
                cur.execute(list_columns, (name,))
                columns = [{"name": col, "type": dtype} for col, dtype in cur.fetchall()]
                tables.append({"name": name, "columns": columns})
            return tables

    # Reads foreign keys from pg_catalog, returned as 'table.column -> ref_table.ref_column'
    def _read_foreign_keys(self, conn):
        query = """
            SELECT src.relname, sa.attname, tgt.relname, ta.attname
            FROM pg_constraint c
            JOIN pg_class src ON src.oid = c.conrelid
            JOIN pg_class tgt ON tgt.oid = c.confrelid
            JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
            JOIN unnest(c.confkey) WITH ORDINALITY AS fk(attnum, ord) ON fk.ord = k.ord
            JOIN pg_attribute sa ON sa.attrelid = c.conrelid AND sa.attnum = k.attnum
            JOIN pg_attribute ta ON ta.attrelid = c.confrelid AND ta.attnum = fk.attnum
            WHERE c.contype = 'f' AND c.connamespace = 'public'::regnamespace
            ORDER BY src.relname, sa.attname
        """
        with conn.cursor() as cur:
            cur.execute(query)
            return [f"{t}.{c} -> {rt}.{rc}" for t, c, rt, rc in cur.fetchall()]

    # List of tables and columns in the database
    def get_tables(self):
        conn = self._acquire()
        try:
            return self._read_tables(conn)
        except psycopg2.Error as e:
            logger.error("Không đọc được schema: %s", e)
            raise DbError(_SCHEMA_FAILED, raw=str(e).strip()) from e
        finally:
            self._release(conn)

    # Schema as a string for the prompt, including value hints and foreign keys
    def get_schema_text(self):
        if self._schema_text is not None:
            return self._schema_text

        conn = self._acquire()
        try:
            lines = []
            for table in self._read_tables(conn):
                cols = ", ".join(f"{c['name']} {c['type']}" for c in table["columns"])
                lines.append(f"{table['name']}({cols})")
                for col in table["columns"]:
                    values = self._categorical_values(conn, table["name"], col)
                    if values:
                        lines.append(f"  - {col['name']} ∈ {{{', '.join(values)}}}")

            relations = self._read_foreign_keys(conn)
            if relations:
                lines.append("")
                lines.append("QUAN HỆ (dùng để JOIN):")
                lines.extend(f"- {rel}" for rel in relations)

            self._schema_text = "\n".join(lines)
            return self._schema_text
        except psycopg2.Error as e:
            logger.error("Không đọc được schema: %s", e)
            raise DbError(_SCHEMA_FAILED, raw=str(e).strip()) from e
        finally:
            self._release(conn)

    # Clears the cached schema
    def refresh_schema(self):
        self._schema_text = None

    # List of values for a categorical column, None if not categorical
    def _categorical_values(self, conn, table, column):
        if not column["type"].startswith(("character", "text")):
            return None
        if any(word in column["name"].lower() for word in _FREETEXT_HINTS):
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    pgsql.SQL(
                        "SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL "
                        "ORDER BY 1 LIMIT %s"
                    ).format(
                        col=pgsql.Identifier(column["name"]),
                        tbl=pgsql.Identifier(table),
                    ),
                    (CATEGORICAL_MAX_VALUES + 1,),
                )
                rows = [str(r[0]) for r in cur.fetchall()]
        except psycopg2.Error as e:
            logger.warning("Bỏ qua value-hint cho %s.%s: %s", table, column["name"], e)
            conn.rollback()
            return None

        if not 0 < len(rows) <= CATEGORICAL_MAX_VALUES:
            return None
        if any(len(value) > MAX_VALUE_LENGTH for value in rows):
            return None
        return rows

    # Runs a SELECT statement inside a read-only transaction
    def run_select(self, sql):
        conn = self._acquire()
        try:
            conn.set_session(readonly=True)
            with conn.cursor() as cur:
                cur.execute("SET LOCAL statement_timeout = %s", (settings.sql_timeout_ms,))
                cur.execute(sql)
                columns = _dedupe([d[0] for d in cur.description]) if cur.description else []
                rows = cur.fetchall() if cur.description else []
            return [dict(zip(columns, row)) for row in rows]
        except psycopg2.Error as e:
            raise DbError("Câu truy vấn chạy lỗi trên PostgreSQL.", raw=str(e).strip()) from e
        finally:
            try:
                conn.rollback()
                conn.set_session(readonly=False)
            except psycopg2.Error:
                pass
            self._release(conn)
