"""
LOCOMO benchmark for Vayl
=========================

Ingest -> Search -> Evaluate, the same three stages mem0ai/memory-benchmarks runs
(Apache-2.0, see NOTICE), pointed at Vayl instead of Mem0.

    1. Ingest   conversation turns are chunked and fed to Vayl, which extracts facts and
                reconciles each against what it already believes
    2. Search   each question ranks the stored facts; the top-k are kept
    3. Evaluate an answerer LLM writes an answer from those facts; a judge LLM scores it

Held fixed against upstream so the parity number is comparable: the dataset, the chunk
size, the answerer prompt, the judge prompt, the retrieval cutoffs, and the definition of
"correct" (score >= 0.5). What differs is the memory system under test — which is the
point — and two additions that are reported separately and never folded into the parity
number: LOCOMO's adversarial category, and a strict judge that can see staleness. See
benchmarks/locomo/README.md.

Usage
-----
    # smoke: one conversation, deepest cutoff only
    python -m benchmarks.locomo.run --project-name smoke --conversations 0-0 --cutoffs 200

    # parity run: upstream's exact configuration
    python -m benchmarks.locomo.run --project-name parity --parity

    # full run: both judges, all five categories
    python -m benchmarks.locomo.run --project-name full --judge-mode both
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmarks.common.llm_client import LLMClient
from benchmarks.common.metrics import compute_overall_metrics, compute_staleness
from benchmarks.common.schema import RunConfig, metrics_to_dict
from benchmarks.common.vayl_client import VaylClient, default_db_path
from benchmarks.locomo.prompts import (
    CATEGORY_NAMES,
    DEFAULT_CATEGORIES,
    JUDGE_SYSTEM_PROMPT,
    PARITY_CATEGORIES,
    get_answer_prompt,
    get_judge_prompt,
    preprocess_answer,
)

DATASET_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
DEFAULT_DATASET_DIR = "benchmarks/datasets/locomo"
DATASET_FILE = "locomo10.json"
CHUNK_SIZE = 1              # turns per ingestion call — upstream's value, kept for parity

ANSWER_RE = re.compile(r"ANSWER:\s*(.*)", re.S)


# ===============================================================================
# DATASET
# ===============================================================================

def download_dataset(dataset_dir: str) -> str:
    """Fetch locomo10.json from its original authors if we don't have it."""
    path = os.path.join(dataset_dir, DATASET_FILE)
    if os.path.exists(path):
        return path
    os.makedirs(dataset_dir, exist_ok=True)
    import urllib.request
    print(f"Downloading LOCOMO-10 from {DATASET_URL}")
    urllib.request.urlretrieve(DATASET_URL, path)  # noqa: S310 - fixed https URL
    print(f"  saved to {path}")
    return path


def dataset_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_dataset(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ===============================================================================
# CONVERSATION PARSING  (field names and formats follow the dataset, as upstream does)
# ===============================================================================

def parse_locomo_date(date_str: str) -> datetime | None:
    """LOCOMO dates look like '1:56 pm on 8 May, 2023'."""
    for fmt in ("%I:%M %p on %d %B, %Y", "%I:%M %p on %d %b, %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def locomo_date_to_epoch(date_str: str) -> int | None:
    parsed = parse_locomo_date(date_str)
    return int(parsed.replace(tzinfo=timezone.utc).timestamp()) if parsed else None


def get_sorted_sessions(conversation: dict) -> list[tuple[str, str, list[dict]]]:
    """Sessions in chronological order — memory only reconciles correctly in time order."""
    paired = []
    for key in [k for k in conversation if re.match(r"^session_\d+$", k)]:
        paired.append((key, conversation.get(f"{key}_date_time", ""), conversation[key]))

    def sort_key(item: tuple) -> tuple:
        parsed = parse_locomo_date(item[1])
        if parsed:
            return (0, parsed)
        return (1, datetime(2000, 1, int(re.search(r"\d+", item[0]).group())))

    paired.sort(key=sort_key)
    return paired


def session_to_chunks(turns: list[dict], speaker_a: str, chunk_size: int) -> list[list[dict]]:
    """Turns -> message chunks. Image turns are rendered the way upstream renders them,
    so both systems ingest the same text."""
    messages = []
    for turn in turns:
        speaker = turn.get("speaker", "")
        text = turn.get("text", "")
        blip, query = turn.get("blip_caption", ""), turn.get("query", "")
        if query and blip:
            tag = f"[Sharing image - query: {query}. The image shows: {blip}]"
        elif query:
            tag = f"[Sharing image - query for: {query}]"
        elif blip:
            tag = f"[Sharing image that shows: {blip}]"
        else:
            tag = ""
        if tag:
            text = f"{text} {tag}" if text else tag
        if not text:
            continue
        messages.append({
            "role": "user" if speaker == speaker_a else "assistant",
            "speaker": speaker,
            "content": text,
        })
    return [messages[i:i + chunk_size] for i in range(0, len(messages), chunk_size)]


# ===============================================================================
# STAGE 1 — INGEST
# ===============================================================================

async def ingest_conversation(conv_idx: int, conversation: dict, client: VaylClient,
                              chunk_size: int, verbose: bool, max_chunks: int = 0) -> dict:
    user_id = f"locomo_conv_{conv_idx}"
    speaker_a = conversation["speaker_a"]
    sessions = get_sorted_sessions(conversation)

    all_chunks: list[tuple[list[dict], int | None, str]] = []
    for _key, date_str, turns in sessions:
        epoch = locomo_date_to_epoch(date_str)
        for chunk in session_to_chunks(turns, speaker_a, chunk_size):
            all_chunks.append((chunk, epoch, date_str))

    if max_chunks:
        all_chunks = all_chunks[:max_chunks]

    t0 = time.perf_counter()
    facts = 0
    failed = 0
    consecutive = 0
    for i, (chunk, epoch, date_str) in enumerate(all_chunks, 1):
        try:
            res = await client.add(chunk, user_id, timestamp=epoch, date_str=date_str)
            facts += res.get("facts", 0)
            consecutive = 0
        except Exception as exc:                              # noqa: BLE001
            failed += 1
            consecutive += 1
            print(f"    ! chunk {i} failed: {exc}", file=sys.stderr)
            # A skipped chunk is a hole in the memory, and a memory with holes scores badly for
            # reasons that have nothing to do with the system under test. Silently continuing
            # produces a plausible-looking number from corrupted state — the worst outcome. A
            # transient blip (a few retries away) is tolerable; a sustained outage is not.
            if consecutive >= 10:
                raise RuntimeError(
                    f"ingest aborted: {consecutive} consecutive chunk failures at chunk {i}. "
                    f"The memory would have holes and any score from it would be meaningless. "
                    f"Last error: {exc}") from exc
        if verbose and i % 25 == 0:
            print(f"    conv {conv_idx}: {i}/{len(all_chunks)} chunks, {facts} facts",
                  flush=True)

    elapsed = time.perf_counter() - t0
    stats = client.stats(user_id)
    rate = failed / len(all_chunks) * 100 if all_chunks else 0.0
    if rate > 2.0:
        raise RuntimeError(
            f"ingest aborted: {failed}/{len(all_chunks)} chunks failed ({rate:.1f}%). "
            f"Above 2% the memory is materially incomplete and a score from it would not "
            f"measure the system under test.")
    note = f"  [{failed} chunk(s) failed — {rate:.1f}%]" if failed else ""
    print(f"  conv {conv_idx}: {len(all_chunks)} chunks -> "
          f"{stats['stored']} stored / {stats['current']} current "
          f"({stats['retired']} retired) in {elapsed:.0f}s{note}", flush=True)
    return {"user_id": user_id, "chunks": len(all_chunks), "elapsed_s": elapsed,
            "failed_chunks": failed, "failed_pct": round(rate, 2), **stats}


# ===============================================================================
# STAGE 2+3 — SEARCH, ANSWER, JUDGE
# ===============================================================================

def format_memories(results: list[dict], limit: int) -> str:
    """Render retrieved facts for the answerer, status tag included.

    The tag is Vayl's actual output and withholding it would misrepresent the system. It is
    also what the strict judge needs in order to distinguish "reported the old value" from
    "reported the old value and said it was old".
    """
    lines = []
    for r in results[:limit]:
        status = r.get("status", "current")
        lines.append(f"- [{status}] {r['memory']}")
    return "# Memories\n" + ("\n".join(lines) if lines else "(none)")


def extract_answer(raw: str) -> str:
    m = ANSWER_RE.search(raw or "")
    return (m.group(1) if m else (raw or "")).strip()


async def judge_one(judge: LLMClient, question: str, gold: str, response: str,
                    mode: str) -> dict:
    prompt = get_judge_prompt(question, gold, response, mode=mode)
    try:
        data = await judge.complete_json(JUDGE_SYSTEM_PROMPT, prompt, max_tokens=512)
    except Exception as exc:                                  # noqa: BLE001
        return {"judgment": "ERROR", "score": 0.0, "error": str(exc)}
    label = str(data.get("label", "")).upper()
    if label not in ("CORRECT", "WRONG"):
        return {"judgment": "ERROR", "score": 0.0, "error": f"unparseable label: {data!r}"}
    out = {
        "judgment": label,
        "score": 1.0 if label == "CORRECT" else 0.0,
        "reasoning": data.get("reasoning", ""),
    }
    if mode == "strict":
        out["stale"] = bool(data.get("stale", False))
        out["ambiguous"] = bool(data.get("ambiguous", False))
    return out


async def process_question(qa: dict, conv_idx: int, client: VaylClient,
                           answerer: LLMClient, judge: LLMClient, args: argparse.Namespace,
                           reference_date: str) -> dict:
    user_id = f"locomo_conv_{conv_idx}"
    category = qa["category"]
    question = qa["question"]
    gold = preprocess_answer(category, str(qa.get("answer", "")))

    t0 = time.perf_counter()
    results = await client.search(question, user_id, top_k=args.top_k)
    search_ms = (time.perf_counter() - t0) * 1000

    record: dict[str, Any] = {
        "conv_idx": conv_idx,
        "question": question,
        "gold_answer": gold,
        "category": category,
        "category_name": CATEGORY_NAMES.get(category, "unknown"),
        "search_ms": round(search_ms, 1),
        "retrieved": len(results),
        # Keep what was actually retrieved. Without it a wrong answer cannot be
        # attributed to extraction vs retrieval vs answering, which is the first
        # question asked of any bad score.
        "memories": [{k: r.get(k) for k in ("id", "memory", "status", "when")}
                     for r in results[:max(args.cutoffs)]],
        "cutoff_results": {},
    }

    allow_abstention = args.judge_mode != "parity"
    for cutoff in args.cutoffs:
        label = f"top_{cutoff}"
        memories = format_memories(results, cutoff)
        prompt = get_answer_prompt(memories, question, reference_date,
                                   allow_abstention=allow_abstention)
        try:
            raw = await answerer.complete(
                "You answer questions from retrieved memories.", prompt,
                max_tokens=args.answer_max_tokens)
            answer = extract_answer(raw)
        except Exception as exc:                              # noqa: BLE001
            record["cutoff_results"][label] = {
                "judgment": "ERROR", "score": 0.0, "error": f"answerer: {exc}"}
            continue

        entry: dict[str, Any] = {"answer": answer}
        modes = ["parity", "strict"] if args.judge_mode == "both" else [args.judge_mode]
        for mode in modes:
            verdict = await judge_one(judge, question, gold, answer, mode)
            if args.judge_mode == "both":
                entry[mode] = verdict
            else:
                entry.update(verdict)
        if args.judge_mode == "both":
            # The parity verdict is the headline so the number stays comparable; the strict
            # verdict rides alongside rather than replacing it.
            entry.update({k: v for k, v in entry["parity"].items()})
            entry["stale"] = entry["strict"].get("stale")
            entry["ambiguous"] = entry["strict"].get("ambiguous")
            entry["strict_judgment"] = entry["strict"].get("judgment")
            entry["strict_score"] = entry["strict"].get("score")
        record["cutoff_results"][label] = entry

    return record


# ===============================================================================
# REPORTING
# ===============================================================================

def display_results(metrics: Any, cutoffs: list[int], staleness: dict | None,
                    strict_metrics: Any = None) -> None:
    line = "=" * 74
    print(f"\n{line}\nLOCOMO — Vayl\n{line}")
    print(f"\nOverall (parity judge, top_{cutoffs[-1]}): "
          f"{metrics.overall_accuracy:.1f}%  ({metrics.correct}/{metrics.total})"
          + (f"   [{metrics.errors} errors]" if metrics.errors else ""))

    print("\nBy category:")
    print(f"  {'category':<16} {'accuracy':>10}  {'n':>6}")
    for name, g in metrics.by_group.items():
        print(f"  {name:<16} {g.accuracy:>9.1f}%  {g.correct:>3}/{g.total:<3}")

    if len(cutoffs) > 1:
        print("\nBy retrieval depth:")
        print(f"  {'cutoff':<10} {'accuracy':>10}  {'n':>6}")
        for label, cm in metrics.by_cutoff.items():
            o = cm.overall
            print(f"  {label:<10} {o['accuracy']:>9.1f}%  {o['correct']:>3}/{o['total']:<3}")

    if strict_metrics is not None:
        print(f"\nOverall (strict judge, top_{cutoffs[-1]}): "
              f"{strict_metrics.overall_accuracy:.1f}%  "
              f"({strict_metrics.correct}/{strict_metrics.total})")

    if staleness and staleness.get("judged"):
        print(f"\nStaleness (strict judge, {staleness['judged']} answers judged):")
        print(f"  presented a superseded value as current : "
              f"{staleness['stale']:>4} ({staleness['stale_rate']:.1f}%)")
        print(f"  gave multiple values without committing : "
              f"{staleness['ambiguous']:>4} ({staleness['ambiguous_rate']:.1f}%)")
    print(line)


def strict_view(evaluations: list[dict]) -> list[dict]:
    """Re-key a judge-mode=both result set so the strict verdict is the scored one."""
    out = []
    for e in evaluations:
        c = dict(e)
        c["cutoff_results"] = {
            k: {**v, "judgment": v.get("strict_judgment", v.get("judgment")),
                "score": v.get("strict_score", v.get("score", 0.0))}
            for k, v in e.get("cutoff_results", {}).items()
        }
        out.append(c)
    return out


# ===============================================================================
# CLI
# ===============================================================================

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run the LOCOMO benchmark against Vayl.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--project-name", required=True, help="Run identifier")
    p.add_argument("--dataset-path", default=None, help="Path to a local locomo10.json")
    p.add_argument("--output-dir", default="benchmarks/results/locomo")
    p.add_argument("--db-path", default=None, help="SQLite path (default: per project)")

    p.add_argument("--conversations", default=None,
                   help="Range like 0-4, or a single index. Default: all 10")
    p.add_argument("--categories", default=None,
                   help="Comma-separated category ids. Default: 1,2,3,4,5")
    p.add_argument("--parity", action="store_true",
                   help="Reproduce upstream exactly: categories 1-4, parity judge only")

    p.add_argument("--top-k", type=int, default=200, help="Memories retrieved per question")
    p.add_argument("--cutoffs", default="10,20,50,200", help="Evaluate at these depths")
    p.add_argument("--chunk-size", type=int, default=CHUNK_SIZE,
                   help="Turns per ingestion call; upstream uses 1. Raising it cuts ingest "
                        "cost proportionally but is a deviation")

    p.add_argument("--answerer-model", default="gpt-4o")
    p.add_argument("--judge-model", default="gpt-4o")
    p.add_argument("--judge-mode", choices=["parity", "strict", "both"], default="both")
    p.add_argument("--provider", default="openai")
    p.add_argument("--answer-max-tokens", type=int, default=1024)
    p.add_argument("--concurrency", type=int, default=8, help="Concurrent LLM calls")

    p.add_argument("--skip-ingest", action="store_true",
                   help="Reuse an already-populated database")
    p.add_argument("--reset", action="store_true", help="Delete the database before ingest")
    p.add_argument("--max-chunks", type=int, default=0,
                   help="Ingest at most N chunks per conversation. For validating a change in "
                        "minutes instead of re-running a 45-minute ingest; not a valid full result")
    p.add_argument("--max-questions", type=int, default=0,
                   help="Evaluate at most N questions per conversation (same purpose)")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def resolve_range(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(total))
    if "-" in spec:
        lo, hi = spec.split("-", 1)
        return list(range(int(lo), min(int(hi) + 1, total)))
    return [int(spec)]


async def async_main() -> int:
    args = parse_args()

    if args.parity:
        args.categories = ",".join(str(c) for c in PARITY_CATEGORIES)
        args.judge_mode = "parity"

    cutoffs = sorted({int(c) for c in args.cutoffs.split(",") if c.strip()})
    if not cutoffs:
        print("--cutoffs must list at least one depth", file=sys.stderr)
        return 2
    args.cutoffs = cutoffs
    categories = ([int(c) for c in args.categories.split(",")] if args.categories
                  else list(DEFAULT_CATEGORIES))

    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set — the answerer, judge, and Vayl's own extractor "
              "all need it.", file=sys.stderr)
        return 2

    dataset_path = args.dataset_path or download_dataset(DEFAULT_DATASET_DIR)
    data = load_dataset(dataset_path)
    conv_indices = resolve_range(args.conversations, len(data))

    db_path = args.db_path or default_db_path(args.project_name)
    if args.reset and os.path.exists(db_path):
        os.remove(db_path)
        for suffix in (".salt", ".salt.kdf"):
            Path(db_path + suffix).unlink(missing_ok=True)

    cfg = RunConfig(
        project_name=args.project_name,
        judge_mode=args.judge_mode,
        extraction_model=os.environ.get("OPENAI_MODEL", "gpt-5-mini"),
        read_model=os.environ.get("VAYL_READ_MODEL", ""),
        embed_model=os.environ.get("EMBED_MODEL", "text-embedding-3-small"),
        answerer_model=args.answerer_model,
        judge_model=args.judge_model,
        provider=args.provider,
        top_k=args.top_k,
        cutoffs=cutoffs,
        categories=categories,
        conversations=args.conversations or f"0-{len(data) - 1}",
        dataset_sha256=dataset_sha256(dataset_path),
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    print(f"LOCOMO / Vayl — project '{args.project_name}'")
    print(f"  conversations : {conv_indices}")
    print(f"  categories    : {categories}  ({', '.join(CATEGORY_NAMES[c] for c in categories)})")
    print(f"  judge mode    : {args.judge_mode}")
    print(f"  extraction    : {cfg.extraction_model}   answerer: {args.answerer_model}   "
          f"judge: {args.judge_model}")
    print(f"  cutoffs       : {cutoffs}   top_k: {args.top_k}   chunk: {args.chunk_size}")
    print(f"  database      : {db_path}\n")

    answerer = LLMClient(args.answerer_model, args.provider, concurrency=args.concurrency)
    judge = LLMClient(args.judge_model, args.provider, concurrency=args.concurrency)

    os.makedirs(args.output_dir, exist_ok=True)
    evaluations: list[dict] = []
    ingest_stats: list[dict] = []

    async with VaylClient(db_path) as client:
        # ── stage 1 ──
        if not args.skip_ingest:
            print("Ingesting")
            for idx in conv_indices:
                ingest_stats.append(
                    await ingest_conversation(idx, data[idx]["conversation"], client,
                                              args.chunk_size, args.verbose,
                                              args.max_chunks))
        else:
            print("Skipping ingest (--skip-ingest)")

        # ── stages 2+3 ──
        print("\nAnswering")
        for idx in conv_indices:
            entry = data[idx]
            sessions = get_sorted_sessions(entry["conversation"])
            reference_date = sessions[-1][1] if sessions else "2023"
            questions = [q for q in entry.get("qa", entry.get("qa_pairs", []))
                         if q.get("category") in categories]
            if args.max_questions:
                questions = questions[:args.max_questions]
            if not questions:
                continue
            tasks = [process_question(qa, idx, client, answerer, judge, args, reference_date)
                     for qa in questions]
            done = await asyncio.gather(*tasks, return_exceptions=True)
            ok = [d for d in done if isinstance(d, dict)]
            for d in done:
                if not isinstance(d, dict):
                    print(f"  ! question failed: {d}", file=sys.stderr)
            evaluations.extend(ok)
            print(f"  conv {idx}: {len(ok)}/{len(questions)} questions evaluated", flush=True)

    if not evaluations:
        print("No questions evaluated.", file=sys.stderr)
        return 1

    # ── metrics ──
    labels = [f"top_{c}" for c in cutoffs]
    metrics = compute_overall_metrics(evaluations, "category_name", labels)
    strict_metrics = None
    staleness = None
    if args.judge_mode in ("strict", "both"):
        staleness = compute_staleness(evaluations, labels[-1])
        if args.judge_mode == "both":
            strict_metrics = compute_overall_metrics(strict_view(evaluations),
                                                     "category_name", labels)

    cfg.finished_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "config": cfg.to_dict(),
        "ingest": ingest_stats,
        "metrics": metrics_to_dict(metrics),
        "strict_metrics": metrics_to_dict(strict_metrics) if strict_metrics else None,
        "staleness": staleness,
        "llm_calls": {"answerer": answerer.calls, "judge": judge.calls,
                      "answerer_rate_limited": answerer.rate_limited,
                      "judge_rate_limited": judge.rate_limited},
        "evaluations": evaluations,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = os.path.join(args.output_dir, f"locomo_{args.project_name}_{stamp}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    display_results(metrics, cutoffs, staleness, strict_metrics)
    print(f"\nWrote {out}")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
