# STRADDLY

STRADDLY is a full-stack trading platform with:
- FastAPI backend
- React + Vite frontend
- PostgreSQL database
- Real-time market feed support
- Paper order simulation and portfolio workflows

This README is intentionally self-contained and does not depend on separate deployment docs.

## Project Layout

```text
STRADDLY/
  app/                    FastAPI backend
  frontend/               React frontend
  migrations/             SQL migrations
  instrument_master/      Instrument and market master files
  mockdhan/               Local mock market-data service
  docker-compose.yml      Main container setup
  docker-compose.prod.yml Production-oriented compose file
  requirements.txt        Python dependencies
```

## Prerequisites

Option A (recommended):
- Docker Desktop with Docker Compose

Option B (native run):
- Python 3.11+
- Node.js 18+
- PostgreSQL 14+

## Quick Start (Docker)

1. Create local env file:

```bash
cp .env.example .env
```

2. Fill required values in .env:
- DB_PASSWORD
- DHAN_CLIENT_ID
- DHAN_API_KEY / DHAN_API_SECRET or DHAN_ACCESS_TOKEN
- DHAN_PIN / DHAN_TOTP_SECRET (if using TOTP mode)
- MESSAGE_CENTRAL_CUSTOMER_ID / MESSAGE_CENTRAL_PASSWORD
- EMAIL_OTP_SERVICE_BASE_URL

3. Start services:

```bash
docker compose up -d --build
```

4. Open:
- Frontend: http://localhost
- Backend health: http://localhost:8000/health
- API docs: http://localhost:8000/api/v2/docs

5. Stop services:

```bash
docker compose down
```

## Quick Start (Native)

### Backend

1. Create virtual environment and install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

2. Create env file:

```bash
cp .env.example .env
```

3. Run backend from project root:

```bash
uvicorn app.main:create_app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

1. Install frontend dependencies:

```bash
cd frontend
npm install
```

2. Start Vite dev server:

```bash
npm run dev
```

3. Open:
- Frontend dev URL: http://localhost:5173

## Environment Variables

Main required keys are in .env.example. Important groups:

- Database
  - DATABASE_URL

- Dhan credentials
  - DHAN_CLIENT_ID
  - DHAN_ACCESS_TOKEN
  - DHAN_API_KEY
  - DHAN_API_SECRET
  - DHAN_PIN
  - DHAN_TOTP_SECRET

- OTP providers
  - MESSAGE_CENTRAL_CUSTOMER_ID
  - MESSAGE_CENTRAL_PASSWORD
  - EMAIL_OTP_SERVICE_BASE_URL

- Startup flags
  - DISABLE_DHAN_WS
  - STARTUP_START_STREAMS
  - STARTUP_LOAD_MASTER
  - STARTUP_LOAD_TIER_B

## API Base and Auth

- Base path: /api/v2
- Interactive docs: /api/v2/docs
- Authentication is token-based via Authorization header.

## Frontend Scripts

From frontend/:

```bash
npm run dev
npm run build
npm run preview
```

## Common Problems

1. Backend cannot connect to DB
- Verify DATABASE_URL and DB container/service health.

2. Frontend cannot reach backend
- In Docker mode, confirm backend service is healthy.
- In native mode, confirm backend is running on port 8000.

3. Market stream not updating
- Check DISABLE_DHAN_WS in env.
- Check credentials and startup flags.

## Security Notes

- Keep .env out of source control.
- Rotate API secrets regularly.
- Use production secrets manager for hosted deployments.
- Do not hardcode tokens in source files.

## Handover Note

If you are handing this repo to a client/team:
- Share only .env.example
- Provide actual credentials through a secure secrets channel
- Validate /health and /api/v2/docs after first boot
