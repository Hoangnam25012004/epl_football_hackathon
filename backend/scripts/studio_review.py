"""Review the model's studio texts (docs/day3-design.md section 17, phase P6).

Reads the JSONL written by `pundit studio --json out.jsonl` and prints every text a model wrote
that was published, then every draft the Guardrail rejected with its violations, so a person
can look for wrong rejections (blocked good text) and for holes (bad text that passed).

    cd backend
    uv run pundit studio --provider ollama --llm-min-importance 2 --report --json out.jsonl
    uv run python scripts/studio_review.py out.jsonl
"""

import io
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def main(path: Path) -> int:
    published: list[str] = []
    rejected: list[str] = []
    calls: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        record: dict[str, Any] = json.loads(line)
        kind = record["type"]
        item = record.get("item", {})
        if kind == "commentary":
            for text in item["texts"]:
                if text["source"] == "llm":
                    published.append(
                        f"[{item['display_minute']:>6}] {item['trigger_kind']:<15} "
                        f"{text['language']}/{text['mode']:<7} {text['text']}"
                    )
        elif kind == "talking_point":
            for language in ("en", "vi"):
                if item[f"source_{language}"] == "llm":
                    lines = " | ".join(line["text"] for line in item[language])
                    published.append(
                        f"[{item['display_minute']:>6}] {item['trigger_kind']:<15} "
                        f"tp {language}     {lines}"
                    )
        elif kind == "recap":
            for text in item["texts"]:
                if text["source"] == "llm":
                    body = " ".join(text["paragraphs"])
                    published.append(
                        f"[recap {item['scope']} r{item['revision']}] {text['language']}/"
                        f"{text['mode']} {text['title']} — {body}"
                    )
        elif kind == "trace":
            for span in record["spans"]:
                if span["kind"] == "llm":
                    calls[span["status"]] += 1
                if span["kind"] != "guardrail" or span["status"] != "rejected":
                    continue
                agent = str(span["input"].get("agent", ""))
                if agent.startswith("Template"):
                    continue
                violations = span["output"].get("violations", [])
                codes.update(str(v.get("code")) for v in violations if isinstance(v, dict))
                texts = span["input"].get("texts") or [span["input"]]
                detail = "; ".join(
                    f"{v.get('code')}: {v.get('detail')}" for v in violations if isinstance(v, dict)
                )
                rejected.append(
                    f"[{record['trigger_id']}] {agent} {span['input'].get('language', '')}/"
                    f"{span['input'].get('mode') or '-'} attempt {span['attempt']}\n"
                    f"      draft: {json.dumps(texts, ensure_ascii=False)[:600]}\n"
                    f"      why:   {detail[:600]}"
                )
    print(f"PUBLISHED MODEL TEXTS ({len(published)})")
    for entry in published:
        print(f"  {entry}")
    print(f"\nREJECTED DRAFTS ({len(rejected)}) by code: {dict(codes.most_common())}")
    for entry in rejected:
        print(f"  {entry}")
    print(f"\nmodel calls by status: {dict(calls)}")
    return 0


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) != 2:
        raise SystemExit("usage: studio_review.py out.jsonl")
    raise SystemExit(main(Path(sys.argv[1])))
