# 🚀 Deployment Guide

Production deployment instructions.

## Prerequisites

- Docker & Docker Compose
- PostgreSQL 14+ (managed externally or via RDS)
- Valid DhanHQ credentials
- SMS gateway account
- Domain name + SSL certificate

## Option 1: Docker Compose (Simple)

```bash
# Build production images
docker-compose -f docker-compose.prod.yml build

# Start services
docker-compose -f docker-compose.prod.yml up -d

# Verify health
docker-compose -f docker-compose.prod.yml ps
```

## Option 2: Kubernetes / Managed Platform

### Coolify (Recommended)

1. Push code to Git
2. Create new project in Coolify
3. Set environment variables  
4. Deploy

## Environment Variables (Production)

```env
# Database (use managed RDS or similar)
DATABASE_URL=postgresql://user:strong_password@db.example.com:5432/trading_terminal

# DhanHQ (production credentials)
DHAN_CLIENT_ID=your_production_id
DHAN_API_KEY=your_production_key
DHAN_API_SECRET=your_production_secret

# SMS Gateway
MESSAGE_CENTRAL_CUSTOMER_ID=prod_id
MESSAGE_CENTRAL_PASSWORD=prod_password

# Email OTP Service (configure your service)
EMAIL_OTP_SERVICE_BASE_URL=https://your-otp-api.example.com

# Enable streams
DISABLE_DHAN_WS=false
STARTUP_START_STREAMS=true

# Security
DEBUG=false
LOG_LEVEL=INFO
```

## Reverse Proxy Setup (Nginx)

```nginx
upstream backend {
    server backend:8000;
}

server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://backend;
        proxy_set_header Authorization $http_authorization;
        proxy_pass_header Authorization;
    }
}
```

## SSL/HTTPS

Use Let's Encrypt or your certificate provider:

```bash
certbot certonly -d api.example.com
# Copy cert to Nginx volume
```

## Database Backup

```bash
# Backup
pg_dump trading_terminal > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore
psql trading_terminal < backup.sql
```

## Monitoring

- **Health Check**: `/api/v2/health`
- **Logs**: `docker-compose logs -f backend`
- **Metrics**: Export via Prometheu (configure in config.py)

## Scaling

For high load:

1. Run multiple backend instances (use load balancer)
2. Separate database server
3. Redis cache layer (for prices)
4. CDN for frontend assets

---

**Security Note:** Never commit `.env` files with real credentials. Use platform secrets.
