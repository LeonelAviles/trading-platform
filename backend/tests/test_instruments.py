from config.instruments import load_instruments, get_root


def test_roots_loaded():
    ins = load_instruments()
    assert set(ins.roots) >= {"ES", "NQ", "MES", "MNQ"}
    es = get_root("ES")
    assert es.tick_size == 0.25 and es.tick_value == 12.5 and es.multiplier == 50
    assert es.points_to_usd(2.0, 3) == 300.0
    assert es.ticks_to_usd(4) == 50.0
    nq = get_root("NQ")
    assert nq.multiplier == 20 and nq.ticks_to_usd(1) == 5.0


def test_symbol_resolution():
    ins = load_instruments()
    assert ins.root_for_symbol("ES1!").root == "ES"
    assert ins.root_for_symbol("ESM6").root == "ES"
    assert ins.root_for_symbol("MESM6").root == "MES"
    assert ins.root_for_symbol("NQU6").root == "NQ"
    assert ins.root_for_symbol("ESM6-ESU6") is None
    assert ins.root_for_symbol("ESZ26") is None
    assert ins.is_continuous("NQ1!") and not ins.is_continuous("ESM6")
    assert ins.is_outright("ESM6") and not ins.is_outright("ES1!")


def test_session_and_costs():
    ins = load_instruments()
    assert ins.session.timezone == "America/New_York"
    assert ins.session.rth_start == "09:30" and ins.session.rth_end == "16:00"
    assert ins.session.flatten_before_close_minutes == 2
    assert ins.costs.limit_fill_rule == "trade_through"
    d = ins.to_dict()
    assert d["roots"]["ES"]["continuous"] == "ES1!"
