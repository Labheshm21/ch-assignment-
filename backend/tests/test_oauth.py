from datetime import datetime, timezone
from types import SimpleNamespace

from google.oauth2.credentials import Credentials

from app.services import oauth


def test_local_oauth_allows_loopback_http(monkeypatch) -> None:
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    monkeypatch.setattr(
        oauth,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_redirect_uri="http://localhost:8000/auth/google/callback",
        ),
    )

    flow = oauth.build_oauth_flow()

    assert flow.redirect_uri == "http://localhost:8000/auth/google/callback"
    assert oauth.os.environ["OAUTHLIB_INSECURE_TRANSPORT"] == "1"


def test_production_oauth_does_not_allow_insecure_transport(monkeypatch) -> None:
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    monkeypatch.setattr(
        oauth,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="production",
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_redirect_uri="https://api.example.com/auth/google/callback",
        ),
    )

    oauth.build_oauth_flow()

    assert "OAUTHLIB_INSECURE_TRANSPORT" not in oauth.os.environ


def test_oauth_flow_preserves_pkce_verifier(monkeypatch) -> None:
    monkeypatch.setattr(
        oauth,
        "get_settings",
        lambda: SimpleNamespace(
            app_env="development",
            google_client_id="client-id",
            google_client_secret="client-secret",
            google_redirect_uri="http://localhost:8000/auth/google/callback",
        ),
    )

    flow = oauth.build_oauth_flow(state="state", code_verifier="verifier")

    assert flow.code_verifier == "verifier"


def test_credentials_round_trip_expiry() -> None:
    expiry = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    credentials = Credentials(
        token="access-token",
        refresh_token="refresh-token",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client-id",
        client_secret="client-secret",
        scopes=["scope"],
        expiry=expiry,
    )

    restored = oauth.credentials_from_payload(oauth.serialize_credentials(credentials))

    assert restored.token == "access-token"
    assert restored.refresh_token == "refresh-token"
    assert restored.expiry == expiry.replace(tzinfo=None)
