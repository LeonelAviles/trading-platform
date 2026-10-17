"""Prepare reusable MBO feature rows; no models are fitted.

    python scripts/prepare_mbo_features.py --request feature-request.json [--restore]

JSON may be one MBOFeatureRequest or a list (one per selected contract).
--restore downloads only missing requested raw days via the existing S3
manifest/checksum workflow. Existing matching feature partitions skip restore.
"""
import argparse
import json
from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from config.instruments import load_instruments  # noqa: E402
from market.paths import get_paths  # noqa: E402
from research_ml import feature_store  # noqa: E402
from research_ml.schema import MBOFeatureRequest  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--restore", action="store_true", help="restore missing selected raw days from S3")
    args = parser.parse_args(argv)
    payload = json.loads(args.request.read_text())
    requests = payload if isinstance(payload, list) else [payload]
    if not 1 <= len(requests) <= 100:
        parser.error("provide 1..100 contract requests")
    # Validate the entire batch before performing downloads or extraction.
    requests = [MBOFeatureRequest.model_validate(request) for request in requests]
    tick = load_instruments().roots["ES"].tick_size
    for request in requests:
        for source in request.sources:
            identity = feature_store._identity(request, source, tick)
            raw = get_paths().raw_dir / "ES" / f"{source.day}.mbo.dbn.zst"
            if args.restore and not feature_store.exists(identity) and not raw.exists():
                from scripts.restore import main as restore
                restore(["ES", str(source.day)])
            print(f"prepare {request.contract} {source.day}", flush=True)
            result = feature_store.prepare_day(request, source, tick)
            print(json.dumps({"partitionId": result["partitionId"], "reused": result["reused"],
                              "rows": result["document"]["rows"]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
