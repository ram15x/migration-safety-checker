"""Create one user and one item through the application's own code (run from the release's backend directory)."""
import uuid

from sqlmodel import Session

from app import crud
from app.core.db import engine
from app.models import ItemCreate, UserCreate

tag = uuid.uuid4().hex[:8]
with Session(engine) as session:
    user = crud.create_user(
        session=session,
        user_create=UserCreate(email=f"w-{tag}@example.com", password="correct-horse-battery"),
    )
    crud.create_item(session=session, item_in=ItemCreate(title=f"item {tag}"), owner_id=user.id)
print("wrote", tag)
