from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CostCenter

SEED_CENTRES = (
    ("IT", "Informační technologie", "IT"),
    ("GIS", "Geografické informační systémy", "GIS"),
    ("PROVOZ", "Provoz", "PROVOZ"),
)


def seed() -> None:
    with SessionLocal.begin() as db:
        for code, name, pohoda_code in SEED_CENTRES:
            if not db.scalar(select(CostCenter).where(CostCenter.code == code)):
                db.add(CostCenter(code=code, name=name, pohoda_code=pohoda_code))


if __name__ == "__main__":
    seed()

