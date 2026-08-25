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
    "trading_get_condition_vocabulary": {
        "type": "function",
        "function": {
            "name": "trading_get_condition_vocabulary",
            "description": "The exact entry-condition/stop/target vocabulary the backtest engine understands, read live from strategy_spec.py \u2014 call this before building a strategy so the rule set you generate is guaranteed valid.",
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
            "description": "Validate and save a new strategy. direction is 'long', 'short', or 'both' (saves two sibling strategies sharing a directionGroup id, since the engine is single-directional per run).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string"
                    },
                    "symbol": {
                        "type": "string"
                    },
                    "direction": {
                        "type": "string",
                        "enum": [
                            "long",
                            "short",
                            "both"
                        ]
                    },
                    "conditions": {
                        "type": "array",
                        "items": {
                            "type": "object"
                        },
                        "description": "entry conditions (ANDed); see get_condition_vocabulary"
                    },
                    "stop": {
                        "type": "object",
                        "description": "{type: percent|fixed_points|atr, value, period?, mult?}"
                    },
                    "target": {
                        "type": "object",
                        "description": "{type: rr|percent|fixed_points, value}"
                    },
                    "sizing": {
                        "type": "object",
                        "description": "optional {type: fixed_qty|percent_equity, value}"
                    },
                    "session": {
                        "type": "object",
                        "description": "optional {start: 'HH:MM', end: 'HH:MM'} UTC"
                    },
                    "interval": {
                        "type": "string",
                        "description": "bar interval, default '1min'"
                    }
                },
                "required": [
                    "name",
                    "symbol",
                    "direction",
                    "conditions",
                    "stop",
                    "target"
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
            "description": "Clone an existing strategy with changes applied (e.g. new conditions/ stop/target from a winners-vs-losers finding) and save it as a new strategy, so it can be backtested and compared against the original without overwriting it. `changes` is shallow-merged onto the base spec.",
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
    "trading_get_trade_features": {
        "type": "function",
        "function": {
            "name": "trading_get_trade_features",
            "description": "The core enrichment tool: for every closed trade in a backtest, reconstruct the market context at entry (relative volume, ATR14, RSI14, hour/day, distance from the 20-bar high/low) using the exact same indicator math the backtest engine used. This is what compare_winners_vs_losers() analyzes \u2014 call this first if you want the per-trade detail instead of the aggregate comparison.",
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
            "description": "Statistical comparison of winning vs losing trades' entry context \u2014 answers \"what do winners have in common.\" Numeric features (relVolume20, atr14, rsi14, distFrom20High/Low) are ranked by effect size (Cohen's d: how many pooled standard deviations apart the two groups' means are \u2014 >0.5 is a moderately strong separation, >0.8 is strong). Categorical features (hourUtc, dayOfWeek, exitReason) get a win-rate-by-bucket breakdown instead.",
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
            "description": "Bars where entry almost triggered \u2014 all but (up to) max_conditions_missing of the entry conditions were true, during the trading session, but the strategy didn't actually enter (either a condition fell just short, or the strategy was already in a position \u2014 this doesn't distinguish the two). Useful for judging whether thresholds are too tight, not a bug detector for the backtest engine itself.",
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
