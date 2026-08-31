"""Synthetic partitions -> NautilusTrader catalog -> read back."""

from datetime import date

import pytest

from market import catalog as cat
from market import ingest as ing
from market import paths as paths_mod
from config.instruments import get_root
from tests import synth
from tests.test_ingest import _chunks


def test_contract_calendar():
    es = get_root("ES")
    assert cat.third_friday(2026, 6) == date(2026, 6, 19)
    assert cat.third_friday(2026, 3) == date(2026, 3, 20)
    assert cat.parse_outright("ESM6", es, date(2026, 4, 1)) == (2026, 6)
    assert cat.parse_outright("ESU6", es, date(2026, 6, 20)) == (2026, 9)
    # The digit rolls over: 'ESH7' seen in 2026 is March 2027; 'ESM6' after its expiry means 2036.
    assert cat.parse_outright("ESH7", es, date(2026, 4, 1)) == (2027, 3)
    assert cat.parse_outright("ESM6", es, date(2026, 7, 1)) == (2036, 6)
    assert cat.parse_outright("ESM6", es, date(2026, 6, 22)) == (2026, 6)   # a week of grace after expiry
    with pytest.raises(ValueError):
        cat.parse_outright("ESXX", es, date(2026, 4, 1))


def test_contract_fields():
    inst = cat.contract_for("ESM6", get_root("ES"), date(2026, 4, 1))
    assert str(inst.id) == "ESM6.SIM"
    assert float(inst.multiplier) == 50 and str(inst.price_increment) == "0.25" and inst.price_precision == 2
    assert inst.underlying == "ES"
    assert cat.bar_type_str("ESM6") == "ESM6.SIM-1-MINUTE-LAST-EXTERNAL"
    nq = cat.contract_for("NQU6", get_root("NQ"), date(2026, 7, 1))
    assert float(nq.multiplier) == 20 and nq.info["tickValue"] == 5.0


def test_build_day_roundtrip(tmp_path):
    p = paths_mod.configure(data_dir=tmp_path / "data", market_data_dir=tmp_path / "market-data")
    p.ensure_dirs()
    try:
        cfg = synth.SynthConfig(rth_start="09:30", rth_end="09:50", seed=21)
        mbo = synth.generate_mbo(cfg)
        ing.DayIngest(None, schema="mbo", session_date=cfg.session_date, frames=_chunks(mbo), paths=p,
                      min_daily_volume=1, book=False, name="synthetic").run()
        summary = cat.build(p, progress=lambda s: None)
        assert summary == {"built": 1, "skipped": 0, "instruments": 2}

        c = cat.open_catalog(p)
        ids = sorted(str(i.id) for i in c.instruments())
        assert ids == ["ESM6.SIM", "ESU6.SIM"]
        trades = synth.trades(mbo)
        front = trades[trades["symbol"] == "ESM6"]
        ticks = c.trade_ticks(instrument_ids=["ESM6.SIM"])
        assert len(ticks) == len(front)
        buyers = sum(1 for t in ticks if t.aggressor_side.name == "BUYER")
        assert buyers == int((front["side"] == "B").sum())
        assert ticks[0].ts_init >= ticks[0].ts_event
        assert all(a.ts_init <= b.ts_init for a, b in zip(ticks, ticks[1:]))
        bars = c.bars(bar_types=[cat.bar_type_str("ESM6")])
        ref = synth.bars_1m(front)
        assert len(bars) == len(ref)
        assert bars[0].ts_event == int(ref["ts"].iloc[0]) + 60 * synth.NS
        assert float(bars[0].close) == pytest.approx(float(ref["close"].iloc[0]))

        # Second build is a no-op; a rebuild rewrites the same counts.
        assert cat.build(p, progress=lambda s: None)["skipped"] == 1
        again = cat.build(p, rebuild=True, progress=lambda s: None)
        assert again["built"] == 1
        assert len(cat.open_catalog(p).trade_ticks(instrument_ids=["ESM6.SIM"])) == len(front)
    finally:
        paths_mod.configure(data_dir=paths_mod.REPO_ROOT / "data", market_data_dir=paths_mod.REPO_ROOT / "market-data")
