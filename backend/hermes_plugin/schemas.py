"""Tool schemas for the Hermes plugin — GENERATED, do not edit by hand.

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

_SCHEMAS = {
    "trading_get_spec_schema": {
        "type": "function",
        "function": {
            "name": "trading_get_spec_schema",
            "description": "The Strategy Spec v2 JSON Schema, every executable primitive with its parameters and docstring, the expression operators, and three worked examples (an ORB breakout, an ORB retest, a teaching-derived spec). Call this before building or revising a strategy: the expression tree may only reference primitives listed here; concepts that are missing must be composed from these or requested with request_primitive (Phase 4).",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    "trading_create_strategy": {
        "type": "function",
        "function": {
            "name": "trading_create_strategy",
            "description": "Validate and save a new Strategy Spec v2 (pass the whole spec as `spec`). Errors come back as readable messages \u2014 fix them and call again. The legacy v1 keyword form (name, symbol, direction, conditions, stop, target, ...) is still accepted and converted; `direction: \"both\"` is a single v2 strategy whose short side mirrors the long rules.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spec": {
                        "type": "object",
                        "description": "a complete Strategy Spec v2 document (see get_spec_schema)"
                    }
                },
                "required": [
                    "spec"
                ]
            }
        }
    },
    "trading_get_strategy": {
        "type": "function",
        "function": {
            "name": "trading_get_strategy",
            "description": "Fetch a saved strategy by id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "strategy_id"
                ]
            }
        }
    },
    "trading_list_strategies": {
        "type": "function",
        "function": {
            "name": "trading_list_strategies",
            "description": "List all saved strategies.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    "trading_propose_strategy_revision": {
        "type": "function",
        "function": {
            "name": "trading_propose_strategy_revision",
            "description": "Clone a strategy with ONE variable changed and save it as a lineage child (parentId = base, trialIndex + 1). `changes` maps dotted paths to values, e.g. {\"exit.target.value\": 3.0} or {\"filters\": [...]}. Say which variable changed in `changed_variable`; if two fields move together because one is the unit of the other, that counts as one change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "base_strategy_id": {
                        "type": "string"
                    },
                    "changes": {
                        "type": "object",
                        "description": "fields to override on the base strategy (e.g. new conditions/stop/target)"
                    },
                    "rationale": {
                        "type": "string",
                        "description": "why this change, e.g. citing a compare_winners_vs_losers finding"
                    },
                    "name": {
                        "type": "string"
                    }
                },
                "required": [
                    "base_strategy_id",
                    "changes",
                    "rationale"
                ]
            }
        }
    },
    "trading_update_strategy": {
        "type": "function",
        "function": {
            "name": "trading_update_strategy",
            "description": "Edit a saved strategy IN PLACE (same id). `changes` maps dotted paths or top-level keys to values. Destructive \u2014 for A/B experiments use propose_strategy_revision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_id": {
                        "type": "string"
                    },
                    "changes": {
                        "type": "object",
                        "description": "only the fields to overwrite on the existing strategy (e.g. new conditions/stop/target/session/interval)"
                    },
                    "rationale": {
                        "type": "string",
                        "description": "one line on what changed and why"
                    }
                },
                "required": [
                    "strategy_id",
                    "changes"
                ]
            }
        }
    },
    "trading_run_backtest": {
        "type": "function",
        "function": {
            "name": "trading_run_backtest",
            "description": "Run a strategy through the real NautilusTrader backtest and block until it finishes (or times out) \u2014 the point of calling this is to get trades back to analyze next, so it waits rather than returning a job id to poll.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_id": {
                        "type": "string"
                    },
                    "timeout_s": {
                        "type": "number",
                        "description": "default 180"
                    }
                },
                "required": [
                    "strategy_id"
                ]
            }
        }
    },
    "trading_get_backtest": {
        "type": "function",
        "function": {
            "name": "trading_get_backtest",
            "description": "Full backtest job, including every trade.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id"
                ]
            }
        }
    },
    "trading_get_backtest_analytics": {
        "type": "function",
        "function": {
            "name": "trading_get_backtest_analytics",
            "description": "Win rate, profit factor, expectancy (R), drawdown, Sharpe/SQN, equity curve, R-distribution, monthly table, exit-reason mix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id"
                ]
            }
        }
    },
    "trading_get_win_rate": {
        "type": "function",
        "function": {
            "name": "trading_get_win_rate",
            "description": "Cheap, direct answer to \"what's the win rate\" \u2014 pull get_backtest_analytics for the full picture.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id"
                ]
            }
        }
    },
    "trading_compare_backtests": {
        "type": "function",
        "function": {
            "name": "trading_compare_backtests",
            "description": "Side-by-side comparison of two backtests \u2014 use this whenever a user is choosing between two entry approaches (\"the breakout or the retest, which is better\") rather than eyeballing two get_backtest_analytics calls yourself. Ranks by expectancy (R), not raw win rate, since expectancy accounts for risk sizing and a low-win-rate/high-R strategy can beat a high-win-rate/low-R one. Also runs a two-proportion z-test on the win-rate difference and flags when either side has too few trades (<20) to draw a confident conclusion \u2014 don't declare a winner if `verdict` says evidence is insufficient, say so plainly instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id_a": {
                        "type": "string"
                    },
                    "job_id_b": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id_a",
                    "job_id_b"
                ]
            }
        }
    },
    "trading_get_weekly_performance": {
        "type": "function",
        "function": {
            "name": "trading_get_weekly_performance",
            "description": "Week-by-week returns for a backtest, scored against the goal: return between 2% and 5% in the MAJORITY of weeks traded, and end up positive overall. This is the pass/fail check for a strategy \u2014 call it on every backtest you want to judge, and use `meetsGoal`/`verdict` to decide whether to keep tuning or stop. Returns are percentages of account equity (starting at 100k, compounding week to week), and only weeks that actually traded are counted. Weeks above 5% count as meeting the goal \u2014 overshooting the band is not a failure \u2014 but they're reported separately as `weeksAboveBand` because outsized weeks are usually where the risk is.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id"
                ]
            }
        }
    },
    "trading_get_trade_features": {
        "type": "function",
        "function": {
            "name": "trading_get_trade_features",
            "description": "The core enrichment tool: for every closed trade in a backtest, reconstruct the market context at entry \u2014 relative volume, ATR14, RSI14, hour/day, distance from the 20-bar high/low, AND the Databento MBO order flow (deltaBar, cvd20, relDelta20, cvdSession, flowDivergence) \u2014 using the exact same indicator math the backtest engine used. The flow fields are null for symbols with no tick-level side data. This is what compare_winners_vs_losers() analyzes \u2014 call this first if you want the per-trade detail instead of the aggregate comparison.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id"
                ]
            }
        }
    },
    "trading_compare_winners_vs_losers": {
        "type": "function",
        "function": {
            "name": "trading_compare_winners_vs_losers",
            "description": "Statistical comparison of winning vs losing trades' entry context \u2014 answers \"what do winners have in common.\" Numeric features (relVolume20, atr14, rsi14, distFrom20High/Low, and the MBO order flow: deltaBar, cvd20, relDelta20, cvdSession) are ranked by effect size (Cohen's d: how many pooled standard deviations apart the two groups' means are \u2014 >0.5 is a moderately strong separation, >0.8 is strong). Categorical features (hourUtc, dayOfWeek, flowDivergence, exitReason) get a win-rate-by-bucket breakdown instead. A strong order-flow separation is directly actionable: delta_above/cvd_rising/rel_volume_above are real entry conditions, so a finding there can be tested as a revision.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id"
                ]
            }
        }
    },
    "trading_find_near_miss_entries": {
        "type": "function",
        "function": {
            "name": "trading_find_near_miss_entries",
            "description": "Bars inside the entry window where all but (up to) max_conditions_missing of the entry sub-conditions (the trigger's top-level AND terms plus the filters) were true, but the trigger did not fire. Useful for judging whether thresholds are too tight; it does not distinguish \"a condition fell just short\" from \"already in a position\".",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_id": {
                        "type": "string"
                    },
                    "max_conditions_missing": {
                        "type": "integer",
                        "description": "default 1"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "default 25"
                    }
                },
                "required": [
                    "strategy_id"
                ]
            }
        }
    },
    "trading_log_finding": {
        "type": "function",
        "function": {
            "name": "trading_log_finding",
            "description": "Record an agent finding/hypothesis against a backtest (e.g. \"winners cluster in the first hour of the session, confidence 0.7\") \u2014 appended to backtests/<job_id>/findings.json so it survives the chat session and can be shown in the UI or referenced in a later run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    },
                    "category": {
                        "type": "string",
                        "description": "e.g. 'pattern', 'risk', 'session-timing'"
                    },
                    "summary": {
                        "type": "string"
                    },
                    "confidence": {
                        "type": "number",
                        "description": "0..1, optional"
                    }
                },
                "required": [
                    "job_id",
                    "category",
                    "summary"
                ]
            }
        }
    },
    "trading_get_findings": {
        "type": "function",
        "function": {
            "name": "trading_get_findings",
            "description": "Findings previously logged against a backtest.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string"
                    }
                },
                "required": [
                    "job_id"
                ]
            }
        }
    }
}


# name as Hermes knows it -> name the backend dispatcher knows
UNPREFIXED = {name: name[len("trading_"):] for name in _SCHEMAS}
