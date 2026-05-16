"""Effective context window evaluator.

This script measures practical usable context length (quality degradation), not just
hard limit. It calls the model directly, so project-level compression/sliding-window
mechanisms are bypassed.

Method:
1. Build contexts at multiple target lengths.
2. Inject three anchor facts at START/MIDDLE/END.
3. Ask model to return all three anchors in strict JSON.
4. Score exact match accuracy by position and overall.

Example:
    venv\\Scripts\\python.exe eval\\context_effective_window_eval.py \\
        --model qwen2.5-7b-instruct-1m \\
        --lengths 2000,8000,16000,32000,64000 \\
        --cases-per-length 8 \\
        --request-timeout 120 \\
        --out eval\\effective_window_report.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import string
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from langchain_openai import ChatOpenAI


@dataclass
class CaseResult:
    length_target: int
    case_id: int
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    latency_ms: int
    ok: bool
    error: Optional[str]
    start_correct: int
    middle_correct: int
    end_correct: int
    overall_correct: int
    parsed_start: Optional[str]
    parsed_middle: Optional[str]
    parsed_end: Optional[str]
    raw_answer_preview: Optional[str]


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    def resolve_env(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: resolve_env(x) for k, x in v.items()}
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            return os.getenv(v[2:-1], v)
        return v

    return resolve_env(cfg)


def extract_usage(resp: Any) -> Dict[str, Optional[int]]:
    usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}

    usage_meta = getattr(resp, "usage_metadata", None)
    if isinstance(usage_meta, dict):
        usage["input_tokens"] = usage_meta.get("input_tokens")
        usage["output_tokens"] = usage_meta.get("output_tokens")
        usage["total_tokens"] = usage_meta.get("total_tokens")

    resp_meta = getattr(resp, "response_metadata", None)
    if isinstance(resp_meta, dict):
        token_usage = resp_meta.get("token_usage", {}) if isinstance(resp_meta.get("token_usage"), dict) else {}
        if usage["input_tokens"] is None:
            usage["input_tokens"] = token_usage.get("prompt_tokens")
        if usage["output_tokens"] is None:
            usage["output_tokens"] = token_usage.get("completion_tokens")
        if usage["total_tokens"] is None:
            usage["total_tokens"] = token_usage.get("total_tokens")

    return usage


def rand_token(k: int = 10) -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choice(chars) for _ in range(k))


def make_anchor_triplet() -> Dict[str, str]:
    return {
        "start": f"START_{rand_token(8)}",
        "middle": f"MIDDLE_{rand_token(8)}",
        "end": f"END_{rand_token(8)}",
    }


def filler_block() -> str:
    return (
        "这是用于上下文记忆测试的中性文本，不包含关键答案。"
        "请忽略这段文字的语义，只关注显式标注的锚点信息。"
    )


def build_context(target_tokens: int, anchors: Dict[str, str]) -> str:
    # This target is synthetic. Actual token count is from API usage.
    base = [
        f"[ANCHOR_START] key=start value={anchors['start']}",
        "\n" + (filler_block() * max(10, target_tokens // 80)),
        f"\n[ANCHOR_MIDDLE] key=middle value={anchors['middle']}",
        "\n" + (filler_block() * max(10, target_tokens // 80)),
        f"\n[ANCHOR_END] key=end value={anchors['end']}",
    ]
    return "".join(base)


def build_prompt(context_text: str) -> str:
    return (
        "你将收到一段较长上下文。请提取三个锚点值并严格按 JSON 返回。\n"
        "仅输出一行 JSON，不要解释，不要 markdown。\n"
        "格式：{\"start\":\"...\",\"middle\":\"...\",\"end\":\"...\"}\n\n"
        "上下文开始：\n"
        f"{context_text}\n"
        "上下文结束。"
    )


def parse_answer_to_json(text: str) -> Optional[Dict[str, str]]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()

    # Fast path
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return {k: str(v) for k, v in obj.items()}
    except Exception:
        pass

    # Fallback: extract first JSON object block
    l = raw.find("{")
    r = raw.rfind("}")
    if l != -1 and r != -1 and r > l:
        block = raw[l : r + 1]
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                return {k: str(v) for k, v in obj.items()}
        except Exception:
            return None

    return None


def normalize_parsed(obj: Dict[str, str]) -> Dict[str, str]:
    """Normalize possible key variants returned by different models."""
    normalized: Dict[str, str] = {}
    for k, v in obj.items():
        kk = str(k).strip().lower().replace("_", "").replace("-", "")
        if kk in ("start", "anchorstart", "startvalue"):
            normalized["start"] = str(v).strip()
        elif kk in ("middle", "anchormiddle", "mid", "middlevalue"):
            normalized["middle"] = str(v).strip()
        elif kk in ("end", "anchorend", "endvalue"):
            normalized["end"] = str(v).strip()
    return normalized


def extract_from_text_fallback(text: str) -> Dict[str, str]:
    """Fallback extraction for non-JSON outputs.

    Tries patterns like:
    - start: START_XXXX
    - middle = MIDDLE_XXXX
    - end END_XXXX
    """
    import re

    out: Dict[str, str] = {}
    patterns = {
        "start": [r"start\s*[:=]\s*([A-Z_0-9]+)", r"\b(START_[A-Z0-9]+)\b"],
        "middle": [r"middle\s*[:=]\s*([A-Z_0-9]+)", r"\b(MIDDLE_[A-Z0-9]+)\b"],
        "end": [r"end\s*[:=]\s*([A-Z_0-9]+)", r"\b(END_[A-Z0-9]+)\b"],
    }

    for key, pats in patterns.items():
        for pat in pats:
            m = re.search(pat, text, flags=re.IGNORECASE)
            if m:
                out[key] = m.group(1).strip().upper()
                break

    return out


def evaluate_case(llm: ChatOpenAI, length_target: int, case_id: int) -> CaseResult:
    anchors = make_anchor_triplet()
    context_text = build_context(length_target, anchors)
    prompt = build_prompt(context_text)

    t0 = time.time()
    try:
        resp = llm.invoke(prompt)
        elapsed = int((time.time() - t0) * 1000)

        usage = extract_usage(resp)
        text = resp.content if hasattr(resp, "content") else str(resp)
        parsed_json = parse_answer_to_json(text) or {}
        parsed = normalize_parsed(parsed_json)
        if not parsed:
            parsed = extract_from_text_fallback(text)

        start_ok = int(parsed.get("start", "") == anchors["start"])
        mid_ok = int(parsed.get("middle", "") == anchors["middle"])
        end_ok = int(parsed.get("end", "") == anchors["end"])
        all_ok = int(start_ok == 1 and mid_ok == 1 and end_ok == 1)

        return CaseResult(
            length_target=length_target,
            case_id=case_id,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            total_tokens=usage["total_tokens"],
            latency_ms=elapsed,
            ok=True,
            error=None,
            start_correct=start_ok,
            middle_correct=mid_ok,
            end_correct=end_ok,
            overall_correct=all_ok,
            parsed_start=parsed.get("start"),
            parsed_middle=parsed.get("middle"),
            parsed_end=parsed.get("end"),
            raw_answer_preview=text[:220],
        )
    except Exception as e:
        elapsed = int((time.time() - t0) * 1000)
        return CaseResult(
            length_target=length_target,
            case_id=case_id,
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            latency_ms=elapsed,
            ok=False,
            error=str(e),
            start_correct=0,
            middle_correct=0,
            end_correct=0,
            overall_correct=0,
            parsed_start=None,
            parsed_middle=None,
            parsed_end=None,
            raw_answer_preview=None,
        )


def summarize_by_length(rows: List[CaseResult]) -> List[Dict[str, Any]]:
    grouped: Dict[int, List[CaseResult]] = {}
    for r in rows:
        grouped.setdefault(r.length_target, []).append(r)

    summary: List[Dict[str, Any]] = []
    for length in sorted(grouped.keys()):
        g = grouped[length]
        n = len(g)
        success = [x for x in g if x.ok]
        input_vals = [x.input_tokens for x in success if isinstance(x.input_tokens, int)]
        lat_vals = [x.latency_ms for x in g]

        s_acc = sum(x.start_correct for x in g) / n if n else 0.0
        m_acc = sum(x.middle_correct for x in g) / n if n else 0.0
        e_acc = sum(x.end_correct for x in g) / n if n else 0.0
        o_acc = sum(x.overall_correct for x in g) / n if n else 0.0

        summary.append(
            {
                "length_target": length,
                "cases": n,
                "success_rate": round(len(success) / n, 4) if n else 0.0,
                "start_accuracy": round(s_acc, 4),
                "middle_accuracy": round(m_acc, 4),
                "end_accuracy": round(e_acc, 4),
                "overall_accuracy": round(o_acc, 4),
                "input_tokens_avg": int(statistics.mean(input_vals)) if input_vals else None,
                "input_tokens_p95": int(percentile(input_vals, 95)) if input_vals else None,
                "latency_ms_avg": int(statistics.mean(lat_vals)) if lat_vals else None,
                "latency_ms_p95": int(percentile(lat_vals, 95)) if lat_vals else None,
            }
        )

    return summary


def percentile(values: List[int], p: int) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    k = (len(v) - 1) * (p / 100)
    f = int(k)
    c = min(f + 1, len(v) - 1)
    if f == c:
        return float(v[f])
    return v[f] + (v[c] - v[f]) * (k - f)


def find_effective_limit(summary: List[Dict[str, Any]], threshold: float) -> Optional[Dict[str, Any]]:
    passed = [s for s in summary if s["overall_accuracy"] >= threshold and s["success_rate"] > 0]
    if not passed:
        return None
    return max(passed, key=lambda x: x["length_target"])


def parse_lengths(s: str) -> List[int]:
    out = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate effective context window by memory recall quality")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--model", default="qwen2.5-7b-instruct-1m")
    parser.add_argument("--lengths", default="2000,8000,16000,32000,64000")
    parser.add_argument("--cases-per-length", type=int, default=6)
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument("--accuracy-threshold", type=float, default=0.9)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sleep-ms", type=int, default=80)
    parser.add_argument("--out", default="eval/effective_window_report.json")
    args = parser.parse_args()

    random.seed(args.seed)

    cfg = load_config(Path(args.config))
    llm_cfg = cfg.get("llm", {})

    llm = ChatOpenAI(
        model=args.model,
        api_key=llm_cfg.get("api_key"),
        base_url=llm_cfg.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        temperature=0,
        max_tokens=80,
        timeout=args.request_timeout,
        streaming=False,
    )

    lengths = parse_lengths(args.lengths)
    rows: List[CaseResult] = []

    print("=== Effective Context Window Eval ===")
    print(f"Model: {args.model}")
    print("Mode: direct call (no workflow compression/sliding-window)")
    print(f"Lengths: {lengths}")
    print(f"Cases per length: {args.cases_per_length}")

    for length in lengths:
        print(f"\n[Length {length}] running...")
        for i in range(1, args.cases_per_length + 1):
            r = evaluate_case(llm, length, i)
            rows.append(r)
            status = "OK" if r.ok else "FAIL"
            print(
                f"  case={i:02d} {status} input={r.input_tokens} total={r.total_tokens} "
                f"acc=({r.start_correct},{r.middle_correct},{r.end_correct}) latency_ms={r.latency_ms} "
                f"parsed=({r.parsed_start},{r.parsed_middle},{r.parsed_end})"
            )
            if args.sleep_ms > 0:
                time.sleep(args.sleep_ms / 1000)

    summary = summarize_by_length(rows)
    effective = find_effective_limit(summary, args.accuracy_threshold)

    report = {
        "meta": {
            "model": args.model,
            "lengths": lengths,
            "cases_per_length": args.cases_per_length,
            "accuracy_threshold": args.accuracy_threshold,
            "seed": args.seed,
            "note": "Practical usable limit based on recall quality, not hard max context size.",
        },
        "summary": summary,
        "effective_limit": effective,
        "raw": [asdict(r) for r in rows],
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== Summary ===")
    for s in summary:
        print(
            f"length={s['length_target']} overall_acc={s['overall_accuracy']:.2f} "
            f"mid_acc={s['middle_accuracy']:.2f} success={s['success_rate']:.2f} "
            f"input_avg={s['input_tokens_avg']} latency_p95={s['latency_ms_p95']}"
        )

    if effective:
        print(
            "\nEffective usable limit (by threshold): "
            f"length_target={effective['length_target']} "
            f"overall_acc={effective['overall_accuracy']:.2f}"
        )
    else:
        print("\nNo length met the configured accuracy threshold.")

    print(f"Saved: {out}")


if __name__ == "__main__":
    main()
