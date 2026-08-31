"""Market data routes: symbols, OHLCV, range, CVD, DOM snapshot, DOM heatmap."""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

import data_store

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/symbols")
def list_symbols():
    return {"symbols": data_store.list_symbols()}


@router.get("/ohlcv")
def get_ohlcv(
    symbol: str = Query(..., description="Ticker symbol, e.g. ES1!"),
    interval: str = Query("1min", description="Pandas offset alias: 1min, 5min, 1h, 1D, ..."),
    start: int | None = Query(None, description="Clip to bars at/after this unix timestamp"),
    end: int | None = Query(None, description="Clip to bars at/before this unix timestamp"),
):
    # bars_to_records() already returns plain int/float/dict — skip FastAPI's
    # default jsonable_encoder() pass (it's a generic recursive encoder built
    # for arbitrary/pydantic types, and that per-item overhead dominates the
    # response time once a payload gets into the 100k-row range).
    records = data_store.bars_to_records(data_store.get_bars(symbol, interval, start, end))
    return JSONResponse(content=records)


@router.get("/range")
def get_range(symbol: str = Query(...)):
    """First/last available bar time — lets the chart size its initial
    request, and bounds replay selection.

    Answered from min/max timestamps, not by building the 1-minute series:
    this is on the critical path of the first paint, and aggregating every
    tick just to read its two endpoints cost ~16s.
    """
    start, end = data_store.data_range(symbol)
    return {"start": start, "end": end}


@router.get("/cvd")
def get_cvd(
    symbol: str = Query(...),
    interval: str = Query("1min"),
    start: int | None = Query(None),
    end: int | None = Query(None),
):
    """Cumulative Volume Delta, bucketed like /api/ohlcv so it lines up with the chart."""
    series = data_store.get_cvd(symbol, interval, start, end)
    return [{"time": int(ts.timestamp()), "cvd": round(float(v), 2)} for ts, v in series.items()]


@router.get("/dom")
def get_dom(
    symbol: str = Query(...),
    as_of: int | None = Query(None, description="Unix seconds; defaults to the latest event"),
    depth: int = Query(12, ge=1, le=50),
):
    """Approximate order-book snapshot reconstructed from recent Add/Cancel/Fill events."""
    return data_store.order_book_snapshot(symbol, as_of, depth)


# One-second snapshots preserve order-removal timing on the intraday views
# where traders inspect touches and absorptions. Wider views coarsen
# automatically, keeping the total number of time buckets bounded.
_MAX_HEATMAP_BUCKETS = 7200

# The materialised read model makes request cost proportional to visible
# buckets, not to the number of raw order messages preceding the viewport.
# Keep the span clamp as a payload/canvas guardrail.
_MAX_HEATMAP_SPAN_SECONDS = 6 * 3600


@router.get("/dom-heatmap")
def get_dom_heatmap(
    symbol: str = Query(...),
    start: int = Query(...),
    end: int = Query(...),
    depth: int = Query(30, ge=1, le=50),
    min_price: float | None = Query(None),
    max_price: float | None = Query(None),
):
    """Persistent resting size from the materialised one-second read model."""
    if end < start:
        raise HTTPException(400, "end must be greater than or equal to start")
    if (min_price is None) != (max_price is None):
        raise HTTPException(400, "min_price and max_price must be provided together")
    if min_price is not None and min_price >= max_price:
        raise HTTPException(400, "min_price must be less than max_price")
    end = min(end, start + _MAX_HEATMAP_SPAN_SECONDS)
    span = max(1, end - start)
    bucket_seconds = max(1, -(-span // _MAX_HEATMAP_BUCKETS))  # ceil div, min 1s
    return data_store.get_dom_heatmap(
        symbol, start, end, bucket_seconds, depth, min_price, max_price,
    )
