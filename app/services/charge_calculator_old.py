"""
Charge Calculator Service
Calculates brokerage and statutory charges for closed positions.

Implements:
- Brokerage (flat or percentage-based)
- STT/CTT (Securities/Commodity Transaction Tax)
- Exchange transaction charges (NSE, BSE, MCX)
- SEBI charges
- GST (18% on brokerage + SEBI + exchange)
- Stamp duty (0.015% on buy side)
- IPFT (Inter-transfer charges)
"""
from decimal import Decimal
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ChargeRates:
    """
    Statutory charge rates as per Indian regulations.
    All rates are defined as decimals for precision.
    """
    
    # Exchange Transaction Charges (per lakh of turnover)
    EXCHANGE_RATES = {
        'NSE_EQ': Decimal('0.00297'),    # ₹2.97 per lakh = 0.00297%
        'BSE_EQ': Decimal('0.00375'),    # ₹3.75 per lakh = 0.00375%
        'NSE_FNO_FUTURES': Decimal('0.00173'),  # ₹1.73 per lakh = 0.00173%
        'NSE_FNO_OPTIONS': Decimal('0.03503'),  # ₹35.03 per lakh of premium = 0.03503%
        'BSE_FNO_OPTIONS': Decimal('0.0325'),   # ₹32.5 per lakh of premium = 0.0325%
        'MCX_COMM': Decimal('0.00035'),   # MCX commodity (approximate)
    }
    
    # STT/CTT Rates
    STT_RATES = {
        'EQUITY_DELIVERY_BUY': Decimal('0.001'),   # 0.1% on buy
        'EQUITY_DELIVERY_SELL': Decimal('0.001'),  # 0.1% on sell
        'EQUITY_INTRADAY_SELL': Decimal('0.00025'), # 0.025% on sell only
        'EQUITY_FUTURES_SELL': Decimal('0.0002'),   # 0.02% on sell
        'EQUITY_OPTIONS_SELL': Decimal('0.001'),    # 0.1% on sell (on premium)
        'COMMODITY_FUTURES_SELL': Decimal('0.0001'), # 0.01% on sell
        'COMMODITY_OPTIONS_SELL': Decimal('0.0005'), # 0.05% on sell (on premium)
    }
    
    # Other Charges
    SEBI_RATE = Decimal('0.000001')      # ₹10 per crore  = 0.0001%
    GST_RATE = Decimal('0.18')           # 18%
    STAMP_DUTY_RATE = Decimal('0.00015') # 0.015% on buy side
    
    # IPFT (Inter-transfer charges per crore)
    IPFT_EQUITY = Decimal('0.00001')     # ₹10 per crore = 0.001%
    IPFT_FUTURES = Decimal('0.00001')    # ₹10 per crore = 0.001%
    IPFT_OPTIONS = Decimal('0.00005')    # ₹50 per crore = 0.005%


class ChargeCalculator:
    """
    Calculates all trading charges for a closed position.
    """
    
    def __init__(self):
        self.rates = ChargeRates()
    
    def calculate_all_charges(
        self,
        turnover: float,
        exchange_segment: str,
        product_type: str,
        instrument_type: str,
        brokerage_flat: float,
        brokerage_percent: float,
        is_option: bool = False,
        premium_turnover: Optional[float] = None
    ) -> Dict[str, float]:
        """
        Calculate all charges for a position.
        
        Args:
            turnover: Total turnover (buy + sell value)
            exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, MCX_COMM
            product_type: MIS (intraday) or NORMAL (delivery)
            instrument_type: EQUITY, FUTIDX, OPTIDX, FUTSTK, OPTSTK, FUTCOM, OPTFUT
            brokerage_flat: Flat fee per order (e.g., ₹20)
            brokerage_percent: Percentage of turnover (e.g., 0.002 for 0.2%)
            is_option: Whether this is an option contract
            premium_turnover: For options, the premium value (not notional)
        
        Returns:
            Dict with all charge components
        """
        try:
            # Convert to Decimal for precision
            turnover_dec = Decimal(str(turnover))
            premium_dec = Decimal(str(premium_turnover or turnover))
            
            # 1. Brokerage
            brokerage = self._calculate_brokerage(
                turnover_dec,
                Decimal(str(brokerage_flat)),
                Decimal(str(brokerage_percent))
            )
            
            # 2. STT/CTT
            stt_ctt = self._calculate_stt_ctt(
                turnover_dec,
                premium_dec,
                exchange_segment,
                product_type,
                instrument_type,
                is_option
            )
            
            # 3. Exchange transaction charges
            exchange_charge = self._calculate_exchange_charge(
                turnover_dec,
                premium_dec,
                exchange_segment,
                instrument_type,
                is_option
            )
            
            # 4. SEBI charges
            sebi_charge = self._calculate_sebi_charge(turnover_dec)
            
            # 5. Stamp duty (on buy side only, applied to total turnover)
            stamp_duty = self._calculate_stamp_duty(turnover_dec)
            
            # 6. IPFT
            ipft_charge = self._calculate_ipft(turnover_dec, instrument_type, is_option)
            
            # 7. GST (18% on brokerage + SEBI + exchange charges)
            taxable_amount = brokerage + sebi_charge + exchange_charge
            gst_charge = taxable_amount * self.rates.GST_RATE
            
            # 8. Totals
            # platform_cost = brokerage charge only (what broker keeps)
            # trade_expense = all other charges (regulatory/statutory)
            platform_cost = brokerage
            trade_expense = stt_ctt + exchange_charge + sebi_charge + stamp_duty + ipft_charge + gst_charge
            total_charges = platform_cost + trade_expense
            
            return {
                'brokerage_charge': float(brokerage),
                'stt_ctt_charge': float(stt_ctt),
                'exchange_charge': float(exchange_charge),
                'sebi_charge': float(sebi_charge),
                'stamp_duty': float(stamp_duty),
                'ipft_charge': float(ipft_charge),
                'gst_charge': float(gst_charge),
                'platform_cost': float(platform_cost),
                'trade_expense': float(trade_expense),
                'total_charges': float(total_charges),
            }
            
        except Exception as e:
            logger.error(f"Error calculating charges: {e}")
            # Return zeros on error
            return {
                'brokerage_charge': 0.0,
                'stt_ctt_charge': 0.0,
                'exchange_charge': 0.0,
                'sebi_charge': 0.0,
                'stamp_duty': 0.0,
                'ipft_charge': 0.0,
                'gst_charge': 0.0,
                'platform_cost': 0.0,
                'trade_expense': 0.0,
                'total_charges': 0.0,
            }
    
    def _calculate_brokerage(
        self,
        turnover: Decimal,
        flat_fee: Decimal,
        percent_fee: Decimal
    ) -> Decimal:
        """Calculate brokerage: flat fee + (turnover × percent)"""
        return flat_fee + (turnover * percent_fee)
    
    def _calculate_stt_ctt(
        self,
        turnover: Decimal,
        premium: Decimal,
        exchange_segment: str,
        product_type: str,
        instrument_type: str,
        is_option: bool
    ) -> Decimal:
        """
        Calculate STT/CTT based on instrument and product type.
        NOTE: Simplified - assumes both buy and sell happened.
        """
        half_turnover = turnover / Decimal('2')  # Sell side value
        
        # Equity
        if 'EQ' in exchange_segment or instrument_type == 'EQUITY':
            if product_type == 'MIS':  # Intraday
                return half_turnover * self.rates.STT_RATES['EQUITY_INTRADAY_SELL']
            else:  # Delivery (both buy and sell)
                return turnover * self.rates.STT_RATES['EQUITY_DELIVERY_SELL']
        
        # Futures
        if 'FUT' in instrument_type:
            if 'COMM' in exchange_segment or 'MCX' in exchange_segment:
                return half_turnover * self.rates.STT_RATES['COMMODITY_FUTURES_SELL']
            else:
                return half_turnover * self.rates.STT_RATES['EQUITY_FUTURES_SELL']
        
        # Options
        if is_option or 'OPT' in instrument_type:
            # STT on premium value, sell side only
            premium_sell = premium / Decimal('2')
            if 'COMM' in exchange_segment or 'MCX' in exchange_segment:
                return premium_sell * self.rates.STT_RATES['COMMODITY_OPTIONS_SELL']
            else:
                return premium_sell * self.rates.STT_RATES['EQUITY_OPTIONS_SELL']
        
        return Decimal('0')
    
    def _calculate_exchange_charge(
        self,
        turnover: Decimal,
        premium: Decimal,
        exchange_segment: str,
        instrument_type: str,
        is_option: bool
    ) -> Decimal:
        """Calculate exchange transaction charges."""
        
        # Determine exchange rate key
        if 'NSE_EQ' in exchange_segment:
            rate = self.rates.EXCHANGE_RATES['NSE_EQ']
            base = turnover
        elif 'BSE_EQ' in exchange_segment or 'BSE' in exchange_segment:
            rate = self.rates.EXCHANGE_RATES['BSE_EQ']
            base = turnover
        elif 'MCX' in exchange_segment:
            rate = self.rates.EXCHANGE_RATES['MCX_COMM']
            base = turnover
        elif is_option or 'OPT' in instrument_type:
            # Options: charged on premium
            if 'BSE' in exchange_segment:
                rate = self.rates.EXCHANGE_RATES['BSE_FNO_OPTIONS']
            else:
                rate = self.rates.EXCHANGE_RATES['NSE_FNO_OPTIONS']
            base = premium
        else:
            # Futures
            rate = self.rates.EXCHANGE_RATES['NSE_FNO_FUTURES']
            base = turnover
        
        return base * rate
    
    def _calculate_sebi_charge(self, turnover: Decimal) -> Decimal:
        """Calculate SEBI charges (₹10 per crore)."""
        return turnover * self.rates.SEBI_RATE
    
    def _calculate_stamp_duty(self, turnover: Decimal) -> Decimal:
        """Calculate stamp duty (0.015% on buy side only)."""
        buy_value = turnover / Decimal('2')
        return buy_value * self.rates.STAMP_DUTY_RATE
    
    def _calculate_ipft(
        self,
        turnover: Decimal,
        instrument_type: str,
        is_option: bool
    ) -> Decimal:
        """Calculate IPFT (Inter-transfer charges)."""
        if is_option or 'OPT' in instrument_type:
            return turnover * self.rates.IPFT_OPTIONS
        elif 'FUT' in instrument_type:
            return turnover * self.rates.IPFT_FUTURES
        else:
            return turnover * self.rates.IPFT_EQUITY


# Global singleton instance
charge_calculator = ChargeCalculator()


def calculate_position_charges(
    quantity: int,
    avg_price: float,
    exit_price: float,
    exchange_segment: str,
    product_type: str,
    instrument_type: str,
    brokerage_flat: float = 20.0,
    brokerage_percent: float = 0.0,
    is_option: bool = False
) -> Dict[str, float]:
    """
    Convenience function to calculate charges for a closed position.
    
    Args:
        quantity: Position quantity
        avg_price: Average entry price
        exit_price: Exit price (LTP at close)
        exchange_segment: Exchange segment code
        product_type: MIS or NORMAL
        instrument_type: Instrument type
        brokerage_flat: Flat brokerage fee
        brokerage_percent: Percentage brokerage
        is_option: Whether this is an option
    
    Returns:
        Dictionary with all charge components
    """
    abs_qty = abs(quantity)
    
    # Calculate turnover (buy + sell value)
    buy_value = abs_qty * avg_price
    sell_value = abs_qty * exit_price
    turnover = buy_value + sell_value
    
    # For options, premium is the actual value traded
    premium_turnover = turnover if is_option else None
    
    return charge_calculator.calculate_all_charges(
        turnover=turnover,
        exchange_segment=exchange_segment,
        product_type=product_type,
        instrument_type=instrument_type,
        brokerage_flat=brokerage_flat,
        brokerage_percent=brokerage_percent,
        is_option=is_option,
        premium_turnover=premium_turnover
    )
