"""Restore one archived raw day from S3 for tick replay (PLATFORM-SPEC.md §4.1).

    python scripts/restore.py <ROOT> <YYYY-MM-DD> [--schema mbo]

Downloads market-data/raw/<ROOT>/<date>.<schema>.dbn.zst from its
`archive_uri` in the manifest (egress costs a few cents per GB, so restore
single days, not ranges) and clears the manifest's `local: false` flag.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_DIR / ".env")

from market import ingest as ing  # noqa: E402
from market.paths import get_paths  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root")
    ap.add_argument("date")
    ap.add_argument("--schema", default="mbo")
    args = ap.parse_args(argv)

    paths = get_paths()
    manifest = ing.load_manifest(paths)
    key = f"{args.root}/{args.date}.{args.schema}.dbn.zst"
    entry = manifest["files"].get(key)
    if entry is None or not entry.get("archive_uri"):
        sys.exit(f"{key} is not archived (no archive_uri in manifest)")
    dst = paths.raw_dir / key
    if dst.exists():
        print(f"{dst} already local")
        return 0
    try:
        import boto3
    except ImportError:
        sys.exit("boto3 is required: pip install boto3")
    uri = entry["archive_uri"]
    bucket, s3_key = uri[len("s3://"):].split("/", 1)
    session = boto3.session.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    client = session.client("s3")
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"download {uri} -> {dst}")
    client.download_file(bucket, s3_key, str(dst))
    if entry.get("sha256") and ing.sha256_of(dst) != entry["sha256"]:
        dst.unlink()
        sys.exit("sha256 mismatch after download; file removed")
    entry["local"] = True
    ing.save_manifest(manifest, paths)
    print("restored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
