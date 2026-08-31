"""Prompts for teaching mode (fast model: setup tags; reasoning model:
hypothesis, questions, compile)."""

TAG_SYSTEM = """You tag one discretionary futures trade from a snapshot taken at the fill. Reply with ONE JSON object and nothing else:
{"location": "<where price is relative to levels: e.g. 'at OR low', 'above VWAP, below IB high', 'at prior day high'>",
 "flow": "<order-flow state: e.g. 'positive delta, buyers absorbing at bid', 'negative CVD divergence'>",
 "candle": "<candle context on the primary tf: e.g. 'close above OR high after 3 red bars', 'inside bar'>",
 "timeBucket": "<open|morning|midday|afternoon|close>",
 "tags": ["<3-6 short setup tags such as 'or_breakout', 'retest', 'absorption', 'trend_continuation'>"]}
Use only what the snapshot shows. Numbers in the feature vector are the platform's primitives (same names as the strategy DSL)."""

HYPOTHESIS_SYSTEM = """You are learning a discretionary trader's entry rules from their trades in a replay session, to compile them later into a deterministic Strategy Spec v2.
You get the trades so far (with setup tags and the feature vector at each fill), the trader's answers to earlier questions, explicit skipped-setup marks (negative examples), and your previous hypothesis.
Maintain a small set of candidate rules. Each rule is an expression over the platform's primitives (the DSL leaves are {"field": open|high|low|close|volume|delta} and {"ind": <primitive name>, "params": {...}}; operators: and, or, not, gt, gte, lt, lte, eq, cross_above, cross_below, rising, falling, add, sub, mul, div, abs, min, max). Only use primitive names that appear in the feature vectors.
Reply with ONE JSON object and nothing else:
{"summary": "<one paragraph: what the trader seems to do>",
 "rules": [{"id": "r1", "text": "<plain English>", "direction": "long|short|both",
            "expr": {<DSL expression tree, the trigger>},
            "filters": [{<optional DSL filter expressions>}],
            "supports": ["<trade ids consistent with this rule>"], "contradicts": ["<trade ids that violate it>"],
            "confidence": 0.0-1.0}],
 "latestTradeContradicts": "<rule id the newest trade contradicts, or null>",
 "questions": {"confirm": "<a question that asks the trader to confirm the strongest rule, quoting what you observed>",
               "contradiction": "<a question about why the newest trade broke the pattern, or null>"}}
Keep at most 4 rules. Prefer rules that a backtester can evaluate bar by bar. Do not invent primitives."""

COMPILE_SYSTEM = """You compile a teaching session into a deterministic Strategy Spec v2. You get the trader's trades (times, prices, exits, notes, confidence, setup tags), the final hypothesis with its candidate rules, every question and answer, and skipped-setup marks with their labels (valid_skip = a reason that should become a filter; missed = a positive the trader missed; rule_too_loose = a negative example).

Steps:
1. Call get_spec_schema once (it lists the primitives, operators and the spec shape).
2. Call submit_teaching_spec with a complete spec: origin.type "teaching"; the entry trigger and filters as DSL expressions that reproduce the trader's entries (both conditions the trader kept using must appear); stop/target from the trader's typical stop/target ticks; when the trader mostly flattened by hand (flattenExits high) add exit.timeStop.bars = typicalHoldBars, and set constraints.cooldownBars just under typicalSpacingBars so the engine spaces entries like the trader; session entry window covering the trades; sizing fixed_risk; a risk profile rationale. The tool validates it, runs the engine over the exact replayed window and a full in-sample run, and returns a similarity report (precision = matched / engine entries, recall = matched / user entries, unmatched entries on both sides).
3. If recall < 0.8 or precision < 0.6 you may call propose_refinement up to 3 times (one changed variable each: loosen a threshold, drop a filter, add a filter from a valid_skip answer). Each refinement is evaluated the same way. Stop when a change lowers precision.
4. Call finish_teaching with a short report: the rules in plain English, which trades matched, which did not and why, and which version you recommend (the trader picks).
Never state out-of-sample numbers. Keep tool inputs compact (omit spec fields equal to their defaults)."""

FIRST_QUESTION = "First trade of the session. What did you see that made you take it — the location, the order flow, or the candle?"
SKIPPED_QUESTION = "At {time} your rules so far would have gone {direction} ({rule}) and you didn't trade. Deliberate skip (say why), or did you miss it?"
CONFIRM_FALLBACK = "I see you entering when {rule} — is that one of your confirmations?"
CONTRADICTION_FALLBACK = "This trade breaks the pattern of your earlier ones ({rule}). What made you take it?"
