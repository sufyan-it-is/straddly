# Straddly - Clean Project Template

A modern, full-stack trading platform with real-time market data, order management, and portfolio tracking.

**This is a blank, clean project ready for client handover or fresh development deployment.**

## 📋 What's Included

```
straddly-clean/
├── app/                    # FastAPI backend
│   ├── routers/           # API endpoints (/api/v2/*)
│   ├── database.py        # PostgreSQL async pool
│   ├── config.py          # Environment-driven settings
│   ├── dependencies.py    # Auth & role guards
│   └── main.py            # App factory & startup
├── frontend/              # React + Vite SPA
│   ├── src/
│   │   ├── components/    # Reusable UI components
│   │   ├── pages/         # Page-level components
│   │   ├── services/      # API client (apiService.jsx handles auth headers)
│   │   └── contexts/      # Auth, theme, market state
│   └── vite.config.js     # Vite + proxy config (/api → backend)
├── migrations/            # Raw SQL files
│   └── *.sql              # Auto-applied on backend startup (sorted by filename)
├── mockdhan/              # Local DhanHQ REST+WS emulator (for safe local dev)
├── docker-compose.yml     # Local dev stack (includes mockdhan)
└── docker-compose.prod.yml # Production stack (no mockdhan)
```

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose (recommended)
- OR: Node.js 18+, Python 3.11+, PostgreSQL 14+

### Local Development (with Docker)

```bash
# 1. Clone and navigate
git clone <repo-url>
cd straddly-clean

# 2. Copy environment template and configure
cp .env.example .env.local

# 3. Start full stack (backend + frontend + mockdhan + db)
docker-compose up

# 4. Access
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000/api/v2
- API Docs: http://localhost:8000/api/v2/docs
- Mock DhanHQ (local): wss://localhost:8001 (emulator mode)
```

### Native Setup (without Docker)

#### Backend Setup

```bash
# 1. Set up PostgreSQL
createdb trading_terminal

# 2. Create Python environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env.local
# Edit .env.local with your database and Dhan credentials

# 5. Run migrations and start backend
cd app
uvicorn main:create_app --reload --port 8000
```

#### Frontend Setup

```bash
# 1. Install dependencies
cd frontend
npm install

# 2. Start dev server (proxy to backend)
npm run dev

# 3. Open http://localhost:5173
```



## 🔐 Environment Configuration

### Get Started - Configure Your Environment

Before running the project locally or in production, you **must** configure the `.env` file with your credentials.

### Step 1: Create Your .env File

```bash
# Copy the template
cp .env.example .env

# Edit .env with YOUR actual credentials (use your favorite editor)
# This file is automatically excluded from git by .gitignore
```

### Step 2: Fill in Required Credentials

The `.env` file contains placeholders marked with `<ENTER_YOUR_...>`. You must replace these with actual values:

#### Database Configuration
```env
DATABASE_URL=<ENTER_YOUR_DATABASE_URL>
# Replace with: postgresql://username:password@host:port/database_name
# Example: postgresql://postgres:YourSecurePassword@db.example.com:5432/trading_app
```

#### DhanHQ API Credentials
```env
DHAN_CLIENT_ID=<ENTER_YOUR_DHAN_CLIENT_ID>
# Get from: https://dhanhq.co/
```

#### SMS Gateway (for OTP login)
```env
MESSAGE_CENTRAL_CUSTOMER_ID=<ENTER_YOUR_SMS_CUSTOMER_ID>
MESSAGE_CENTRAL_PASSWORD=<ENTER_YOUR_SMS_PASSWORD>
# Get from: Your SMS gateway provider
```

#### Email OTP Service
```env
EMAIL_OTP_SERVICE_BASE_URL=<ENTER_YOUR_OTP_SERVICE_URL>
# Replace with: Your email/OTP service endpoint
```

### Step 3: Verify Your Configuration

```bash
# Ensure all <ENTER_YOUR_...> placeholders are replaced
grep "<ENTER_YOUR" .env
# Should return: (nothing)

# If you see any <ENTER_YOUR...>, those are required
```

### Local Development Setup

For local development with Docker (no real credentials needed):

```bash
# Use .env.local for testing with mock data
# Replace placeholders with test values, e.g.:
DATABASE_URL=postgresql://postgres:testpass@db:5432/trading_app
DHAN_CLIENT_ID=test_client
DISABLE_DHAN_WS=true
STARTUP_START_STREAMS=false
```

### Production Setup

For production deployment:

1. **Never hardcode credentials** in .env
2. Use your deployment platform's secrets manager:
   - AWS Secrets Manager / Parameter Store
   - Kubernetes Secrets
   - Coolify Secrets
   - Docker Compose secrets
   - Environment variables injected by orchestrator

3. Example production deployment (Coolify/Docker):
```bash
# Instead of .env file, inject via environment:
docker run \
  -e DATABASE_URL="postgres://..." \
  -e DHAN_CLIENT_ID="..." \
  -e DHAN_API_KEY="..." \
  ...
```

### Placeholder Reference

| Placeholder | Purpose | Where to Get |
|-------------|---------|-----------|
| `<ENTER_YOUR_DATABASE_URL>` | PostgreSQL connection | Set up PostgreSQL 14+, get URL |
| `<ENTER_YOUR_DHAN_CLIENT_ID>` | DhanHQ API | Register at https://dhanhq.co/ |
| `<ENTER_YOUR_DHAN_API_KEY>` | DhanHQ API Key | Dashboard → API Settings |
| `<ENTER_YOUR_DHAN_API_SECRET>` | DhanHQ API Secret | Dashboard → API Settings |
| `<ENTER_YOUR_SMS_CUSTOMER_ID>` | SMS Gateway | Your SMS provider account |
| `<ENTER_YOUR_SMS_PASSWORD>` | SMS Gateway | Your SMS provider account |
| `<ENTER_YOUR_OTP_SERVICE_URL>` | Email OTP | Your OTP service endpoint |

### Security Reminders

✅ **DO:**
- Use strong, unique credentials
- Store credentials in `.env` locally (excluded from git)
- Use secrets manager in production
- Rotate credentials regularly
- Use HTTPS for all API communications

❌ **DON'T:**
- Commit `.env` to git
- Hardcode credentials in source code
- Use same credentials for dev, staging, production
- Share credentials via email or chat
- Use weak passwords

### Troubleshooting

**Error: "Invalid DATABASE_URL"**
```
Fix: Ensure DATABASE_URL is properly formatted:
postgresql://username:password@host:port/database
```

**Error: "DHAN_CLIENT_ID required"**
```
Fix: Register at https://dhanhq.co/ and get your client ID
```

**Error: "SMS credentials invalid"**
```
Fix: Verify SMS gateway credentials are correct and account is active
```

## 📡 API Architecture

### Base URL
- Local: `http://localhost:8000/api/v2`
- Production: Configure via environment

### Authentication
- Session-based (Bearer token in `Authorization` header)
- 30-day session expiry
- Fallback: `X-AUTH` header

### Main Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/login` | POST | User login (OTP required) |
| `/auth/me` | GET | Get current user info |
| `/auth/logout` | POST | Logout |
| `/market/instruments` | GET | Search instruments |
| `/market/prices` | GET | Get current prices |
| `/portfolio/positions` | GET | Open positions |
| `/portfolio/orders` | GET/POST | View/create orders |
| `/ws/feed` | WS | Real-time tick stream |
| `/ws/prices` | WS | Real-time prices |

See `http://localhost:8000/api/v2/docs` for full OpenAPI spec.

## 📊 Database Schema

Migrations are auto-applied on backend startup (in filename order).

Key tables:
- `users` - User accounts
- `user_sessions` - Active sessions
- `instruments` - Stock/option/futures master data
- `positions` - User open positions
- `orders` - Buy/sell orders
- `tick_stream` - Real-time market data
- `option_chain` - Option pricing data

## 🔄 Market Data Flow

```
DhanHQ WebSocket
    ↓
tick_processor (app/workers/tick_processor.py)
    ↓
PostgreSQL (tick_stream table)
    ↓
/api/v2/ws/feed (broadcast to clients)
/api/v2/market/prices (REST endpoint)
```

## 🌐 Frontend Features

- **Authentication** - OTP login, session management
- **Market Search** - Find instruments by name/symbol
- **Real-time Charts** - TradingView Lightweight Charts integration
- **Watchlist** - Save and manage symbols
- **Portfolio** - View positions, P&L, margins
- **Order Management** - Place/modify/cancel orders
- **Responsive UI** - Works on desktop and mobile

## 🛠️ For Mobile App (Capacitor)

If deploying the `mobile-app/` separately:

```bash
cd mobile-app

# Sync with Android Studio
npm run android:sync

# Or build APK directly
npm run android:build
```

Update `mobile-app/src/config/runtime.ts` with your backend URL:

```typescript
export const API_BASE_URL = 'https://your-api.example.com/api/v2';
export const WS_BASE_URL = 'wss://your-api.example.com/api/v2';
```

## 📝 Database Maintenance

### Fresh Database Setup

```bash
# Drop and recreate
dropdb trading_terminal
createdb trading_terminal

# Migrations auto-run on next backend startup
```

### Backup

```bash
# Backup
pg_dump trading_terminal > backup.sql

# Restore
psql trading_terminal < backup.sql
```

## 🐛 Debugging

### Backend Logs

```bash
# View with timestamps
python -c "from app.main import *; import logging; \
logging.basicConfig(level=logging.DEBUG); app = create_app()" 
```

### Frontend Logs

```bash
# Browser console (F12)
# Check Network tab for API calls
```

### Database Check

```bash
psql trading_terminal
SELECT * FROM users;
SELECT * FROM instruments LIMIT 10;
```

## 🚢 Production Deployment

### Using Docker Compose

```bash
docker-compose -f docker-compose.prod.yml up -d
```

### Using Kubernetes / Coolify

1. Build Docker images (Dockerfile in repo root)
2. Push to registry
3. Deploy using your orchestrator
4. Ensure environment variables are set via deployment config

### Reverse Proxy Setup

- Use Nginx or Traefik in front
- Set backend upstream to `http://backend:8000/api/v2`
- Set CORS_ORIGINS_RAW to allow your domain

## 📚 Project Structure Details

### app/routers/

Each file is a FastAPI router mounted under `/api/v2`:

- `auth.py` - Login, logout, session management
- `market_data.py` - Instruments, prices, charts
- `portfolio.py` - Positions, orders, P&L
- `admin.py` - Admin endpoints (role-gated)

### frontend/src/services/

- `apiService.jsx` - Central API client with auth headers
- All requests include: `Authorization: Bearer <token>`, `X-AUTH: <token>`, `X-USER: <user_id>`

### frontend/src/contexts/

- `AuthContext.jsx` - Auth state & token lifecycle
- `MarketContext.jsx` - Real-time market data
- `ThemeContext.jsx` - Dark/light mode persistence

## 🔑 Key Technologies

**Backend**
- FastAPI 0.104+
- PostgreSQL 14+ with asyncpg
- Pydantic 2.x
- WebSockets (FastAPI native)

**Frontend**
- React 18+
- Vite 5+
- React Router 6+
- TaiwanCSS / tailwindcss
- TradingView Lightweight Charts

**Infrastructure**
- Docker & Docker Compose
- Nginx reverse proxy
- PostgreSQL database

## ⚠️ Security Notes

- All credentials **must** be set via environment variables, never hardcoded
- Use `.env.local` (excluded from git) for local secrets
- Database URL should use strong credentials in production
- Enable HTTPS in production (via reverse proxy)
- Sessions expire after 30 days

## 📖 Additional Documentation

- API Docs: http://localhost:8000/api/v2/docs (Swagger UI)
- Schema: http://localhost:8000/api/v2/openapi.json (OpenAPI 3.0)

## 💬 Support

For issues or questions:
1. Check API docs at `/api/v2/docs`
2. Review logs: `docker-compose logs -f backend`
3. Test with curl:
   ```bash
   curl -X GET http://localhost:8000/api/v2/market/instruments \
     -H "Authorization: Bearer <token>"
   ```

---

**Version:** 1.0.0 (Clean Template)  
**Last Updated:** 2025  
**License:** As per your organization

