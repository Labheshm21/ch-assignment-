import os
from urllib.parse import urlparse

from google_auth_oauthlib.flow import Flow

from ..config import get_settings


GMAIL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
]


def build_oauth_flow(
    *,
    state: str | None = None,
    code_verifier: str | None = None,
) -> Flow:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise RuntimeError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required")

    redirect_uri = settings.google_redirect_uri
    parsed_redirect = urlparse(redirect_uri)
    if (
        settings.app_env == "development"
        and parsed_redirect.scheme == "http"
        and parsed_redirect.hostname in {"localhost", "127.0.0.1"}
    ):
        # OAuthlib blocks plain HTTP by default. Google permits loopback HTTP for
        # installed development apps, so allow it only for this local callback.
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

    config = {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(
        config,
        scopes=GMAIL_SCOPES,
        state=state,
        code_verifier=code_verifier,
        autogenerate_code_verifier=code_verifier is None,
    )
    flow.redirect_uri = redirect_uri
    return flow


def serialize_credentials(credentials) -> dict:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": list(credentials.scopes or GMAIL_SCOPES),
    }

