from __future__ import annotations

import json
import argparse
from pathlib import Path

from models.events import DLQ_TOPICS, PRIMARY_TOPICS, event_schema


OUTPUT_DIR = Path(__file__).resolve().parent / "schemas"


def main() -> None:
    parser = argparse.ArgumentParser(description="Export or verify committed event contracts")
    parser.add_argument("--check",action="store_true")
    args = parser.parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stale = []
    for topic in (*PRIMARY_TOPICS, *DLQ_TOPICS):
        path = OUTPUT_DIR / f"{topic}.schema.json"
        expected = json.dumps(event_schema(topic), indent=2, sort_keys=True) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != expected:
                stale.append(path.name)
        else:
            path.write_text(expected,encoding="utf-8")
    if stale:
        parser.exit(1,"Stale event schemas: "+", ".join(stale)+"\n")


if __name__ == "__main__":
    main()
