from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.main import current_workspace
from app.models import Contact, User, Workspace, WorkspaceMember
from app.schemas import ContactUpsertRequest
from app.services.contacts import (
    export_contacts_csv,
    get_or_create_personal_workspace,
    import_contacts_csv,
    upsert_contact,
)


def make_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_creates_one_personal_workspace_for_existing_user() -> None:
    with make_session() as db:
        user = User(email="labhesh@example.com", google_credentials_encrypted="encrypted")
        db.add(user)
        db.commit()

        first = get_or_create_personal_workspace(db, user)
        db.commit()
        second = get_or_create_personal_workspace(db, user)

        memberships = list(
            db.execute(select(WorkspaceMember).where(WorkspaceMember.user_id == user.id)).scalars()
        )
        assert first.id == second.id
        assert first.name == "Labhesh's Workspace"
        assert len(memberships) == 1
        assert memberships[0].role == "owner"


def test_upsert_uses_case_insensitive_email_without_erasing_missing_fields() -> None:
    with make_session() as db:
        user = User(email="owner@example.com", google_credentials_encrypted="encrypted")
        db.add(user)
        db.commit()
        workspace = get_or_create_personal_workspace(db, user)

        first, created = upsert_contact(
            db,
            workspace_id=workspace.id,
            payload=ContactUpsertRequest(
                email="Founder@Example.com",
                name="Ada Lovelace",
                company="Analytical Engines",
                tags=["Founder"],
            ),
        )
        second, created_again = upsert_contact(
            db,
            workspace_id=workspace.id,
            payload=ContactUpsertRequest(
                email="founder@example.com",
                job_title="CEO",
            ),
        )

        assert created is True
        assert created_again is False
        assert first.id == second.id
        assert second.name == "Ada Lovelace"
        assert second.company == "Analytical Engines"
        assert second.job_title == "CEO"
        assert second.tags == ["Founder"]


def test_csv_import_adds_updates_and_rejects_rows() -> None:
    with make_session() as db:
        user = User(email="owner@example.com", google_credentials_encrypted="encrypted")
        db.add(user)
        db.commit()
        workspace = get_or_create_personal_workspace(db, user)
        upsert_contact(
            db,
            workspace_id=workspace.id,
            payload=ContactUpsertRequest(email="ada@example.com", name="Ada"),
        )

        result = import_contacts_csv(
            db,
            workspace_id=workspace.id,
            content=(
                "name,email,company,job_title,tags\n"
                "Ada Lovelace,ada@example.com,Analytical Engines,Founder,VIP\n"
                "Grace Hopper,grace@example.com,Navy,Admiral,Technical;VIP\n"
                "Broken,not-an-email,Nowhere,Unknown,\n"
            ).encode(),
        )
        db.commit()

        contacts = list(db.execute(select(Contact).order_by(Contact.email)).scalars())
        assert result.added == 1
        assert result.updated == 1
        assert result.rejected == 1
        assert result.errors[0].row == 4
        assert len(contacts) == 2
        assert contacts[0].name == "Ada Lovelace"
        assert contacts[1].tags == ["Technical", "VIP"]


def test_csv_export_quotes_values_and_prevents_spreadsheet_formulas() -> None:
    contact = Contact(
        workspace_id="workspace-id",
        name="Doe, Jane",
        email="jane@example.com",
        company="=RISKY()",
        job_title="Founder",
        tags=["VIP", "Technical"],
    )

    exported = export_contacts_csv([contact])

    assert exported.startswith("name,email,company,job_title,tags\n")
    assert '"Doe, Jane"' in exported
    assert "'=RISKY()" in exported
    assert "VIP;Technical" in exported


def test_active_workspace_is_selected_only_from_users_memberships() -> None:
    with make_session() as db:
        user = User(email="owner@example.com", google_credentials_encrypted="encrypted")
        db.add(user)
        db.commit()
        personal = get_or_create_personal_workspace(db, user)
        client = Workspace(name="Acme Client")
        db.add(client)
        db.flush()
        db.add(WorkspaceMember(workspace_id=client.id, user_id=user.id, role="admin"))
        db.commit()

        request = SimpleNamespace(session={"workspace_id": client.id})
        selected = current_workspace(request=request, user=user, db=db)
        assert selected.id == client.id

        request.session["workspace_id"] = "workspace-the-user-cannot-access"
        fallback = current_workspace(request=request, user=user, db=db)
        assert fallback.id == personal.id
        assert request.session["workspace_id"] == personal.id


def test_same_contact_email_can_exist_in_separate_workspaces() -> None:
    with make_session() as db:
        user = User(email="owner@example.com", google_credentials_encrypted="encrypted")
        db.add(user)
        db.commit()
        first_workspace = get_or_create_personal_workspace(db, user)
        second_workspace = Workspace(name="Second Client")
        db.add(second_workspace)
        db.flush()

        first, first_created = upsert_contact(
            db,
            workspace_id=first_workspace.id,
            payload=ContactUpsertRequest(email="lead@example.com", name="First copy"),
        )
        second, second_created = upsert_contact(
            db,
            workspace_id=second_workspace.id,
            payload=ContactUpsertRequest(email="lead@example.com", name="Second copy"),
        )

        assert first_created is True
        assert second_created is True
        assert first.id != second.id
