"""Regenerate hermes_plugin/schemas.py from agent_tools.TOOLS.

The Hermes plugin can't import agent_tools (that's the whole design — see
hermes_plugin/__init__.py), but it still needs the schemas at registration
time, before the backend is known to be up. So it ships a generated copy,
and this script is how that copy stays honest. Run it after changing TOOLS:

    .venv/bin/python scripts/gen_hermes_schemas.py

`/trading drift` in Hermes compares the shipped copy against the live
/api/agent/tools manifest and tells you when this needs re-running.
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import agent_tools  # noqa: E402

PREFIX = "trading_"
OUT = pathlib.Path(__file__).resolve().parent.parent / "hermes_plugin" / "schemas.py"

HEADER = '''"""Tool schemas for the Hermes plugin — GENERATED, do not edit by hand.

Regenerate with: .venv/bin/python scripts/gen_hermes_schemas.py
Source of truth: agent_tools.TOOLS

Shipped as a static copy rather than fetched at register() time so the tools
still appear in `hermes tools` when the backend is down — a tool that exists
and reports "backend unreachable" is easier to debug than a tool that
silently isn't there.

Names are prefixed `trading_` because the bare ones (create_strategy,
get_findings, run_backtest) are generic enough to collide with another
plugin's toolset in a shared agent namespace.
"""

'''

TRAILER = '''

# name as Hermes knows it -> name the backend dispatcher knows
UNPREFIXED = {name: name[len("%s"):] for name in _SCHEMAS}
''' % PREFIX


def main() -> None:
    schemas = {}
    for tool in agent_tools.TOOLS:
        fn = dict(tool["function"])
        fn["name"] = PREFIX + fn["name"]
        # Descriptions come from the tool functions' docstrings; collapse the
        # whitespace so the model sees one clean paragraph per tool.
        if fn.get("description"):
            fn["description"] = " ".join(fn["description"].split())
        schemas[fn["name"]] = {"type": "function", "function": fn}

    body = "_SCHEMAS = " + json.dumps(schemas, indent=4) + "\n"
    OUT.write_text(HEADER + body + TRAILER, encoding="utf-8")
    print(f"wrote {OUT} ({len(schemas)} tools)")


if __name__ == "__main__":
    main()
