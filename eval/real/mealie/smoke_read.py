"""Read-only schema smoke test for a Mealie release (run from the release checkout).

Selects up to one row from every table the release's own ORM models map, so a missing or renamed column,
or a changed type the model cannot load, makes it fail. It never writes, and it does not start the application
(Mealie's own test suite and startup run migrations themselves, which would hide the state under test).
"""
import sys

import mealie.db.models._all_models  # noqa: F401  registers every model
from mealie.db.db_setup import SessionLocal
from mealie.db.models._model_base import SqlAlchemyBase

failures = []
checked = 0
for mapper in SqlAlchemyBase.registry.mappers:
    cls = mapper.class_
    with SessionLocal() as session:
        try:
            session.query(cls).limit(1).all()
            checked += 1
        except Exception as exc:  # noqa: BLE001 - report every model that cannot be read
            failures.append(f"{cls.__name__} ({cls.__tablename__}): {str(exc).splitlines()[0][:160]}")
print(f"selected from {checked} mapped tables; {len(failures)} failed")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
