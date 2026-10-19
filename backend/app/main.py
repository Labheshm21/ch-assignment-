import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import Base, engine, get_db
from .models import (
    Contact,
    EmailStatus,
    ScheduledEmail,
    User,
    Workspace,
    WorkspaceMember,
    WorkspaceScheduledEmail,
)
from .schemas import (
    AuthStatusResponse,
    CampaignCreateRequest,
    CampaignResponse,
    ContactImportResponse,
    ContactMutationResponse,
    ContactResponse,
    ContactUpsertRequest,
    ParseIntentRequest,
    ParsedEmailIntent,
    ScheduleBatchResponse,
    ScheduleEmailRequest,
    ScheduledEmailResponse,
    WorkspaceCreateRequest,
    WorkspaceResponse,
    WorkspaceUpdateRequest,
)
from .security import CredentialCipher
from .services.contacts import (
    export_contacts_csv,
    get_or_create_personal_workspace,
    import_contacts_csv,
    update_contact,
    upsert_contact,
)
from .services.campaigns import (
    cancel_campaign,
    create_campaign,
    get_campaign,
    list_campaigns,
    serialize_campaign,
)
from .services.intent import parse_email_intent
from .services.oauth import build_oauth_flow, serialize_credentials
from .services.scheduling import create_schedule_batch


settings = get_settings()
logger = logging.getLogger("chronos-api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Relay AI API", version="0.2.0", lifespan=lifespan)
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


def current_workspace(
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Workspace:
    selected_workspace_id = request.session.get("workspace_id")
    membership = None
    if selected_workspace_id:
        membership = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == user.id,
                WorkspaceMember.workspace_id == selected_workspace_id,
            )
        ).scalar_one_or_none()
    workspace = membership.workspace if membership is not None else get_or_create_personal_workspace(db, user)
    # The first request for an existing pre-workspace user creates their workspace.
    # Committing here makes that lazy migration durable even for read-only endpoints.
    db.commit()
    db.refresh(workspace)
    request.session["workspace_id"] = workspace.id
    return workspace


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
    workspace = get_or_create_personal_workspace(db, user)
    db.commit()

    request.session.clear()
    request.session["user_id"] = user.id
    request.session["workspace_id"] = workspace.id
    return RedirectResponse(f"{settings.frontend_url}?connected=1")


@app.get("/auth/status", response_model=AuthStatusResponse)
def auth_status(request: Request, db: Session = Depends(get_db)) -> AuthStatusResponse:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None:
        return AuthStatusResponse(connected=False)

    selected_workspace_id = request.session.get("workspace_id")
    membership = None
    if selected_workspace_id:
        membership = db.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.user_id == user.id,
                WorkspaceMember.workspace_id == selected_workspace_id,
            )
        ).scalar_one_or_none()
    workspace = membership.workspace if membership is not None else get_or_create_personal_workspace(db, user)
    db.commit()
    db.refresh(workspace)
    request.session["workspace_id"] = workspace.id
    credential_payload = CredentialCipher().decrypt(user.google_credentials_encrypted)
    granted_scopes = set(credential_payload.get("scopes") or [])
    return AuthStatusResponse(
        connected=True,
        email=user.email,
        workspace_id=workspace.id,
        workspace_name=workspace.name,
        reply_monitoring_enabled="https://www.googleapis.com/auth/gmail.metadata" in granted_scopes,
    )


@app.post("/auth/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@app.get("/api/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[WorkspaceResponse]:
    memberships = list(
        db.execute(
            select(WorkspaceMember)
            .where(WorkspaceMember.user_id == user.id)
            .order_by(WorkspaceMember.created_at.asc())
        ).scalars()
    )
    if not memberships:
        workspace = get_or_create_personal_workspace(db, user)
        db.commit()
        db.refresh(workspace)
        memberships = list(
            db.execute(
                select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)
            ).scalars()
        )
    return [
        WorkspaceResponse(
            id=membership.workspace.id,
            name=membership.workspace.name,
            role=membership.role,
            created_at=membership.workspace.created_at,
        )
        for membership in memberships
    ]


@app.post("/api/workspaces", response_model=WorkspaceResponse)
def create_workspace(
    payload: WorkspaceCreateRequest,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    workspace = Workspace(name=payload.name)
    db.add(workspace)
    db.flush()
    membership = WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    db.add(membership)
    db.commit()
    db.refresh(workspace)
    request.session["workspace_id"] = workspace.id
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        role="owner",
        created_at=workspace.created_at,
    )


@app.post("/api/workspaces/{workspace_id}/select", response_model=WorkspaceResponse)
def select_workspace(
    workspace_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    membership = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    request.session["workspace_id"] = workspace_id
    return WorkspaceResponse(
        id=membership.workspace.id,
        name=membership.workspace.name,
        role=membership.role,
        created_at=membership.workspace.created_at,
    )


@app.put("/api/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def rename_workspace(
    workspace_id: str,
    payload: WorkspaceUpdateRequest,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> WorkspaceResponse:
    membership = db.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.user_id == user.id,
            WorkspaceMember.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    if membership.role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="Only workspace owners and admins can rename it")
    membership.workspace.name = payload.name
    db.commit()
    db.refresh(membership.workspace)
    return WorkspaceResponse(
        id=membership.workspace.id,
        name=membership.workspace.name,
        role=membership.role,
        created_at=membership.workspace.created_at,
    )


@app.get("/api/contacts", response_model=list[ContactResponse])
def list_contacts(
    search: str | None = Query(default=None, max_length=200),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> list[Contact]:
    statement = select(Contact).where(Contact.workspace_id == workspace.id)
    if search and search.strip():
        needle = f"%{search.strip().casefold()}%"
        statement = statement.where(
            or_(
                Contact.normalized_email.like(needle),
                func.lower(Contact.name).like(needle),
                func.lower(Contact.company).like(needle),
                func.lower(Contact.job_title).like(needle),
            )
        )
    return list(db.execute(statement.order_by(Contact.updated_at.desc())).scalars())


@app.post("/api/contacts", response_model=ContactMutationResponse)
def create_or_upsert_contact(
    payload: ContactUpsertRequest,
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> ContactMutationResponse:
    contact, created = upsert_contact(db, workspace_id=workspace.id, payload=payload)
    db.commit()
    db.refresh(contact)
    return ContactMutationResponse(contact=contact, created=created)


@app.post("/api/contacts/import", response_model=ContactImportResponse)
async def import_contacts(
    file: UploadFile = File(...),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> ContactImportResponse:
    if file.content_type not in {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}:
        raise HTTPException(status_code=415, detail="Upload a CSV file")
    content = await file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(status_code=413, detail="CSV must be 2 MB or smaller")
    try:
        result = import_contacts_csv(db, workspace_id=workspace.id, content=content)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return result


@app.get("/api/contacts/export")
def export_contacts(
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> Response:
    contacts = list(
        db.execute(
            select(Contact)
            .where(Contact.workspace_id == workspace.id)
            .order_by(Contact.created_at.asc())
        ).scalars()
    )
    content = "\ufeff" + export_contacts_csv(contacts)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="relay-contacts.csv"'},
    )


@app.put("/api/contacts/{contact_id}", response_model=ContactResponse)
def edit_contact(
    contact_id: str,
    payload: ContactUpsertRequest,
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> Contact:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    try:
        update_contact(db, contact=contact, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(contact)
    return contact


@app.delete("/api/contacts/{contact_id}")
def delete_contact(
    contact_id: str,
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> dict:
    contact = db.get(Contact, contact_id)
    if contact is None or contact.workspace_id != workspace.id:
        raise HTTPException(status_code=404, detail="Contact not found")
    db.delete(contact)
    db.commit()
    return {"ok": True}


@app.post("/api/campaigns", response_model=CampaignResponse, status_code=201)
def schedule_campaign(
    payload: CampaignCreateRequest,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> CampaignResponse:
    try:
        campaign = create_campaign(
            db,
            user_id=user.id,
            workspace_id=workspace.id,
            request=payload,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return serialize_campaign(campaign)


@app.get("/api/campaigns", response_model=list[CampaignResponse])
def get_campaigns(
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> list[CampaignResponse]:
    return [
        serialize_campaign(campaign)
        for campaign in list_campaigns(db, workspace_id=workspace.id)
    ]


@app.get("/api/campaigns/{campaign_id}", response_model=CampaignResponse)
def get_campaign_detail(
    campaign_id: str,
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> CampaignResponse:
    campaign = get_campaign(db, campaign_id=campaign_id, workspace_id=workspace.id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return serialize_campaign(campaign)


@app.delete("/api/campaigns/{campaign_id}", response_model=CampaignResponse)
def stop_campaign(
    campaign_id: str,
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> CampaignResponse:
    campaign = get_campaign(db, campaign_id=campaign_id, workspace_id=workspace.id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if not any(
        job.status == EmailStatus.pending.value
        for recipient in campaign.recipients
        for job in (
            [delivery.scheduled_email for delivery in recipient.deliveries]
            or [recipient.scheduled_email]
        )
    ):
        raise HTTPException(status_code=409, detail="This campaign has no pending recipients")
    cancel_campaign(campaign)
    db.commit()
    updated_campaign = get_campaign(db, campaign_id=campaign_id, workspace_id=workspace.id)
    if updated_campaign is None:  # pragma: no cover - guarded by the lookup above
        raise HTTPException(status_code=404, detail="Campaign not found")
    return serialize_campaign(updated_campaign)


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
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> ScheduleBatchResponse:
    try:
        jobs = create_schedule_batch(
            db,
            user_id=user.id,
            workspace_id=workspace.id,
            request=payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ScheduleBatchResponse(jobs=jobs)


@app.get("/api/emails", response_model=list[ScheduledEmailResponse])
def list_emails(
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> list[ScheduledEmail]:
    # Assign pre-workspace jobs to the first workspace used after this upgrade.
    legacy_jobs = list(
        db.execute(
            select(ScheduledEmail)
            .outerjoin(
                WorkspaceScheduledEmail,
                WorkspaceScheduledEmail.scheduled_email_id == ScheduledEmail.id,
            )
            .where(
                ScheduledEmail.user_id == user.id,
                WorkspaceScheduledEmail.scheduled_email_id.is_(None),
            )
        ).scalars()
    )
    if legacy_jobs:
        db.add_all(
            [
                WorkspaceScheduledEmail(scheduled_email_id=job.id, workspace_id=workspace.id)
                for job in legacy_jobs
            ]
        )
        db.commit()
    return list(
        db.execute(
            select(ScheduledEmail)
            .join(
                WorkspaceScheduledEmail,
                WorkspaceScheduledEmail.scheduled_email_id == ScheduledEmail.id,
            )
            .where(
                ScheduledEmail.user_id == user.id,
                WorkspaceScheduledEmail.workspace_id == workspace.id,
            )
            .order_by(ScheduledEmail.scheduled_at.desc())
            .limit(100)
        ).scalars()
    )


@app.delete("/api/emails/{job_id}", response_model=ScheduledEmailResponse)
def cancel_email(
    job_id: str,
    user: User = Depends(current_user),
    workspace: Workspace = Depends(current_workspace),
    db: Session = Depends(get_db),
) -> ScheduledEmail:
    job = db.get(ScheduledEmail, job_id)
    assignment = db.get(WorkspaceScheduledEmail, job_id)
    if (
        job is None
        or job.user_id != user.id
        or (assignment is not None and assignment.workspace_id != workspace.id)
    ):
        raise HTTPException(status_code=404, detail="Scheduled email not found")
    if assignment is None:
        db.add(WorkspaceScheduledEmail(scheduled_email_id=job.id, workspace_id=workspace.id))
    if job.status != EmailStatus.pending.value:
        raise HTTPException(status_code=409, detail="Only pending emails can be cancelled")
    job.status = EmailStatus.cancelled.value
    db.commit()
    db.refresh(job)
    return job

