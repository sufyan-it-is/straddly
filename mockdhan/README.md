# Mock DhanHQ API

Local testing engine that emulates DhanHQ REST and WebSocket APIs without external dependencies.

## Usage

### Docker

```bash
docker build -t mock-dhan .
docker run -p 9000:9000 mock-dhan
```

### Native

```bash
pip install -r requirements.txt
python app.py
```

## Endpoints

- `/health` - Health check
- `/v2/instruments` - Get instruments
- `/v2/price` - Get prices

## Environment

Used by `docker-compose.yml` for local development.

Set `DISABLE_DHAN_WS=true` in .env to use mock API for testing.
