from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app, settings
from app.models import Contact, Workspace


def test_automation_import_requires_key_and_stays_in_configured_workspace() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        workspace = Workspace(name="Automation client")
        other_workspace = Workspace(name="Other client")
        db.add_all([workspace, other_workspace])
        db.commit()
        workspace_id = workspace.id
        other_workspace_id = other_workspace.id

    def override_db():
        with Session(engine) as db:
            yield db

    previous_key = settings.automation_api_key
    previous_workspace_id = settings.automation_workspace_id
    settings.automation_api_key = "test-automation-key"
    settings.automation_workspace_id = workspace_id
    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    files = {
        "file": (
            "apollo.csv",
            (
                "name,email,company,organization_website_url,source,use_case,technical_fit,buying_signal\n"
                "Ada Lovelace,ada@example.com,Analytical Engines,analytical.example.com,Apollo,gaming,high,strong\n"
                "Broken,not-an-email,Nowhere,,Apollo,other,unknown,none\n"
            ),
            "text/csv",
        )
    }

    try:
        assert client.post(
            f"/api/automation/workspaces/{workspace_id}/contacts/import",
            files=files,
        ).status_code == 401
        assert client.post(
            f"/api/automation/workspaces/{other_workspace_id}/contacts/import",
            files=files,
            headers={"X-Relay-Automation-Key": "test-automation-key"},
        ).status_code == 404

        response = client.post(
            f"/api/automation/workspaces/{workspace_id}/contacts/import",
            files=files,
            headers={"X-Relay-Automation-Key": "test-automation-key"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["added"] == 1
        assert result["updated"] == 0
        assert result["rejected"] == 1
        assert result["errors"][0]["row"] == 3

        duplicate = client.post(
            f"/api/automation/workspaces/{workspace_id}/contacts/import",
            files=files,
            headers={"X-Relay-Automation-Key": "test-automation-key"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["updated"] == 1

        with Session(engine) as db:
            contact = db.execute(select(Contact)).scalar_one()
            assert contact.workspace_id == workspace_id
            assert contact.lead_source == "Apollo"
            assert contact.website == "https://analytical.example.com/"
            assert contact.fit_score == 85
    finally:
        app.dependency_overrides.clear()
        settings.automation_api_key = previous_key
        settings.automation_workspace_id = previous_workspace_id
