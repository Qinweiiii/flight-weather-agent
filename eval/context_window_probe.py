"""Context window stress probe.

This probe intentionally calls the model directly (not through MasterAgent),
so conversation compression/sliding-window logic in the project pipeline is bypassed.

Examples:
  python eval/context_window_probe.py --model qwen-turbo-latest
  python eval/context_window_probe.py --model qwen-turbo-latest --start-tokens 4000 --step-tokens 8000 --max-rounds 16
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from langchain_openai import ChatOpenAI


@dataclass
class ProbeRow:
    round_id: int
    target_tokens: int
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    latency_ms: int
    ok: bool
    error: Optional[str]


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


def build_prompt(target_tokens: int) -> str:
    # Note: target_tokens is a synthetic target; real token count is read from API usage.
    chunk = "航班延误天气分析测试。"
    repeats = max(1, target_tokens // max(len(chunk), 1))
    body = chunk * repeats
    return (
        "你是一个严格执行指令的助手。只回复OK，不要输出其他内容。\n"
        "以下是压测文本：\n"
        f"{body}"
    )


def run_probe(llm: ChatOpenAI, start_tokens: int, step_tokens: int, max_rounds: int, sleep_ms: int) -> List[ProbeRow]:
    rows: List[ProbeRow] = []
    target = start_tokens

    for i in range(1, max_rounds + 1):
        prompt = build_prompt(target)
        t0 = time.time()
        try:
            resp = llm.invoke(prompt)
            elapsed = int((time.time() - t0) * 1000)
            usage = extract_usage(resp)
            rows.append(
                ProbeRow(
                    round_id=i,
                    target_tokens=target,
                    input_tokens=usage["input_tokens"],
                    output_tokens=usage["output_tokens"],
                    total_tokens=usage["total_tokens"],
                    latency_ms=elapsed,
                    ok=True,
                    error=None,
                )
            )
            target += step_tokens
        except BaseException as e:
            elapsed = int((time.time() - t0) * 1000)
            rows.append(
                ProbeRow(
                    round_id=i,
                    target_tokens=target,
                    input_tokens=None,
                    output_tokens=None,
                    total_tokens=None,
                    latency_ms=elapsed,
                    ok=False,
                    error=str(e),
                )
            )
            break

        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000)

    return rows


def print_report(rows: List[ProbeRow], model: str) -> None:
    print("\n=== Context Probe ===")
    print(f"Model: {model}")
    print("Mode: direct LLM call (compression/sliding-window in project pipeline is bypassed)")

    for r in rows:
        status = "OK" if r.ok else "FAIL"
        print(
            f"#{r.round_id:02d} [{status}] target={r.target_tokens}, "
            f"input={r.input_tokens}, output={r.output_tokens}, total={r.total_tokens}, latency_ms={r.latency_ms}"
        )

    last_ok = None
    first_fail = None
    for r in rows:
        if r.ok:
            last_ok = r
        else:
            first_fail = r
            break

    print("\n--- Boundary ---")
    if last_ok:
        print(
            "Last success: "
            f"target={last_ok.target_tokens}, input={last_ok.input_tokens}, total={last_ok.total_tokens}"
        )
    else:
        print("Last success: none")

    if first_fail:
        print(f"First failure: target={first_fail.target_tokens}, error={first_fail.error}")
    else:
        print("First failure: none (not reached in this run)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe practical context boundary")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--model", default="qwen-turbo-latest", help="Use a cheaper model for probing")
    parser.add_argument("--start-tokens", type=int, default=4000)
    parser.add_argument("--step-tokens", type=int, default=8000)
    parser.add_argument("--max-rounds", type=int, default=14)
    parser.add_argument("--sleep-ms", type=int, default=120)
    parser.add_argument("--request-timeout", type=int, default=60, help="Single request timeout in seconds")
    parser.add_argument("--out", default="eval/context_probe_result_turbo.json")
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    llm_cfg = cfg.get("llm", {})

    llm = ChatOpenAI(
        model=args.model,
        api_key=llm_cfg.get("api_key"),
        base_url=llm_cfg.get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        temperature=0,
        max_tokens=64,
        timeout=args.request_timeout,
        streaming=False,
    )

    rows = run_probe(
        llm=llm,
        start_tokens=args.start_tokens,
        step_tokens=args.step_tokens,
        max_rounds=args.max_rounds,
        sleep_ms=args.sleep_ms,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2), encoding="utf-8")

    print_report(rows, args.model)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
