"""Tool handlers — one thin closure per tool, all routed through client.

Every handler has the same body, so they're built by a factory rather than
written out 15 times. The only per-tool difference is the name, and the
schemas module already knows all 15.
"""

from __future__ import annotations

from tools.registry import tool_error, tool_result

from . import client
from .schemas import UNPREFIXED


def make_handler(hermes_name: str):
    """Build the handler for one tool.

    Hermes wants a JSON *string* back and wants failures returned rather
    than raised. agent_tools.call_tool() already honours that contract on
    the far side ({"error": ...} instead of an exception), so the only
    thing left to catch here is the transport itself.
    """
    backend_name = UNPREFIXED[hermes_name]

    def handler(args: dict, **_kwargs) -> str:
        try:
            return tool_result(client.call_tool(backend_name, args or {}))
        except client.BackendUnavailable as e:
            return tool_error(str(e))
        except Exception as e:  # never let a plugin bug kill the agent turn
            return tool_error(f"{type(e).__name__}: {e}")

    handler.__name__ = f"handle_{hermes_name}"
    return handler
