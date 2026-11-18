"""Read every user and item through the application's own models and serialise them like the API does."""
from sqlmodel import Session, select

from app.core.db import engine
from app import models

UserOut = getattr(models, "UserPublic", None) or models.UserOut
ItemOut = getattr(models, "ItemPublic", None) or models.ItemOut

with Session(engine) as session:
    users = [UserOut.model_validate(u) for u in session.exec(select(models.User)).all()]
    items = [ItemOut.model_validate(i) for i in session.exec(select(models.Item)).all()]
print("read", len(users), "users and", len(items), "items")
