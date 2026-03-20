"""
app/runtime/geoip.py
=====================
Offline GeoIP resolver using MaxMind GeoLite2-City database.

Falls back gracefully when:
 - Database file is absent
 - IP is private/loopback/unknown
 - geoip2 library is not installed

Returns a GeoResult dataclass with country/region/city/lat/lon or all None.
"""
from __future__ import annotations

import ipaddress
import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Loaded once at first use; stays None if DB file is absent / library missing.
_reader = None
_load_attempted = False


@dataclass
class GeoResult:
    country: Optional[str] = None
    country_code: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "country": self.country,
            "country_code": self.country_code,
            "region": self.region,
            "city": self.city,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }


def _get_reader():
    global _reader, _load_attempted
    if _load_attempted:
        return _reader
    _load_attempted = True
    try:
        import geoip2.database  # type: ignore
        from app.config import get_settings
        cfg = get_settings()
        path = cfg.geoip_db_path
        _reader = geoip2.database.Reader(path)
        log.info("GeoIP database loaded from %s", path)
    except ImportError:
        log.warning("geoip2 library not installed — GeoIP lookup disabled")
    except FileNotFoundError:
        log.warning("GeoIP database not found at configured path — GeoIP lookup disabled")
    except Exception as exc:
        log.warning("GeoIP database load failed: %s", exc)
    return _reader


def _is_private(ip: str) -> bool:
    """Return True for private/loopback/link-local addresses that have no geo data."""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_unspecified
    except ValueError:
        return True


def lookup(ip: str | None) -> GeoResult:
    """
    Resolve an IP address to city-level geographic data.
    Always returns a GeoResult; fields are None when lookup is not possible.
    """
    if not ip:
        return GeoResult()
    if _is_private(ip):
        return GeoResult()
    reader = _get_reader()
    if reader is None:
        return GeoResult()
    try:
        response = reader.city(ip)
        return GeoResult(
            country=response.country.name,
            country_code=response.country.iso_code,
            region=response.subdivisions.most_specific.name if response.subdivisions else None,
            city=response.city.name,
            latitude=float(response.location.latitude) if response.location.latitude is not None else None,
            longitude=float(response.location.longitude) if response.location.longitude is not None else None,
        )
    except Exception:
        return GeoResult()
