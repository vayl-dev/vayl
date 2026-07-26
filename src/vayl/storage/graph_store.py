#!/usr/bin/env python3
"""
Neo4j graph projection for Vayl — the entity-graph memory (deep multi-hop / relational).

The graph is DERIVED: SQLite holds the facts, including each fact's (head, relation, tail), so the
graph can be dropped, migrated or lost and rebuilt with `Store.reproject_graph()`. That is what makes
it a projection rather than a second source of truth you cannot recover.
Facts are written as directed entity->relation->entity edges. Scales via:
  - a full-text index on entity names (O(log N) seeding), and
  - **edge embeddings stored on write + a native Neo4j VECTOR INDEX**, so a query ranks the most
    relevant edges INSIDE the database (sub-second, no per-query embedding, no hub cap).
The Python-side neighborhood+rank path remains as a fallback when embeddings aren't available.
"""
import re

from vayl.memory.llm_memory import _embed


class Neo4jGraph:
    def __init__(self, uri="bolt://localhost:7687", user="neo4j", pw="testpass123"):
        # neo4j is an optional [graph] dependency — import it only when a graph is actually
        # instantiated, so `import graph_store` works in a slot-only install (and in CI).
        from neo4j import GraphDatabase
        self.driver = GraphDatabase.driver(uri, auth=(user, pw))
        self._vec_dim = None
        with self.driver.session() as s:
            # Exact-lookup index on the entity key. Without it `MERGE (h:Entity {name:…})` — which
            # every add_edge runs twice — compiles to NodeByLabelScan, so ingestion degrades
            # quadratically as the graph grows (a full-text index cannot serve an exact MERGE, and is
            # not a substitute). Uniqueness matches the semantics MERGE already assumes: one node per
            # entity name.
            try:
                s.run("CREATE CONSTRAINT entity_name IF NOT EXISTS "
                      "FOR (n:Entity) REQUIRE n.name IS UNIQUE")
            except Exception:
                pass
            try:
                s.run("CREATE FULLTEXT INDEX entity_names IF NOT EXISTS FOR (n:Entity) ON EACH [n.name]")
            except Exception:
                pass

    def close(self): self.driver.close()
    def wipe(self):
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")

    def _ensure_vector_index(self, dim):
        if self._vec_dim:
            return
        with self.driver.session() as s:
            s.run("CREATE VECTOR INDEX edge_emb IF NOT EXISTS FOR ()-[r:REL]-() ON (r.emb) "
                  "OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: 'cosine'}}",
                  dim=dim)
        self._vec_dim = dim

    def add_edge(self, head, relation, tail, source="", ns="", subject="", functional=False):
        # ns = (user/agent/run) namespace, subject = slot key — stamped so erasure can scope
        # per-user/per-subject. ns is part of the MERGE key so different users' identical
        # triples stay distinct edges (not merged into one).
        # functional=True: this relation holds ONE tail per (head, ns) — a re-point (routes_to,
        # reports_to, located_in…). Retire any valid edge to a DIFFERENT tail first, so a change that
        # the extractor labelled ADD (rather than SUPERSEDE) can't leave two live edges. Additive
        # relations (depends_on, member_of…) pass functional=False and coexist as before.
        emb = self._embed_edge(head, relation, tail)
        with self.driver.session() as s:
            s.execute_write(self._write_edge, head, relation, tail, source, ns, subject, emb, functional)

    def _embed_edge(self, head, relation, tail):
        """Embed (and ensure the index) BEFORE opening a write transaction — creating an index
        inside one is not permitted, and the embed is a network call we do not want to hold a
        transaction open across."""
        try:
            emb = _embed([f"{head} {relation} {tail}"])[0]
            self._ensure_vector_index(len(emb))
            return emb
        except Exception:
            return None

    @staticmethod
    def _write_edge(tx, head, relation, tail, source, ns, subject, emb, functional=False):
        if functional:
            tx.run("MATCH (:Entity {name:$head})-[r:REL {type:$rel, ns:$ns}]->(o:Entity) "
                   "WHERE o.name <> $tail AND r.valid SET r.valid=false",
                   head=head, rel=relation, ns=ns, tail=tail)
        q = ("MERGE (h:Entity {name:$head}) MERGE (t:Entity {name:$tail}) "
             "MERGE (h)-[r:REL {type:$rel, ns:$ns}]->(t) SET r.valid=true, r.source=$src, r.subject=$subject")
        p = dict(head=head, tail=tail, rel=relation, src=source, ns=ns, subject=subject)
        if emb is not None:
            q += ", r.emb=$emb"; p["emb"] = emb
        tx.run(q, **p)

    def supersede_edge(self, head, relation, new_tail, source="", ns="", subject=""):
        """Retire the old tail and write the new one in ONE transaction. Split across two, a failure
        between them leaves the slot invalidated with no replacement — the fact vanishes from the
        graph, and (since the projection cannot be rebuilt) does not come back."""
        emb = self._embed_edge(head, relation, new_tail)

        def work(tx):
            tx.run("MATCH (h:Entity {name:$head})-[r:REL {type:$rel, ns:$ns}]->() SET r.valid=false",
                   head=head, rel=relation, ns=ns)
            self._write_edge(tx, head, relation, new_tail, source, ns, subject, emb)
        with self.driver.session() as s:
            s.execute_write(work)

    def retract_edge(self, head, relation, ns=""):
        with self.driver.session() as s:
            s.run("MATCH (h:Entity {name:$head})-[r:REL {type:$rel, ns:$ns}]->() SET r.valid=false",
                  head=head, rel=relation, ns=ns)

    def head_for_subject(self, subject, ns=""):
        """The head entity this slot's edges are already attached to, or None.

        Entity resolution, cheaply: the extractor is not always consistent about naming the same
        entity ("API Gateway" one turn, "Org" the next), which strands the new edge on a fresh node
        where a query about the original entity can never reach it. The slot `subject` is stable
        across those turns, so it is a reliable anchor for choosing the canonical head."""
        if not subject:
            return None
        with self.driver.session() as s:
            rec = s.run("MATCH (h:Entity)-[r:REL {ns:$ns, subject:$subject}]->() "
                        "RETURN h.name AS name ORDER BY r.valid DESC LIMIT 1",
                        ns=ns, subject=subject).single()
            return rec["name"] if rec else None

    def retire_subject_edges(self, subject, ns=""):
        """Invalidate the edges projected from a slot the store just superseded/retracted, keyed by
        (subject, ns) — NOT by head. Keeps the graph in step with the active slot set even when the
        superseding fact named a different head entity (or carried no graph triple at all), which is
        exactly how a stale edge would otherwise linger and be returned as current."""
        if not subject:
            return
        with self.driver.session() as s:
            s.run("MATCH ()-[r:REL {ns:$ns, subject:$subject}]->() SET r.valid=false",
                  ns=ns, subject=subject)

    def delete_edges(self, ns=None, ns_prefix=None, subject=None):
        """HARD-erase edges (and any orphaned entity nodes) for GDPR erasure — scoped by namespace
        (exact `ns` or `ns_prefix` for a whole user), optionally narrowed to one `subject`."""
        conds, p = [], {}
        if ns is not None:
            conds.append("r.ns = $ns"); p["ns"] = ns
        if ns_prefix is not None:
            conds.append("r.ns STARTS WITH $pfx"); p["pfx"] = ns_prefix
        if subject is not None:
            conds.append("r.subject = $subject"); p["subject"] = subject
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        with self.driver.session() as s:
            s.run(f"MATCH ()-[r:REL]->(){where} DELETE r", **p)
            s.run("MATCH (n:Entity) WHERE NOT (n)--() DELETE n")   # sweep orphaned nodes

    def embed_all_edges(self, batch=256):
        """Backfill embeddings for edges written without them (e.g. bulk loads), in batches."""
        with self.driver.session() as s:
            rows = [dict(rid=r["rid"], h=r["h"], rel=r["rel"], t=r["t"]) for r in s.run(
                "MATCH (h)-[r:REL]->(t) WHERE r.emb IS NULL "
                "RETURN elementId(r) AS rid, h.name AS h, r.type AS rel, t.name AS t")]
        for i in range(0, len(rows), batch):
            chunk = rows[i:i + batch]
            vecs = _embed([f"{x['h']} {x['rel']} {x['t']}" for x in chunk])
            self._ensure_vector_index(len(vecs[0]))
            with self.driver.session() as s:
                s.run("UNWIND $rows AS row MATCH ()-[r:REL]->() WHERE elementId(r)=row.rid SET r.emb=row.emb",
                      rows=[{"rid": x["rid"], "emb": v} for x, v in zip(chunk, vecs)])
        with self.driver.session() as s:
            try: s.run("CALL db.awaitIndexes()")   # let the vector index catch up before querying
            except Exception: pass
        return len(rows)

    def vector_search(self, qvec, k=15, ns=None):
        """Top-k most relevant VALID edges, ranked by the in-DB vector index. Sub-second, no hub cap.
        Pass `ns` to scope to one (user/agent/run) namespace — otherwise the search spans all edges."""
        with self.driver.session() as s:
            res = s.run(
                "CALL db.index.vector.queryRelationships('edge_emb', $probe, $qv) YIELD relationship AS r, score "
                "WHERE r.valid AND ($ns IS NULL OR r.ns = $ns) "
                "RETURN startNode(r).name AS head, r.type AS rel, endNode(r).name AS tail "
                "ORDER BY score DESC LIMIT $k", probe=k * 4, qv=list(qvec), k=int(k), ns=ns)
            return [(r["head"], r["rel"], r["tail"]) for r in res]

    def search_entities(self, text, limit=8, ns=None):
        """Seed entities for a traversal, restricted to entities THIS tenant actually has edges for.

        The full-text index covers every `:Entity` node in the database, which is wider than it
        should be twice over: entity nodes are not namespaced (only edges carry `ns`), and the label
        is shared with anything else writing `:Entity` into the same Neo4j. Requiring a Vayl `:REL`
        edge in the caller's namespace keeps another tenant's — or another tool's — entity names out
        of the seed set."""
        terms = [w for w in re.findall(r"[A-Za-z0-9_]+", text) if len(w) > 2]
        if not terms:
            return []
        with self.driver.session() as s:
            try:
                res = s.run("CALL db.index.fulltext.queryNodes('entity_names', $q) YIELD node, score "
                            "WHERE EXISTS { MATCH (node)-[r:REL]-() WHERE $ns IS NULL OR r.ns = $ns } "
                            "RETURN node.name AS name ORDER BY score DESC LIMIT $lim",
                            q=" OR ".join(terms), lim=limit, ns=ns)
                return [r["name"] for r in res]
            except Exception:
                return []

    def neighborhood(self, entities, hops=2, valid_only=True, limit=800, ns=None):
        conds = []
        if valid_only:
            conds.append("all(rel IN relationships(p) WHERE rel.valid)")
        if ns is not None:
            conds.append("all(rel IN relationships(p) WHERE rel.ns = $ns)")   # scope traversal to one tenant
        cond = ("AND " + " AND ".join(conds)) if conds else ""
        # Match the seed on the indexed property directly. Wrapping it in toLower() made the predicate
        # non-indexable, so every relational query paid a full :Entity scan — measurably linear in
        # graph size. Seeds always come back from the database itself (search_entities returns
        # node.name, the fallback reads names off all_edges), so they are already exact.
        names = list(entities)
        cypher = (f"MATCH p=(e:Entity)-[:REL*1..{int(hops)}]-(m) WHERE e.name IN $names {cond} "
                  "UNWIND relationships(p) AS r WITH startNode(r) AS h, r AS r, endNode(r) AS t, length(p) AS d "
                  "RETURN DISTINCT h.name AS head, r.type AS rel, t.name AS tail, r.valid AS valid, min(d) AS dist "
                  "ORDER BY dist ASC LIMIT $lim")
        with self.driver.session() as s:
            return [(r["head"], r["rel"], r["tail"], r["valid"])
                    for r in s.run(cypher, names=names, lim=int(limit), ns=ns)]

    def all_edges(self, valid_only=True, limit=1000, ns=None):
        conds = []
        if valid_only:
            conds.append("r.valid")
        if ns is not None:
            conds.append("r.ns = $ns")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        with self.driver.session() as s:
            return [(r["h"], r["type"], r["t"], r["valid"]) for r in s.run(
                f"MATCH (h:Entity)-[r:REL]->(t:Entity) {where} "
                "RETURN h.name AS h, r.type AS type, t.name AS t, r.valid AS valid LIMIT $lim",
                lim=int(limit), ns=ns)]
