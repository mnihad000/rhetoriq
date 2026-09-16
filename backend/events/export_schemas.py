from __future__ import annotations

import json
from pathlib import Path

from models.events import DLQ_TOPICS, PRIMARY_TOPICS, event_schema


OUTPUT_DIR = Path(__file__).resolve().parent / "schemas"


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for topic in (*PRIMARY_TOPICS, *DLQ_TOPICS):
        path = OUTPUT_DIR / f"{topic}.schema.json"
        path.write_text(
            json.dumps(event_schema(topic), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
