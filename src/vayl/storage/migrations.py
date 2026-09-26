"""
Versioned schema migrations — one ordered list for the whole database, recorded in `schema_migrations`.

Every component (Store, Auth, Audit, Decisions, Receipts, Metrics) calls `migrate(db)` on construction,
so whichever opens the database first brings it up to date; the rest find nothing pending. Runs on
SQLite and Postgres, under a lock (a cross-process advisory lock on Postgres).

Rules for adding a migration — append `(next_version, "name", fn)` to MIGRATIONS, never edit or
reorder a shipped one:
  • Idempotent. On SQLite, DDL is not transactional, so a migration interrupted halfway must be safe
    to run again (IF NOT EXISTS, add_column_if_missing, UPDATE … WHERE not-yet-migrated).
  • Additive, so the PREVIOUS release keeps running against the upgraded schema. That is the rollback
    story: roll the code back, the data stays readable. A change that cannot be additive (rename, drop,
    rewrite) must say so in the CHANGELOG and requires a backup before upgrading.
  • An older Vayl refuses to start against a database migrated past what it knows (SchemaTooNew),
    rather than writing rows a newer schema doesn't expect.

Ops: `vayl-migrate status` shows the applied and pending versions; `vayl-migrate up` applies pending
migrations explicitly (e.g. once, before rolling out several server processes).
"""
import datetime
import os
import sys

from vayl.storage.db import Database, ensure


class SchemaTooNew(RuntimeError):
    pass


def _v1_baseline(db):
    """The schema as of 0.4.0, including the in-place upgrades earlier releases applied on startup, so
    a database created by any earlier version lands on the same schema."""
    pk = db.autoincrement_pk()
    # ── memory statements ──
    db.execute("""CREATE TABLE IF NOT EXISTS statements(
        user_id TEXT, id INTEGER, slot TEXT, subject TEXT, value TEXT, scope TEXT,
        status TEXT, supersedes INTEGER, confidence REAL, raw TEXT, seq INTEGER, embedding TEXT,
        agent_id TEXT DEFAULT '', run_id TEXT DEFAULT '', metadata TEXT, subject_hmac TEXT,
        created_at REAL, source TEXT, tenant_id TEXT DEFAULT 'default',
        head TEXT, relation TEXT, tail TEXT)""")
    for ddl in ("embedding TEXT", "agent_id TEXT DEFAULT ''", "run_id TEXT DEFAULT ''",
                "metadata TEXT", "subject_hmac TEXT", "created_at REAL", "source TEXT",
                "tenant_id TEXT DEFAULT 'default'", "head TEXT", "relation TEXT", "tail TEXT"):
        db.add_column_if_missing("statements", ddl)
    # Hot-path indexes (keep load() O(active), independent of history size). `idx_id` (space + id)
    # replaced the old space-only `idx_space`: it serves space lookups AND makes the per-space
    # `SELECT MAX(id)` seed in load() a covering reverse-seek (36ms → 0.01ms at 200k rows).
    db.execute("DROP INDEX IF EXISTS idx_space")
    db.execute("CREATE INDEX IF NOT EXISTS idx_id ON statements(tenant_id, user_id, agent_id, run_id, id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_subj ON statements(tenant_id, user_id, agent_id, run_id, subject_hmac)")
    # `status` in the key lets load()'s `status IN (ACTIVE, FLAGGED_CONFLICT) ORDER BY id` seek straight
    # to the hot rows (93ms → ~3ms at 200k history rows). A partial index would be smaller, but the
    # planner won't match it against the parameterized `IN (?, ?)`.
    db.execute("CREATE INDEX IF NOT EXISTS idx_hot ON statements(tenant_id, user_id, agent_id, run_id, status, id)")
    # per-space reconciliation policy for shared organizational memory
    db.execute("CREATE TABLE IF NOT EXISTS space_config("
               "user_id TEXT, agent_id TEXT, run_id TEXT, policy TEXT, "
               "tenant_id TEXT DEFAULT 'default', PRIMARY KEY(user_id, agent_id, run_id))")
    db.add_column_if_missing("space_config", "tenant_id TEXT DEFAULT 'default'")
    # ── principals (API keys + roles) ──
    db.execute("CREATE TABLE IF NOT EXISTS principals(id TEXT PRIMARY KEY, name TEXT, kind TEXT, "
               "api_key_hash TEXT UNIQUE, roles TEXT, disabled INTEGER DEFAULT 0, created_at REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_prin_key ON principals(api_key_hash)")
    # Added after the first release. Existing rows get NULL: scopes NULL reads as unrestricted and
    # tenant NULL as 'default', so an upgrade never locks an existing principal out.
    db.add_column_if_missing("principals", "scopes TEXT")
    db.add_column_if_missing("principals", "tenant TEXT")
    # ── tamper-evident audit chain ──
    db.execute(f"CREATE TABLE IF NOT EXISTS audit(seq {pk}, "
               "ts TEXT, user_id TEXT, agent_id TEXT, run_id TEXT, action TEXT, detail TEXT, "
               "prev_hash TEXT, entry_hash TEXT, signature TEXT)")
    # pre-chain rows keep NULL hash columns and are treated as legacy
    for ddl in ("prev_hash TEXT", "entry_hash TEXT", "signature TEXT"):
        db.add_column_if_missing("audit", ddl)
    # retention anchor: after a purge, the chain restarts from a SIGNED checkpoint instead of GENESIS
    db.execute("CREATE TABLE IF NOT EXISTS audit_meta(key TEXT PRIMARY KEY, value TEXT, signature TEXT)")
    # ── signed decisions and receipts ──
    db.execute(f"CREATE TABLE IF NOT EXISTS decisions(id {pk}, "
               "ts TEXT, user_id TEXT, agent_id TEXT, run_id TEXT, summary TEXT, snapshot TEXT, "
               "anchor TEXT, entry_hash TEXT, signature TEXT)")
    db.execute(f"CREATE TABLE IF NOT EXISTS receipts(id {pk}, "
               "ts TEXT, kind TEXT, payload TEXT, signature TEXT, public_key TEXT)")
    # ── operational metrics ──
    db.execute("CREATE TABLE IF NOT EXISTS metrics(key TEXT PRIMARY KEY, value REAL NOT NULL DEFAULT 0)")
    # explicit id PK (Postgres has no rowid); on SQLite it aliases rowid, so ordering is unchanged
    db.execute(f"CREATE TABLE IF NOT EXISTS metric_errors(id {pk}, tool TEXT, etype TEXT, emsg TEXT)")


MIGRATIONS = [
    (1, "baseline", _v1_baseline),
]
LATEST = MIGRATIONS[-1][0]
_LOCK = "vayl:schema-migrations"


def _applied(db):
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations("
               "version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)")
    return {r[0]: (r[1], r[2]) for r in db.execute(
        "SELECT version, name, applied_at FROM schema_migrations ORDER BY version")}


def _check_not_newer(applied):
    newest = max(applied, default=0)
    if newest > LATEST:
        raise SchemaTooNew(
            f"database schema is at version {newest}, but this Vayl only knows up to {LATEST}. "
            "It was upgraded by a newer Vayl: run that version, or restore the backup taken before "
            "the upgrade.")


def migrate(db):
    """Apply pending migrations in order; refuse a schema newer than this code. Returns the versions
    applied (empty when already current)."""
    db = ensure(db)
    if getattr(db, "_schema_current", False):
        return []
    done = []
    with db.space_lock(_LOCK):
        applied = _applied(db)
        _check_not_newer(applied)
        for version, name, fn in MIGRATIONS:
            if version in applied:
                continue
            with db.transaction():
                fn(db)
                db.execute("INSERT INTO schema_migrations(version, name, applied_at) VALUES (?,?,?) "
                           "ON CONFLICT(version) DO NOTHING",
                           (version, name, datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")))
            done.append(version)
        db.commit()
    db._schema_current = True
    return done


def status(db):
    """(applied {version: (name, applied_at)}, pending [(version, name)])."""
    db = ensure(db)
    applied = _applied(db)
    db.commit()
    return applied, [(v, n) for v, n, _ in MIGRATIONS if v not in applied]


def main(argv=None):
    """`vayl-migrate [status|up]` against VAYL_DATABASE_URL, else VAYL_DB (default vayl.db)."""
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "status"
    if cmd not in ("status", "up"):
        print("usage: vayl-migrate [status|up]", file=sys.stderr)
        return 2
    db = Database(os.environ.get("VAYL_DATABASE_URL") or os.path.expanduser(os.environ.get("VAYL_DB", "vayl.db")))
    try:
        if cmd == "up":
            ran = migrate(db)
            print(f"applied: {', '.join(map(str, ran))}" if ran else "nothing to apply")
        applied, pending = status(db)
        _check_not_newer(applied)
    except SchemaTooNew as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for v, (name, at) in applied.items():
        print(f"  v{v}  {name:<20} applied {at}")
    for v, name in pending:
        print(f"  v{v}  {name:<20} PENDING")
    print(f"schema: v{max(applied, default=0)} (this Vayl: v{LATEST})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
