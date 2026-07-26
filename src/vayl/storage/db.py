#!/usr/bin/env python3
"""
Database abstraction (M6) — one code path for SQLite (default) and Postgres (scale).

SQLite is the tested default and needs no server — correct for a single-node deployment under the
process lock. Postgres is the multi-writer backend for teams that outgrow a single node; this module
normalizes the dialect differences (placeholders, autoincrement PKs, per-space locking) so the same
SQL runs on both, and provides a **cross-process advisory lock** so concurrent writers to one memory
space serialize correctly on Postgres.

STATUS — read this honestly:
  • SQLite path: the default, covered by the full test suite.
  • Postgres path: VALIDATED on live Postgres 17 — every storage module (statements, audit, decisions,
    receipts, principals, metrics) plus tenant isolation, the tamper-evident chain, and the advisory
    lock. See tests/test_postgres.py (runs when VAYL_TEST_DATABASE_URL is set) and the end-to-end run
    (real MCP client → vayl-server on Postgres → Ollama).
  • Multi-process: `space_lock` IS wired into mcp_server's write tools (remember/forget/update/delete/
    delete_all), so many vayl-server processes on one Postgres serialize same-space writes via the
    advisory lock (different spaces run in parallel) — no id collision. Validated by a concurrent
    cross-connection test (tests/test_postgres.py) and a live multi-write e2e (0 locks held afterward).
  • Concurrent WITHIN a process too: the process-global threading lock is gone. What it secretly
    protected is now handled piecewise — ids are allocated per space (seeded from MAX(id) at load),
    connections are thread-local (below), and the audit hash-chain serializes its own append — so
    tools to different spaces run in parallel and same-space writes serialize on `space_lock`.

Connection URLs:
    vayl.db                         → SQLite file (back-compat: a bare path)
    sqlite:///abs/or/rel/path.db    → SQLite file
    postgresql://user:pw@host/db    → Postgres
"""
import hashlib
import sqlite3
import threading
from contextlib import contextmanager


def detect_dialect(url):
    u = (url or "").lower()
    return "postgres" if (u.startswith("postgres://") or u.startswith("postgresql://")) else "sqlite"


def _sqlite_path(url):
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///"):]
    if url.startswith("sqlite://"):
        return url[len("sqlite://"):]
    return url


def _advisory_key(key):
    """Stable signed 64-bit int for pg_advisory_xact_lock (Python's hash() is salted, so don't use it)."""
    return int.from_bytes(hashlib.sha256(str(key).encode()).digest()[:8], "big", signed=True)


def ensure(conn_or_db):
    """Return a Database. Passes a Database through; wraps a raw DBAPI connection (assumed SQLite) so
    modules constructed with a bare sqlite3 connection (e.g. in tests) keep working."""
    return conn_or_db if isinstance(conn_or_db, Database) else Database.from_connection(conn_or_db)


class Database:
    """Thin DBAPI wrapper that speaks both dialects. Vayl's SQL is written with `?` placeholders and
    SQLite DDL; this translates for Postgres at execute/DDL time."""
    def __init__(self, url="vayl.db"):
        self.url = url
        self.dialect = detect_dialect(url)
        self._locks = {}                         # per-space in-process locks (SQLite, single node)
        self._locks_guard = threading.Lock()
        # Connections are THREAD-LOCAL. A single shared connection is not safe for concurrent use —
        # psycopg raises and sqlite3 throws "bad parameter or other API misuse" (the load test hit
        # exactly this). The process-global lock used to serialise every op so one connection was
        # enough; giving each thread its own connection is what lets that lock be dropped in favour
        # of per-space locking. WAL + a busy timeout let SQLite serve concurrent readers with one
        # writer. `_fixed` holds an externally-supplied connection (from_connection, single-threaded).
        self._tl = threading.local()
        self._fixed = None
        self.conn.cursor()                       # open the constructing thread's connection eagerly

    @classmethod
    def from_connection(cls, conn, dialect="sqlite"):
        """Wrap an already-open DBAPI connection (used by tests that pass a raw sqlite3 connection).
        Single-threaded by contract — the one connection is shared, not thread-local."""
        self = cls.__new__(cls)
        self.url = None
        self.dialect = dialect
        self._locks = {}
        self._locks_guard = threading.Lock()
        self._tl = threading.local()
        self._fixed = conn
        return self

    @property
    def conn(self):
        """This thread's connection, created on first use. One per thread, so concurrent threads
        never share a DBAPI connection."""
        if self._fixed is not None:
            return self._fixed
        c = getattr(self._tl, "conn", None)
        if c is None:
            c = self._new_conn()
            self._tl.conn = c
        return c

    def _new_conn(self):
        if self.dialect == "postgres":
            import psycopg  # only imported when actually using Postgres
            # autocommit is essential: without it psycopg opens a transaction on the first statement
            # and holds it (with its lock) until an explicit commit, and Vayl's reads never commit —
            # the connection would sit "idle in transaction" and block the next ALTER TABLE forever.
            # Multi-statement writes get atomicity from transaction() instead.
            return psycopg.connect(self.url, autocommit=True)
        conn = sqlite3.connect(_sqlite_path(self.url), check_same_thread=False)
        try:  # WAL: concurrent readers alongside one writer; busy_timeout: wait, don't error, on contention
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except Exception:
            pass                                 # :memory: or a read-only fs — degrade, don't crash
        return conn

    def _translate(self, sql):
        # SQLite uses `?`; psycopg uses `%s`. Vayl's queries contain `?` only as placeholders (never in
        # string literals) and no `%` literals, so a direct swap is safe. Keep it that way.
        return sql if self.dialect == "sqlite" else sql.replace("?", "%s")

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(self._translate(sql), params)
        return cur

    def executemany(self, sql, params):
        cur = self.conn.cursor()
        cur.executemany(self._translate(sql), params)
        return cur

    def commit(self):
        # A per-method commit is a no-op when we are already inside a transaction() block (e.g. a
        # write running under space_lock): that outer block owns the commit, and psycopg forbids an
        # explicit one within it. Swallowing that specific case keeps every write method callable
        # both standalone and nested, without threading transaction state through each of them.
        try:
            self.conn.commit()
        except Exception as e:                       # noqa: BLE001
            if self.dialect == "postgres" and "within a Transaction" in str(e):
                return
            raise

    def rollback(self):
        self.conn.rollback()

    @contextmanager
    def transaction(self):
        """Atomic block for a multi-statement write. On Postgres (autocommit on) this opens a real
        transaction via psycopg; on SQLite it commits on success and rolls back on error. Single
        statements do not need it — they are atomic on their own under both backends."""
        if self.dialect == "postgres":
            with self.conn.transaction():
                yield
        else:
            try:
                yield
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def autoincrement_pk(self):
        return "INTEGER PRIMARY KEY AUTOINCREMENT" if self.dialect == "sqlite" else "BIGSERIAL PRIMARY KEY"

    def add_column_if_missing(self, table, coldef):
        """Idempotent `ALTER TABLE … ADD COLUMN` across engines. Postgres has IF NOT EXISTS; SQLite
        doesn't, so we try and swallow the 'duplicate column' error (a failed statement doesn't abort
        a SQLite transaction the way it aborts a Postgres one, so try/except is safe there)."""
        if self.dialect == "postgres":
            self.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {coldef}")
        else:
            try:
                self.execute(f"ALTER TABLE {table} ADD COLUMN {coldef}")
            except sqlite3.OperationalError:
                pass

    def insert_returning(self, sql, params=(), col="id"):
        """INSERT and return the generated key. Uses RETURNING (supported by SQLite ≥3.35 and Postgres),
        so it works on both without relying on lastrowid (which psycopg doesn't populate)."""
        return self.execute(sql + f" RETURNING {col}", params).fetchone()[0]

    @contextmanager
    def space_lock(self, key):
        """Serialize writers to one memory space. SQLite: an in-process lock per key (single node is
        the boundary). Postgres: a transaction-scoped advisory lock that holds ACROSS processes/nodes.

        The lock is acquired INSIDE an explicit transaction so it is held for the whole block — a
        pg_advisory_xact_lock lives only as long as its transaction, and under the connection's
        autocommit a bare lock statement would commit and release immediately, serializing nothing.
        The transaction commits on success (releasing the lock) or rolls back on error. The block's
        own write (save/delete) nests as a savepoint, which is fine."""
        if self.dialect == "postgres":
            with self.conn.transaction():
                self.execute("SELECT pg_advisory_xact_lock(?)", (_advisory_key(key),))
                yield
        else:
            with self._locks_guard:
                lock = self._locks.setdefault(str(key), threading.Lock())
            with lock:
                yield
