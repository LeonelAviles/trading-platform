"""Download the approved ML4T source subset and rebuild its local index."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
ROOT = BACKEND.parent
TARGET = ROOT / "data" / "knowledge" / "repos" / "ml4t"
URL = "https://github.com/stefan-jansen/machine-learning-for-trading.git"
CHAPTERS = [
    "01_process_is_edge", "02_financial_data_universe", "03_market_microstructure",
    "04_fundamental_alternative_data", "05_synthetic_data", "06_strategy_definition",
    "07_defining_the_learning_task", "08_financial_features", "09_model_based_features",
    "10_text_feature_engineering", "11_ml_pipeline", "12_gradient_boosting",
    "13_dl_time_series", "14_latent_factors", "15_causal_estimation",
    "16_strategy_simulation", "17_portfolio_construction", "18_transaction_costs",
    "19_risk_management", "20_strategy_synthesis", "21_rl_execution_hedging",
    "22_rag_financial_research", "23_knowledge_graphs", "24_autonomous_agents",
    "25_live_trading", "26_mlops_governance", "27_systematic_edge",
    "case_studies", "docs", "scripts", "tests", "utils",
]


def run(*args: str) -> None:
    subprocess.run(list(args), check=True)


def main() -> None:
    if not (TARGET / ".git").exists():
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        run("git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", URL, str(TARGET))
    else:
        run("git", "-C", str(TARGET), "pull", "--ff-only")
    run("git", "-C", str(TARGET), "sparse-checkout", "set", *CHAPTERS)
    run(
        sys.executable, str(BACKEND / "scripts" / "ingest_knowledge.py"), str(TARGET),
        "--name", "machine-learning-for-trading",
        "--url", "https://github.com/stefan-jansen/machine-learning-for-trading",
        "--license", "MIT",
    )


if __name__ == "__main__":
    main()
