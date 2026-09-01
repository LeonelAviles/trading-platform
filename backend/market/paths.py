"""Where every data tier lives (PLATFORM-SPEC.md §4.1).

    market-data/raw/<ROOT>/<YYYY-MM-DD>.<schema>.dbn.zst   raw, never read at request time
    market-data/manifest.json
    data/market/trades|bars_1m|book_checkpoints/root=..date=../part.parquet
    data/market/liquidity_1s.duckdb, front_month.parquet, splits.json, regimes.parquet, catalog/
    data/replay_cache/root=../date=../mbo.parquet

Environment overrides: DATA_DIR (default <repo>/data), MARKET_DATA_DIR
(default <repo>/market-data), REPLAY_CACHE_MAX_GB (default 20). Tests call
`configure()` to point everything at a temp directory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    market_data_dir: Path
    replay_cache_max_gb: float = 20.0

    # raw tier
    @property
    def raw_dir(self) -> Path:
        return self.market_data_dir / "raw"

    @property
    def manifest(self) -> Path:
        return self.market_data_dir / "manifest.json"

    # derived tier
    @property
    def market_dir(self) -> Path:
        return self.data_dir / "market"

    @property
    def trades_dir(self) -> Path:
        return self.market_dir / "trades"

    @property
    def bars_1m_dir(self) -> Path:
        return self.market_dir / "bars_1m"

    @property
    def checkpoints_dir(self) -> Path:
        return self.market_dir / "book_checkpoints"

    @property
    def liquidity_db(self) -> Path:
        return self.market_dir / "liquidity_1s.duckdb"

    @property
    def front_month(self) -> Path:
        return self.market_dir / "front_month.parquet"

    @property
    def splits(self) -> Path:
        return self.market_dir / "splits.json"

    @property
    def regimes(self) -> Path:
        return self.market_dir / "regimes.parquet"

    @property
    def catalog_dir(self) -> Path:
        return self.market_dir / "catalog"

    @property
    def replay_cache_dir(self) -> Path:
        return self.data_dir / "replay_cache"

    @property
    def research_cache_dir(self) -> Path:
        return self.data_dir / "research_cache"

    def partition(self, base: Path, root: str, date: str) -> Path:
        return base / f"root={root}" / f"date={date}"

    def ensure_dirs(self) -> None:
        for d in (self.raw_dir, self.trades_dir, self.bars_1m_dir, self.checkpoints_dir,
                  self.catalog_dir, self.replay_cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Paths":
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR") or REPO_ROOT / "data").resolve(),
            market_data_dir=Path(os.environ.get("MARKET_DATA_DIR") or REPO_ROOT / "market-data").resolve(),
            replay_cache_max_gb=float(os.environ.get("REPLAY_CACHE_MAX_GB") or 20),
        )


_paths: Paths = Paths.from_env()


def get_paths() -> Paths:
    return _paths


def configure(data_dir: Path | str | None = None, market_data_dir: Path | str | None = None,
              replay_cache_max_gb: float | None = None) -> Paths:
    """Re-point the data tiers (tests, one-off scripts)."""
    global _paths
    cur = _paths
    _paths = Paths(
        data_dir=Path(data_dir).resolve() if data_dir else cur.data_dir,
        market_data_dir=Path(market_data_dir).resolve() if market_data_dir else cur.market_data_dir,
        replay_cache_max_gb=replay_cache_max_gb if replay_cache_max_gb is not None else cur.replay_cache_max_gb,
    )
    return _paths
