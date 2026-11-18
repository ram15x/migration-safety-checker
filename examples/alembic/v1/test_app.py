import app


def test_add_and_read_user():
    before = app.emails()
    app.add_user("test@example.com")
    assert app.emails() == before + ["test@example.com"]
