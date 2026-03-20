"""
Mock DhanHQ API Server
Local testing without external dependencies
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import json
from datetime import datetime

app = FastAPI(title="Mock DhanHQ API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok", "service": "mock-dhan"}

@app.get("/v2/instruments")
async def get_instruments():
    """Mock instruments endpoint"""
    return {
        "data": [
            {
                "instrument_token": 1,
                "symbol": "NIFTY",
                "exchange_segment": "NSE",
                "instrument_type": "FUTIDX",
                "expiry_date": "2025-12-25"
            }
        ]
    }

@app.get("/v2/price")
async def get_price():
    """Mock price endpoint"""
    return {
        "data": {
            "last_price": 21500.00,
            "bid_price": 21495.00,
            "ask_price": 21505.00
        }
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
