from datetime import date

from scripts.test_es_closing_momentum import candidates, pnl, signal, summarize


def test_contract_pnl_and_flat_costs():
    assert pnl(1, 6000, 6002, 50, .25, 2.25, 1) == (100, 70.5)
    assert pnl(-1, 6000, 5998, 50, .25, 2.25, 1) == (100, 70.5)
    assert pnl(1, 6000, 5998, 50, .25, 2.25, 2) == (-100, -154.5)
    assert pnl(0, 6000, 6010, 50, .25, 2.25, 1) == (0, 0)
    assert [signal(2, 1), signal(1, 2), signal(1, 1)] == [1, -1, 0]


def test_roll_uses_previously_selected_outright_and_distinct_fill_price():
    a, b = date(2026, 3, 12), date(2026, 3, 13)
    def row(close, sig, entry):
        return dict(n=390, close=close, signal=sig, entry=entry, exit=6010)
    daily = {(a, 'ESH6'): row(6000, 5990, 5991),
             (a, 'ESM6'): row(6050, 6040, 6041),
             (b, 'ESH6'): row(6010, 6001, 6002),
             (b, 'ESM6'): row(6060, 6051, 6052)}
    rows, skipped = candidates(daily, {a: 'ESH6', b: 'ESM6'})
    assert not skipped
    assert rows[0]['symbol'] == 'ESH6'
    assert rows[0]['previous_close'] == 6000
    assert rows[0]['momentum_direction'] == 1
    assert rows[0]['signal_price'] == 6001
    assert rows[0]['entry_reference'] == 6002


def test_short_session_is_excluded():
    a, b = date(2026, 1, 19), date(2026, 1, 20)
    rows = {(a, 'ESH6'): dict(n=210, close=None, signal=None, entry=None, exit=None),
            (b, 'ESH6'): dict(n=390, close=6000, signal=5990, entry=5991, exit=6000)}
    eligible, skipped = candidates(rows, {a: 'ESH6', b: 'ESH6'})
    assert eligible == []
    assert len(skipped) == 1


def test_drawdown_includes_initial_loss():
    rows = [dict(direction=1, net_usd=x, gross_usd=x) for x in [-100, 50, -75]]
    result = summarize(rows)
    assert result['net_usd'] == -125
    assert result['max_drawdown_usd'] == 125
