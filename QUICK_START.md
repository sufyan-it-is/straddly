# 🚀 Quick Start Guide

Get the trading platform running in 5 minutes.

## For Local Development (with Docker)

```bash
# 1. Clone the repository

# 2. Copy environment template
cp .env.example .env.local

# 3. Start everything
docker-compose up

# 4. Open browser
#    Frontend: http://localhost:5173
#    API Docs: http://localhost:8000/api/v2/docs
```

**That's it!** The mock market data engine starts automatically.

## For Native Development (no Docker)

### Backend (FastAPI)

```bash
# 1. Create Python environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install packages
pip install -r requirements.txt

# 3. Start PostgreSQL (separate terminal)
postgres -D /path/to/data

# 4. Configure
cp .env.example .env.local
# Edit .env.local - set DATABASE_URL, DHAN credentials

# 5. Run backend
cd app
uvicorn main:create_app --reload
```

### Frontend (React + Vite)

```bash
# 1. Install Node dependencies
cd frontend
npm install

# 2. Start dev server (auto-proxies /api to backend)
npm run dev

# 3. Open http://localhost:5173
```

## First-Time Setup Checklist

- [ ] Copy `.env.example` to `.env.local`
- [ ] Database is running (PostgreSQL 14+)
- [ ] DhanHQ credentials configured (or use mock)
- [ ] SMS gateway credentials (for OTP)
- [ ] Backend on `localhost:8000`
- [ ] Frontend on `localhost:5173`
- [ ] Can login with test user (check `/docs`)

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Can't connect to database` | Check `DATABASE_URL` in `.env.local` |
| `Backend won't start` | Check logs: `docker-compose logs backend` |
| `Frontend won't connect to API` | Ensure backend is running; check proxy in `frontend/vite.config.js` |
| `Mock market data not working` | `DISABLE_DHAN_WS=false` in `.env.local` |

## Next Steps

1. **Read the full README** for architecture & features
2. **Check API docs** at `/api/v2/docs`
3. **Login** with OTP (SMS or email)
4. **Test create order** endpoint
5. **Review database schema** in `migrations/` folder

---

**Need help?** Check README.md for detailed configuration.
