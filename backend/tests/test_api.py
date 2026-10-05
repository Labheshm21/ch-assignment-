from app.main import app, health


def test_health_endpoint() -> None:
    assert app.title == "Chronos Relay API"
    assert health()["status"] == "ok"



