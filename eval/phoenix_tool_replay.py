"""Replay Phoenix tool decisions against two models without executing tools.

Prompts stay in memory and are never printed or written to the repository. The
OpenRouter key is read from the local configuration, not from the SSH server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from datetime import date

from openai import AsyncOpenAI

from app.config import settings
from app.llm.chat_client import _parse_text_tool_calls

_PHOENIX_DB = "file:/var/lib/docker/volumes/phoenix_phoenix_data/_data/phoenix.db?mode=ro"
_MODELS = ("deepseek/deepseek-v4-flash-0731", "qwen/qwen3.5-flash-02-23")
_GEMINI = "google/gemini-3-flash-preview"


def _read_spans(ssh_target: str, day: date) -> list[dict]:
    sql = f"""
    select json_object('id', id, 'attributes', json(attributes))
    from spans
    where name = 'ChatCompletion'
      and substr(start_time, 1, 10) = '{day.isoformat()}'
      and json_extract(
        json_extract(attributes, '$.llm.invocation_parameters'), '$.tool_choice'
      ) = 'required'
    order by start_time;
    """
    process = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", ssh_target, "sqlite3", _PHOENIX_DB],
        input=sql, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return [json.loads(line) for line in process.stdout.splitlines() if line]


def _request(span: dict) -> tuple[list[dict], list[dict]]:
    attrs = span["attributes"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    messages = []
    for item in attrs["llm"]["input_messages"]:
        message = item["message"]
        messages.append({"role": message["role"], "content": message["content"]})
    tools = [json.loads(item["tool"]["json_schema"]) for item in attrs["llm"]["tools"]]
    return messages, tools


async def _run(spans: list[dict], models: tuple[str, ...], max_usd: float) -> None:
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required")
    client = AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1", api_key=settings.openrouter_api_key,
    )
    reserved = 0.0
    results = []
    for span in spans:
        messages, tools = _request(span)
        for model in models:
            allowance = 0.01 if model == _GEMINI else 0.005
            if reserved + allowance > max_usd + 1e-9:
                print(f"STOP budget: ${reserved:.3f} reserved")
                return
            started = time.monotonic()
            response = await client.chat.completions.create(
                model=model, messages=messages, tools=tools,
                tool_choice="required", max_tokens=1024 if model == _GEMINI else 4096,
                extra_body={"reasoning": {"enabled": False}},
                extra_headers={"X-OpenRouter-Metadata": "enabled"},
            )
            choice = response.choices[0]
            calls = choice.message.tool_calls or []
            names = [call.function.name for call in calls]
            if not names:
                names = [
                    call["name"] for call in _parse_text_tool_calls(choice.message.content or "")
                ]
            reserved += allowance
            row = {
                "span_id": span["id"], "model": model, "finish": choice.finish_reason,
                "selected": names, "meal_selected": "log_meal" in names,
                "latency_s": round(time.monotonic() - started, 2),
                "generation_id": response.id,
            }
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    for model in models:
        sample = [row for row in results if row["model"] == model]
        count = sum(row["meal_selected"] for row in sample)
        print(f"{model}: log_meal selected {count}/{len(sample)}")
    print(f"Reserved ${reserved:.3f} of ${max_usd:.3f}; no tool executed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh", required=True, help="SSH target for read-only Phoenix access")
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--max-usd", type=float, default=0.12)
    parser.add_argument("--model", choices=(*_MODELS, _GEMINI), action="append")
    parser.add_argument("--span-id", type=int, action="append")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if args.max_usd > 0.12:
        parser.error("The production replay is capped at $0.12")
    spans = _read_spans(args.ssh, args.date)
    if args.span_id:
        spans = [span for span in spans if span["id"] in args.span_id]
    print(f"Loaded {len(spans)} required-decision spans (prompts redacted)")
    if args.live:
        asyncio.run(_run(spans, tuple(args.model or _MODELS), args.max_usd))


if __name__ == "__main__":
    main()
