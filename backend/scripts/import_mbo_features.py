"""Import verified prepared Parquet one transaction/day; retain every original.

Dry-run by default. --apply commits, --initialize adds tables explicitly.
Database batches serve as durable checkpoints; rerunning validates and skips
identical batches. Stop on the first corrupt/conflicting partition.
"""
import argparse
import json
import shutil
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from market.paths import get_paths
from research_ml import feature_store as store


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--contract")
    parser.add_argument("--min-free-gib", type=float, default=2)
    args = parser.parse_args(argv)
    if args.min_free_gib < 1:
        parser.error("at least 1 GiB local safety floor required")
    if args.initialize:
        store.initialize()
    root = get_paths().mbo_features_dir
    # Scan one day directory at a time, no all-dataset DataFrame or file list.
    if not root.exists():
        raise ValueError("legacy prepared directory does not exist")
    for contract in sorted(root.glob("contract=*")):
        if args.contract and contract.name != f"contract={args.contract}":
            continue
        for day in sorted(contract.glob("date=*")):
            for partition in sorted(day.iterdir()):
                if not partition.is_dir() or partition.name.startswith("."):
                    continue
                file = partition / "metadata.json"
                if shutil.disk_usage(root).free < args.min_free_gib * 2**30:
                    raise ValueError("local free-disk floor reached; originals retained")
                if args.apply:
                    result = store.import_partition(file.parent)
                else:
                    meta = store.legacy._metadata(file.parent)
                    _, result = store.legacy._read(meta["document"]["identity"])
                print(json.dumps({"partitionId": result["partitionId"], "rows": result["document"]["rows"],
                                  "status": "reused" if result.get("reused") else "committed" if args.apply else "validated"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
