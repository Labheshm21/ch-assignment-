# Chronos Relay

Chronos Relay is a cloud-ready Gmail scheduling agent. A user connects Gmail, describes an email request in natural language, reviews the parsed plan, and confirms it. Durable database jobs are then processed by a separate worker, so delivery continues after the browser and the user's computer are closed.

## What is implemented

- Google OAuth 2.0 with the minimal `gmail.send` permission
- Natural-language extraction with an NVIDIA NIM hosted model
- Explicit confirmation before any job is created
- One-time and interval batches (for example, 10 emails one hour apart)
- PostgreSQL/SQLite persistence
- Separate delivery worker with row locking, stale-lock recovery, retries, and terminal failure state
- Pending/sent/failed/cancelled dashboard and cancellation
- Encrypted OAuth credential storage
- Docker Compose for the frontend, API, worker, and PostgreSQL
- Unit tests for schedule expansion, timezone conversion, and Gmail MIME generation

The NVIDIA model only produces a proposed intent. Application code validates it against a typed schema and stores the schedule; the model never receives Gmail credentials and never sends an email directly.

## Architecture

```text
Next.js browser UI
        |
        v
FastAPI API -----> NVIDIA NIM API (intent extraction only)
        |
        v
PostgreSQL durable queue
        |
        v
Python worker -----> Gmail API
```

## Prerequisites

- Node.js 22+
- Python 3.11+
- Docker Desktop (recommended for PostgreSQL)
- A Google Cloud project with Gmail API enabled
- A free NVIDIA hosted API key from NVIDIA Build

## 1. Configure Google OAuth

1. In Google Cloud Console, create or select a project.
2. Enable **Gmail API**.
3. Configure the OAuth consent screen as **External** and keep it in testing while developing.
4. Add the Gmail accounts used for the demo as test users.
5. Create an OAuth client of type **Web application**.
6. Add this authorized redirect URI exactly:

   ```text
   http://localhost:8000/auth/google/callback
   ```

7. Copy the client ID and client secret into `.env`.

The app requests offline access so the cloud worker can refresh access tokens and send after the browser is closed. Production OAuth credentials must use the deployed HTTPS callback URL.

## 2. Configure environment variables

```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env.local
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Put the generated value in `TOKEN_ENCRYPTION_KEY`, then fill in:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `NVIDIA_API_KEY`
- `APP_SECRET_KEY` (a separate long random value)

Never commit `.env` or real OAuth tokens.

## 3. Run with Docker Compose

```powershell
docker compose up --build
```

Open [http://localhost:3000](http://localhost:3000). The API health endpoint is [http://localhost:8000/health](http://localhost:8000/health).

## Run without Docker

The default `.env.example` uses SQLite, which is convenient for a quick local run.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

Terminal 1:

```powershell
Set-Location backend
uvicorn app.main:app --reload
```

Terminal 2:

```powershell
Set-Location backend
python -m app.worker
```

Terminal 3:

```powershell
Set-Location frontend
npm install
npm run dev
```

## Test and verify

```powershell
Set-Location backend
pytest -q

Set-Location ../frontend
npm run lint
npm run build
```

Recommended demo sequence:

1. Connect a dedicated Gmail test account.
2. Schedule an email to another account for two minutes in the future.
3. Confirm it and close the browser.
4. Show that the worker sends it and the dashboard later reports `sent` with the Gmail message ID.
5. Schedule three emails one minute apart and show the three independent durable jobs.

## AWS demo deployment

The fastest take-home deployment uses one EC2 instance with Docker Compose. It runs the Next.js frontend, FastAPI API, background worker, PostgreSQL, and Caddy. Caddy obtains a trusted HTTPS certificate for an `sslip.io` hostname derived from the instance's Elastic IP. PostgreSQL and certificates use persistent Docker volumes on the EC2 EBS disk.

1. Allocate an Elastic IP and attach it to an Ubuntu EC2 instance.
2. Allow inbound TCP ports 22 (your IP only), 80, and 443, plus UDP 443.
3. Install Git and Docker Engine with the Compose plugin.
4. Clone this repository on the instance.
5. Copy `.env.production.example` to `.env.production`.
6. Set `APP_DOMAIN` to `<ELASTIC_IP>.sslip.io` and use the same hostname with `https://` for `FRONTEND_URL` and `BACKEND_URL`.
7. Fill in fresh application, encryption, Google, NVIDIA, and PostgreSQL secrets.
8. Start the stack:

   ```bash
   docker compose --env-file .env.production -f docker-compose.prod.yml up -d --build
   ```

9. Add `https://<APP_DOMAIN>/auth/google/callback` to the Google OAuth client's authorized redirect URIs.
10. Open `https://<APP_DOMAIN>`, connect Gmail, schedule an email, shut down the local computer, and verify delivery.

For a larger production system, move PostgreSQL to RDS, place API/worker containers on ECS, and store secrets in Secrets Manager.

## Current scope

Attachments, voice input, recurring calendar rules, and arbitrary recipient lists are intentionally outside the first milestone. The core requirement is durable, user-confirmed Gmail delivery from natural language. Attachments can later be stored privately in S3 and referenced by job ID.

## Safety notes

- The UI requires confirmation before scheduling.
- Batch size is capped at 100 emails.
- Only the narrow Gmail send scope is requested.
- OAuth credentials are encrypted at rest with Fernet.
- The worker persists provider errors and uses bounded retries.
- Use only consenting test recipients during development.

