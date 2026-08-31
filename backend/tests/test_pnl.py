from engine import pnl
from config.instruments import get_root

ES = pnl.ContractSpec.from_root(get_root("ES"))
NQ = pnl.ContractSpec.from_root(get_root("NQ"))


def test_es_long_two_points():
    # Long 1 ES from 5000.00 to 5002.00 = 8 ticks = $100 gross, $4.50 commission round trip.
    assert pnl.pnl_points(5000.0, 5002.0, "long") == 2.0
    assert pnl.points_to_ticks(2.0, ES) == 8
    assert pnl.gross_pnl_usd(5000.0, 5002.0, "long", 1, ES) == 100.0
    assert pnl.commission_usd(1, ES) == 4.5
    assert pnl.net_pnl_usd(5000.0, 5002.0, "long", 1, ES) == 95.5


def test_es_short_loss_three_contracts():
    # Short 3 ES at 5000, stopped at 5001.25 (5 ticks): -$62.50 × 3 = -187.50, minus $13.50 commission.
    assert pnl.gross_pnl_usd(5000.0, 5001.25, "short", 3, ES) == -187.5
    assert pnl.net_pnl_usd(5000.0, 5001.25, "short", 3, ES) == -201.0


def test_nq_multiplier():
    # NQ: $5 per tick, $20 per point. Long 2 from 18000 to 18010.5 = +10.5 pts = $210 × 2.
    assert pnl.gross_pnl_usd(18000.0, 18010.5, "long", 2, NQ) == 420.0
    assert pnl.net_pnl_usd(18000.0, 18010.5, "long", 2, NQ) == 420.0 - 9.0


def test_r_multiple():
    assert pnl.r_multiple(5000.0, 5004.0, 4998.0, "long") == 2.0
    assert pnl.r_multiple(5000.0, 4998.0, 4998.0, "long") == -1.0
    assert pnl.r_multiple(5000.0, 4996.0, 5002.0, "short") == 2.0
    assert pnl.r_multiple(5000.0, 5001.0, None, "long") is None
    assert pnl.r_multiple(5000.0, 5001.0, 5000.0, "long") is None


def test_fixed_risk_sizing():
    # 100k × 0.5% = $500 risk; 8-tick stop on ES = $100/contract -> 5 contracts, capped at max.
    assert pnl.contracts_fixed_risk(100_000, 0.5, 8, ES, 10) == 5
    assert pnl.contracts_fixed_risk(100_000, 0.5, 8, ES, 3) == 3
    # Tiny account: never below 1.
    assert pnl.contracts_fixed_risk(5_000, 0.5, 8, ES, 5) == 1
    # NQ: $500 / (8 × $5) = 12.5 -> 12.
    assert pnl.contracts_fixed_risk(100_000, 0.5, 8, NQ, 20) == 12
    assert pnl.contracts_vol_scaled(100_000, 1.0, 5.0, ES, 10) == 4   # ATR 5 pts = 20 ticks = $250 -> 1000/250


def test_slippage_and_tick_rounding():
    assert pnl.apply_slippage(5000.0, "long", 1, ES, entering=True) == 5000.25
    assert pnl.apply_slippage(5000.0, "long", 1, ES, entering=False) == 4999.75
    assert pnl.apply_slippage(5000.0, "short", 1, ES, entering=True) == 4999.75
    assert pnl.apply_slippage(5000.0, "short", 2, ES, entering=False) == 5000.5
    assert pnl.round_to_tick(5000.13, ES) == 5000.25
    assert pnl.round_to_tick(5000.12, ES) == 5000.0
