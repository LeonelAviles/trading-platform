"""Run the research worker from the shell: `make research TOPICS=2`.
Works through the queue (seed + agent + owner topics) until the daily budget."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topics", type=int, default=1)
    ap.add_argument("--url", help="ingest this one URL instead of the queue")
    ap.add_argument("--topic", default=None)
    args = ap.parse_args()
    from agent import research

    if args.url:
        job = research.add_source(url=args.url, topic=args.topic, background=False)
        print(json.dumps({k: job.get(k) for k in ("status", "result", "error")}, indent=1))
        return 0 if job["status"] == "done" else 1
    out = research.run_once(args.topics)
    research._record_run("cli", out)
    for r in out:
        print(f"{r.get('status'):8} {r.get('topic')}: {len(r.get('sources') or [])} sources, "
              f"{sum(s.get('facts') or 0 for s in r.get('sources') or [])} facts; errors {len(r.get('errors') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
