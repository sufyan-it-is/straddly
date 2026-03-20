#!/usr/bin/env python3
"""
Simple health check for Straddly backend.
Exits with 0 if healthy, 1 if not.
"""
import sys
import urllib.request
import urllib.error

try:
    response = urllib.request.urlopen("http://localhost:8000/health", timeout=5)
    if response.status == 200:
        sys.exit(0)
    else:
        sys.exit(1)
except Exception as e:
    print(f"Health check failed: {e}", file=sys.stderr)
    sys.exit(1)
