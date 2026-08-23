from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import CostCenter

SEED_CENTRES = (
    ("100", "Správa", "100"),
    ("200", "Vývoj", "200"),
    ("300", "Obchod", "300"),
)


def seed() -> None:
    with SessionLocal.begin() as db:
        for code, name, pohoda_code in SEED_CENTRES:
            if not db.scalar(select(CostCenter).where(CostCenter.code == code)):
                db.add(CostCenter(code=code, name=name, pohoda_code=pohoda_code))


if __name__ == "__main__":
    seed()
