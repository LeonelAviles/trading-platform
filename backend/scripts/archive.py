"""Archive raw Databento files to S3 (PLATFORM-SPEC.md §4.1).

    python scripts/archive.py [--free-local] [--dry-run] [--roots ES,NQ]

Uploads every file under market-data/raw/ that the manifest does not mark
`archived`, using S3_BUCKET / S3_PREFIX / S3_STORAGE_CLASS (default
GLACIER_IR: instant retrieval, ~$0.004/GB-month) and AWS_PROFILE from the
environment (.env). After a verified upload the manifest gets
`archived: true` and `archive_uri`; with --free-local the local copy is
deleted — only for files whose derived outputs exist, so nothing the
platform serves at request time is lost. `scripts/restore.py` brings a day
back for tick replay.
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


def s3_client():
    try:
        import boto3
    except ImportError:
        sys.exit("boto3 is required: pip install boto3")
    session = boto3.session.Session(profile_name=os.environ.get("AWS_PROFILE") or None)
    return session.client("s3")


def archive_key(prefix: str, key: str) -> str:
    return f"{prefix.strip('/')}/{key}" if prefix else key


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--free-local", action="store_true", help="delete local copies after a verified upload")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--roots")
    args = ap.parse_args(argv)

    bucket = os.environ.get("S3_BUCKET")
    if not bucket:
        sys.exit("S3_BUCKET is not set (backend/.env)")
    prefix = os.environ.get("S3_PREFIX", "")
    storage_class = os.environ.get("S3_STORAGE_CLASS", "GLACIER_IR")
    roots = set(args.roots.split(",")) if args.roots else None

    paths = get_paths()
    manifest = ing.load_manifest(paths)
    files = ing.list_raw_files(paths, ing.SUPPORTED_SCHEMAS + ("mbp-10",), roots)
    client = None if args.dry_run else s3_client()
    uploaded = freed = 0
    for path in files:
        key = ing.manifest_key(path, paths)
        entry = manifest["files"].setdefault(key, {"root": path.parent.name, "date": str(ing.date_of(path)),
                                                    "schema": ing.schema_of(path), "size": path.stat().st_size})
        s3_key = archive_key(prefix, key)
        uri = f"s3://{bucket}/{s3_key}"
        if not entry.get("archived"):
            print(f"upload {key} -> {uri} ({storage_class})")
            if not args.dry_run:
                client.upload_file(str(path), bucket, s3_key, ExtraArgs={"StorageClass": storage_class})
                head = client.head_object(Bucket=bucket, Key=s3_key)
                if int(head["ContentLength"]) != path.stat().st_size:
                    print(f"  size mismatch after upload for {key}; not marking archived")
                    continue
                entry["archived"] = True
                entry["archive_uri"] = uri
                entry["storage_class"] = storage_class
                ing.save_manifest(manifest, paths)
            uploaded += 1
        if args.free_local and entry.get("archived") and path.exists():
            if not ing.outputs_exist(entry, paths):
                print(f"  keep local {key}: derived outputs missing (run scripts/ingest.py first)")
                continue
            print(f"  free local {key}")
            if not args.dry_run:
                path.unlink()
                entry["local"] = False
                ing.save_manifest(manifest, paths)
            freed += 1
    print(f"done: {uploaded} uploaded, {freed} local copies freed" + (" [dry run]" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
