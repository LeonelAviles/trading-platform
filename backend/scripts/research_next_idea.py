"""Export training design context or append an unevaluated next-idea revision.

Uses existing DATABASE_URL and DATA_DIR. Never fits, scores or runs Nautilus.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_ml import iterations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['context', 'record', 'history'])
    parser.add_argument('--thread', required=True)
    parser.add_argument('--discovery', required=True)
    parser.add_argument('--input', type=Path)
    args = parser.parse_args()
    if args.action == 'record':
        if not args.input:
            parser.error('record requires --input with explicit next-idea document')
        result = iterations.propose(args.thread, args.discovery, json.loads(args.input.read_text()))
    elif args.action == 'context':
        result = iterations.context(args.thread, args.discovery)
    else:
        result = iterations.history(args.thread, args.discovery)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
