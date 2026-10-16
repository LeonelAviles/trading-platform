"""Reusable data preparation and inventory, independent of model approval."""
from datetime import date

from fastapi import APIRouter, Query

from config.instruments import load_instruments
from research_ml import feature_store
from research_ml.schema import MBOFeatureRequest
from routers.agent import _research_call

router = APIRouter(prefix="/api/ml/features", tags=["ml-features"])


@router.post("/prepare")
def prepare(body: MBOFeatureRequest):
    return _research_call(feature_store.prepare, body, load_instruments().roots["ES"].tick_size)


@router.get("")
def inventory(contract: str | None = Query(default=None, pattern=r"^ES[HMUZ][0-9]{1,2}$"),
              start: date | None = None, end: date | None = None):
    return _research_call(feature_store.inventory, contract, start, end)
