import pandas as pd
import pytest

from engine.research_audit import audit_bar_sequence


def frame():
    return pd.DataFrame({"open": [100.] * 8, "high": [101.] * 8, "low": [99.] * 8,
                         "close": [100.] * 8, "volume": [1.] * 7 + [0.]},
                        index=pd.date_range("2026-06-01 13:30", periods=8, freq="min", tz="UTC"))


def test_flat_run_reset_at_roll_and_gap_no_mutation():
    bars = frame()
    before = bars.copy(deep=True)
    contracts = ["ESM6"] * 5 + ["ESU6"] * 3
    result = audit_bar_sequence(bars, contracts)
    assert result["flatCloseRows"] == [4]
    assert result["zeroVolumeRows"] == [7]
    assert result["contractSwitches"] == [{"row": 5, "from": "ESM6", "to": "ESU6", "rawCloseGapPoints": 0.0}]
    pd.testing.assert_frame_equal(bars, before)
    bars.index = bars.index[:4].append(bars.index[4:] + pd.Timedelta(minutes=1))
    result = audit_bar_sequence(bars, ["ESM6"] * 8)
    assert result["nonMinuteBoundaryRows"] == [4]
    assert result["flatCloseRows"] == []


def test_roll_jump_recorded_without_repair_or_return():
    bars = frame()
    bars.loc[bars.index[4]:, ["open", "high", "low", "close"]] += 20
    result = audit_bar_sequence(bars, ["ESM6"] * 4 + ["ESU6"] * 4)
    assert result["contractSwitches"][0]["rawCloseGapPoints"] == 20
    assert bars.close.iloc[4] == 120


@pytest.mark.parametrize("kind", ["duplicate", "nan", "ohlc", "naive", "contracts", "threshold"])
def test_reuses_existing_fail_closed_validation(kind):
    bars, contracts, threshold = frame(), ["ESM6"] * 8, 5
    if kind == "duplicate":
        bars.index = pd.DatetimeIndex([bars.index[0]] * len(bars))
    elif kind == "nan":
        bars.iloc[0, 0] = float("nan")
    elif kind == "ohlc":
        bars.iloc[0, 1] = 1
    elif kind == "naive":
        bars.index = bars.index.tz_localize(None)
    elif kind == "contracts":
        contracts = [""] * 8
    else:
        threshold = True
    with pytest.raises(ValueError):
        audit_bar_sequence(bars, contracts, stale_bars=threshold)


def test_numeric_strings_use_existing_validator_normalization():
    bars = frame().astype(str)
    result = audit_bar_sequence(bars, ["ESM6"] * 4 + ["ESU6"] * 4)
    assert result["zeroVolumeRows"] == [7]
    assert result["contractSwitches"][0]["rawCloseGapPoints"] == 0.0
    assert bars.volume.iloc[-1] == "0.0"
