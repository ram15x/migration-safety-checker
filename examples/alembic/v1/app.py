"""Version 1 of the example application: users have an email."""
import os
import sys

from sqlalchemy import create_engine, text


def engine():
    return create_engine(os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+psycopg://", 1))


def add_user(email):
    with engine().begin() as conn:
        conn.execute(text("INSERT INTO users (email) VALUES (:e)"), {"e": email})


def emails():
    with engine().connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT email FROM users ORDER BY id"))]


if __name__ == "__main__":
    if sys.argv[1:] == ["write"]:
        add_user("written@example.com")
    elif sys.argv[1:] == ["read"]:
        print(emails())
