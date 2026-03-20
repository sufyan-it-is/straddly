"""
app/market_data/close_price_validator.py
=========================================
Validation logic for close prices before storing to database.

Prevents:
- Zero or negative close prices
- Extreme deviations from previous close (>50%)
- Suspicious deviations from LTP during market hours (>20%)
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)


class ClosePriceValidator:
    """Validates close prices before storage."""
    
    # Thresholds
    MAX_DEVIATION_FROM_PREV_CLOSE = 0.50  # 50%
    MAX_DEVIATION_FROM_LTP = 0.20         # 20% during market hours
    
    @staticmethod
    def validate(
        close_price: Optional[float],
        instrument_token: int,
        prev_close: Optional[float] = None,
        ltp: Optional[float] = None,
        is_market_open: bool = False,
        symbol: str = "UNKNOWN"
    ) -> tuple[bool, Optional[str]]:
        """
        Validate a close price before storage.
        
        Args:
            close_price: The close price to validate
            instrument_token: Instrument token (for logging)
            prev_close: Previous close price (for deviation check)
            ltp: Last traded price (for market hours check)
            is_market_open: Whether market is currently open
            symbol: Symbol name (for logging)
            
        Returns:
            (is_valid, rejection_reason)
        """
        # Allow None (will be handled by COALESCE in DB)
        if close_price is None:
            return (True, None)
        
        # Rule 1: Reject zero or negative
        if close_price <= 0:
            reason = f"Close price <= 0: {close_price}"
            log.warning(
                f"[CLOSE_VALIDATION] Rejected {symbol} ({instrument_token}): {reason}"
            )
            return (False, reason)
        
        # Rule 2: Deviation from previous close (>50%)
        if prev_close is not None and prev_close > 0:
            deviation = abs(close_price - prev_close) / prev_close
            if deviation > ClosePriceValidator.MAX_DEVIATION_FROM_PREV_CLOSE:
                reason = (
                    f"Deviation from prev_close too high: "
                    f"{deviation*100:.1f}% (close={close_price}, prev={prev_close})"
                )
                log.warning(
                    f"[CLOSE_VALIDATION] Rejected {symbol} ({instrument_token}): {reason}"
                )
                return (False, reason)
        
        # Rule 3: Deviation from LTP during market hours (>20%)
        if is_market_open and ltp is not None and ltp > 0:
            deviation = abs(close_price - ltp) / ltp
            if deviation > ClosePriceValidator.MAX_DEVIATION_FROM_LTP:
                reason = (
                    f"Deviation from LTP during market hours: "
                    f"{deviation*100:.1f}% (close={close_price}, ltp={ltp})"
                )
                log.warning(
                    f"[CLOSE_VALIDATION] Rejected {symbol} ({instrument_token}): {reason}"
                )
                return (False, reason)
        
        # Passed all checks
        return (True, None)
    
    @staticmethod
    def log_accepted(symbol: str, instrument_token: int, close_price: float) -> None:
        """Log accepted close price (for audit trail)."""
        log.debug(
            f"[CLOSE_VALIDATION] Accepted {symbol} ({instrument_token}): "
            f"close={close_price:.2f}"
        )


# Convenience singleton-style functions
def validate_close_price(
    close_price: Optional[float],
    instrument_token: int,
    prev_close: Optional[float] = None,
    ltp: Optional[float] = None,
    is_market_open: bool = False,
    symbol: str = "UNKNOWN"
) -> tuple[bool, Optional[str]]:
    """Validate close price. Returns (is_valid, rejection_reason)."""
    return ClosePriceValidator.validate(
        close_price, instrument_token, prev_close, ltp, is_market_open, symbol
    )
