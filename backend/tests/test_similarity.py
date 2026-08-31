from teaching.similarity import report


def _t(entry_time, price, direction="long", exit_price=None, stop=None, pnl=0.0, id_=None):
    return {"id": id_, "entryTime": entry_time, "entryPrice": price, "direction": direction, "exitPrice": exit_price,
            "stopPrice": stop, "pnlUsd": pnl, "exitTime": entry_time + 300}


def test_matching_within_bars_and_ticks():
    t0 = 1_781_271_000
    user = [_t(t0, 5300.0, id_="a", exit_price=5302.0, stop=5299.0, pnl=100),
            _t(t0 + 600, 5310.0, id_="b", exit_price=5309.0, stop=5309.0, pnl=-50),
            _t(t0 + 1200, 5320.0, "short", id_="c", exit_price=5318.0, stop=5321.0, pnl=100)]
    engine = [_t(t0 + 120, 5300.5, exit_price=5302.0, stop=5299.0, pnl=90),      # 2 bars, 2 ticks -> match a
              _t(t0 + 600, 5313.0, exit_price=5310.0, stop=5309.0, pnl=-100),    # 12 ticks away -> no match
              _t(t0 + 1300, 5319.75, "long", exit_price=5321.0, stop=5318.0),    # wrong direction -> no match
              _t(t0 + 1300, 5319.75, "short", exit_price=5317.0, stop=5321.0, pnl=120)]  # match c
    r = report(user, engine, primary_seconds=60, tick_size=0.25, bars=3, ticks=8)
    assert r["matched"] == 2
    assert r["precision"] == 0.5 and r["recall"] == round(2 / 3, 4)
    assert [m["userId"] for m in r["matches"]] == ["a", "c"]
    assert [u["id"] for u in r["unmatchedUser"]] == ["b"]
    assert len(r["unmatchedEngine"]) == 2
    assert r["exitSimilarity"]["medianExitTickDiff"] == 2.0     # |5302-5302|=0, |5318-5317|=4 ticks -> median 2
    assert r["pnl"] == {"user": 150.0, "engine": 110.0}


def test_empty_sides():
    r = report([], [])
    assert r["precision"] is None and r["recall"] is None and r["matched"] == 0
    r2 = report([_t(1, 1.0)], [])
    assert r2["recall"] == 0.0 and r2["precision"] is None
