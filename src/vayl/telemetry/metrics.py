#!/usr/bin/env python3
"""
Lightweight, on-device metrics for Vayl — nothing leaves the machine.

Tracks the KPIs that actually matter for a memory server:
  • per-tool call counts, average latency, and error counts (operational health), and
  • the distribution of reconciliation ACTIONS the engine takes — SUPERSEDE / RETRACT /
    FLAG / SKIP / ADD / … — which is a *product* signal (e.g. if FLAG never fires the
    honesty gate is miscalibrated; if SKIP dominates, extraction is failing).

Counters persist in the same SQLite DB (a `metrics` table), so they survive restarts,
and are surfaced to the client via the `stats` MCP tool.
"""


class Metrics:
    def __init__(self, db, crypter=None):
        from vayl.storage.db import ensure
        self.db = ensure(db)  # shares the Store's Database (one backend, one lifecycle)
        self.crypter = crypter  # error messages can echo memory content → encrypt them at rest
        self.db.execute("CREATE TABLE IF NOT EXISTS metrics(key TEXT PRIMARY KEY, value REAL NOT NULL DEFAULT 0)")
        # explicit id PK (Postgres has no rowid); on SQLite it aliases rowid, so ordering is unchanged.
        self.db.execute(
            f"CREATE TABLE IF NOT EXISTS metric_errors(id {self.db.autoincrement_pk()}, "
            "tool TEXT, etype TEXT, emsg TEXT)")
        self.db.commit()

    def _bump(self, key, by=1.0):
        # `metrics.value` (table-qualified) on the RHS: Postgres treats a bare `value` here as ambiguous
        # (target vs. excluded); qualifying resolves it, and SQLite accepts the same form.
        self.db.execute(
            "INSERT INTO metrics(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = metrics.value + excluded.value", (key, by))

    def record_call(self, tool, ms, ok=True):
        """One tool invocation: bump its count, add its latency, and (if it failed) its error count."""
        self._bump(f"tool.{tool}.calls")
        self._bump(f"tool.{tool}.ms_sum", ms)
        if not ok:
            self._bump(f"tool.{tool}.errors")
        self.db.commit()

    def record_actions(self, actions):
        """Tally the reconciliation actions produced by a remember/forget call (Action.value strings)."""
        for a in actions:
            self._bump(f"action.{a}")
        self.db.commit()

    def record_error(self, tool, etype, emsg):
        """Keep the last ~50 tool errors (type + message) so failures are debuggable, not swallowed.
        Exception text can embed memory content (an LLM/HTTP error often echoes the prompt), so the
        message is encrypted at rest like audit detail."""
        emsg = str(emsg)[:300]
        if self.crypter:
            emsg = self.crypter.enc(emsg)
        self.db.execute("INSERT INTO metric_errors(tool, etype, emsg) VALUES(?,?,?)",
                        (tool, etype, emsg))
        self.db.execute("DELETE FROM metric_errors WHERE id NOT IN "
                        "(SELECT id FROM metric_errors ORDER BY id DESC LIMIT 50)")
        self.db.commit()

    def _demsg(self, m):
        if self.crypter and m:
            try:
                return self.crypter.dec(m)
            except Exception:
                return m   # legacy plaintext row (pre-encryption) — return as stored
        return m

    def recent_errors(self, limit=5):
        return [{"tool": t, "type": et, "msg": self._demsg(em)} for t, et, em in self.db.execute(
            "SELECT tool, etype, emsg FROM metric_errors ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()]

    def snapshot(self):
        """Structured view: {'tools': {name: {calls, errors, avg_ms}}, 'actions': {name: count}}."""
        rows = dict(self.db.execute("SELECT key, value FROM metrics").fetchall())
        raw_tools, actions = {}, {}
        for key, val in rows.items():
            part = key.split(".")
            if part[0] == "tool" and len(part) == 3:
                raw_tools.setdefault(part[1], {})[part[2]] = val
            elif part[0] == "action" and len(part) == 2:
                actions[part[1]] = int(val)
        tools = {}
        for name, d in raw_tools.items():
            calls = int(d.get("calls", 0))
            tools[name] = {
                "calls": calls,
                "errors": int(d.get("errors", 0)),
                "avg_ms": round(d.get("ms_sum", 0.0) / calls, 1) if calls else 0.0,
            }
        return {"tools": tools, "actions": actions}
