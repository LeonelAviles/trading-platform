"""Explicit ordered schemas. v1 remains the six-input frozen contract."""
from engine.mbo_features import FEATURES, FEATURE_VERSION

V2 = "mbo_receive_minute_v2"
RICH_FEATURES = [*FEATURES, "session_vwap", "distance_to_session_vwap_ticks",
    "session_vwap_slope_ticks", "signed_aggressive_flow_rate", "aggressive_flow_imbalance",
    "buy_absorption_proxy", "sell_absorption_proxy", "ask_replenishment_rate_proxy",
    "bid_replenishment_rate_proxy", "buy_absorption_replenishment_proxy",
    "sell_absorption_replenishment_proxy", "iceberg_repeat_replenishment_proxy"]


def features_for(version=FEATURE_VERSION):
    if version == FEATURE_VERSION:
        return list(FEATURES)
    if version == V2:
        return list(RICH_FEATURES)
    raise ValueError("unsupported MBO feature version")


def frame_features(columns):
    version = V2 if any(c in columns for c in RICH_FEATURES[len(FEATURES):]) else FEATURE_VERSION
    expected = features_for(version)
    present = [c for c in columns if c in RICH_FEATURES]
    if present != expected:
        raise ValueError("all ordered versioned MBO features required")
    return expected


def model_schema(version):
    return {"featureVersion": version, "version": "frozen_mbo_v2" if version == V2 else "frozen_mbo_v1"}
