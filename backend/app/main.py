import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import Base, engine, get_db
from .models import EmailStatus, ScheduledEmail, User
from .schemas import (
    AuthStatusResponse,
    ParseIntentRequest,
    ParsedEmailIntent,
    ScheduleBatchResponse,
    ScheduleEmailRequest,
    ScheduledEmailResponse,
)
from .security import CredentialCipher
from .services.intent import parse_email_intent
from .services.oauth import build_oauth_flow, serialize_credentials
from .services.scheduling import create_schedule_batch


settings = get_settings()
logger = logging.getLogger("chronos-api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Chronos Relay API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.app_secret_key,
    same_site="lax",
    https_only=settings.app_env == "production",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Connect Gmail first")
    return user


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/auth/google")
def google_login(request: Request) -> RedirectResponse:
    try:
        flow = build_oauth_flow()
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    request.session["oauth_state"] = state
    request.session["oauth_code_verifier"] = flow.code_verifier
    return RedirectResponse(authorization_url)


@app.get("/auth/google/callback")
def google_callback(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    expected_state = request.session.get("oauth_state")
    code_verifier = request.session.get("oauth_code_verifier")
    incoming_state = request.query_params.get("state")
    if not expected_state or incoming_state != expected_state or not code_verifier:
        raise HTTPException(status_code=400, detail="OAuth state mismatch")

    flow = build_oauth_flow(state=expected_state, code_verifier=code_verifier)
    # Use the configured public callback origin so OAuth also works behind a
    # production reverse proxy such as Caddy or an AWS load balancer.
    authorization_response = f"{settings.google_redirect_uri}?{request.url.query}"
    flow.fetch_token(authorization_response=authorization_response)
    credentials: Credentials = flow.credentials
    oauth_service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    profile = oauth_service.userinfo().get().execute()
    email = profile["email"]

    encrypted = CredentialCipher().encrypt(serialize_credentials(credentials))
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email, google_credentials_encrypted=encrypted)
    else:
        user.google_credentials_encrypted = encrypted
    db.add(user)
    db.commit()
    db.refresh(user)

    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(f"{settings.frontend_url}?connected=1")


@app.get("/auth/status", response_model=AuthStatusResponse)
def auth_status(request: Request, db: Session = Depends(get_db)) -> AuthStatusResponse:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    return AuthStatusResponse(connected=user is not None, email=user.email if user else None)


@app.post("/auth/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.post("/api/intents/parse", response_model=ParsedEmailIntent)
def parse_intent(
    payload: ParseIntentRequest,
    _: User = Depends(current_user),
) -> ParsedEmailIntent:
    try:
        return parse_email_intent(payload.message, payload.timezone)
    except RuntimeError as exc:
        logger.exception("Intent parsing failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/emails/schedule", response_model=ScheduleBatchResponse)
def schedule_emails(
    payload: ScheduleEmailRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ScheduleBatchResponse:
    try:
        jobs = create_schedule_batch(db, user_id=user.id, request=payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ScheduleBatchResponse(jobs=jobs)


@app.get("/api/emails", response_model=list[ScheduledEmailResponse])
def list_emails(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[ScheduledEmail]:
    return list(
        db.execute(
            select(ScheduledEmail)
            .where(ScheduledEmail.user_id == user.id)
            .order_by(ScheduledEmail.scheduled_at.desc())
            .limit(100)
        ).scalars()
    )


@app.delete("/api/emails/{job_id}", response_model=ScheduledEmailResponse)
def cancel_email(
    job_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> ScheduledEmail:
    job = db.get(ScheduledEmail, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status_code=404, detail="Scheduled email not found")
    if job.status != EmailStatus.pending.value:
        raise HTTPException(status_code=409, detail="Only pending emails can be cancelled")
    job.status = EmailStatus.cancelled.value
    db.commit()
    db.refresh(job)
    return job

