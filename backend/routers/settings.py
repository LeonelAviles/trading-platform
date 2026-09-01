"""Platform settings — a key/value table in SQLite (PLATFORM-SPEC.md §4.7).

GET returns every key; PUT merges the given keys (replay defaults and the
like live here).
"""

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

import database
from models import Setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
def get_settings(db: Session = Depends(database.get_db)):
    return {row.key: row.value_json for row in db.query(Setting).order_by(Setting.key)}


@router.put("")
def put_settings(values: dict = Body(...), db: Session = Depends(database.get_db)):
    for key, value in values.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value_json=value))
        else:
            row.value_json = value
    db.commit()
    return {row.key: row.value_json for row in db.query(Setting).order_by(Setting.key)}
