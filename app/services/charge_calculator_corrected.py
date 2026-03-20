"""
Charge Calculator Service - CORRECTED
Calculates brokerage and statutory charges for closed positions.

Indian Statutory Structure Implementation:
- STT/CTT (Securities/Commodity Transaction Tax)
- Exchange Transaction Charges
- Stamp Duty (segment-specific rates)
- SEBI Charges
- DP Charges (delivery equity only)
- Clearing Charges
- GST (18% on applicable charges)

IMPORTANT: This calculator follows actual Indian regulatory structure
as per SEBI/NSE/BSE/MCX guidelines.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ChargeRates:
    """
    Statutory charge rates as per Indian regulations.
    Updated to match actual regulatory requirements.
    """
    
    # ========== EXCHANGE TRANSACTION CHARGES ==========
    # These are typically per lakh of turnover
    # For NSE EQ: ~₹3.25 per lakh
    # For Options: on premium value
    EXCHANGE_RATES = {
        'NSE_EQ_INTRADAY': Decimal('0.00325'),      # ~0.00325% on total turnover
        'NSE_EQ_DELIVERY': Decimal('0.00325'),       # Same as intraday
        'BSE_EQ': Decimal('0.00375'),                # ~0.00375% on total turnover
        'NSE_FNO_FUTURES': Decimal('0.002'),        # ~₹2 per lakh = 0.002%
        'NSE_FNO_OPTIONS': Decimal('0.035'),        # On premium: ~0.035%
        'BSE_FNO_OPTIONS': Decimal('0.03'),         # On premium: ~0.03%
        'MCX_COMMODITY_FUTURES': Decimal('0.0002'), # Very low for commodities
        'MCX_COMMODITY_OPTIONS': Decimal('0.001'),  # Slightly higher for options
    }
    
    # ========== STT/CTT RATES ==========
    # Segment and transaction specific
    STT_RATES = {
        # EQUITY - Intraday (MIS)
        'EQ_INTRADAY_BUY': Decimal('0'),            # No STT on buy side intraday
        'EQ_INTRADAY_SELL': Decimal('0.00025'),     # 0.025% on sell side
        
        # EQUITY - Delivery
        'EQ_DELIVERY_BUY': Decimal('0.001'),        # 0.1% on buy side
        'EQ_DELIVERY_SELL': Decimal('0.001'),       # 0.1% on sell side
        
        # FUTURES - Index/Stock (on sell side turnover)
        'FUT_INTRADAY_SELL': Decimal('0.000125'),   # 0.0125% on sell side
        'FUT_DELIVERY_SELL': Decimal('0.000125'),   # Same as intraday (rare)
        
        # OPTIONS - Index/Stock (on premium, sell side)
        'OPT_NORMAL_SELL': Decimal('0.000625'),     # 0.0625% on sell premium (normal expiry)
        'OPT_EXERCISED_SELL': Decimal('0.00125'),   # 0.125% on sell premium (if exercised)
        'OPT_BUY': Decimal('0'),                     # No STT on option buy
        
        # COMMODITIES - Futures (on sell side)
        'COM_FUT_SELL_NONAGRI': Decimal('0.0001'),  # 0.01% on sell (non-agricultural)
        'COM_FUT_SELL_AGRI': Decimal('0.0005'),     # 0.05% on sell (agricultural)
        
        # COMMODITIES - Options (on premium sell side)
        'COM_OPT_SELL': Decimal('0.0005'),          # 0.05% on sell premium
    }
    
    # ========== STAMP DUTY RATES ==========
    # Segment and product type specific
    STAMP_DUTY_RATES = {
        'EQ_INTRADAY': Decimal('0.00003'),          # 0.003% on buy side
        'EQ_DELIVERY': Decimal('0.00015'),          # 0.015% on buy side
        'FUT': Decimal('0.00002'),                  # 0.002% on buy side
        'OPT': Decimal('0.00003'),                  # 0.003% on buy premium
        'COM_FUT': Decimal('0.00002'),              # 0.002% on buy side
        'COM_OPT': Decimal('0.00003'),              # 0.003% on buy premium
    }
    
    # ========== OTHER CHARGES ==========
    SEBI_RATE = Decimal('0.000001')           # ₹10 per crore = 0.0001%
    GST_RATE = Decimal('0.18')                 # 18% on taxable charges
    
    # DP Charges - Delivery DP
    # Typical: ₹13.50 per ISIN + 18% GST (deliver only)
    DP_CHARGE_PER_ISIN = Decimal('13.50')      # Flat charge per ISIN on sell
    
    # Clearing Charges (if applicable)
    # Typically 0.001% for equity, varies for derivatives
    CLEARING_CHARGE_EQ = Decimal('0.000002')   # ~₹2 per crore for equity
    CLEARING_CHARGE_FUT = Decimal('0')         # Usually included in brokerage
    CLEARING_CHARGE_OPT = Decimal('0')         # Usually included in brokerage


class ChargeCalculator:
    """
    Calculates all trading charges for a closed position.
    
    Properly implements Indian statutory structure with support for:
    - All equity segments (NSE/BSE, intraday/delivery)
    - All futures segments (index/stock)
    - All options segments (index/stock/commodity)
    - Commodity segments (MCX)
    """
    
    def __init__(self):
        self.rates = ChargeRates()
        self.allowed_exchange_segments = {'NSE_EQ', 'NSE_FNO', 'BSE_EQ', 'MCX_COMM'}
        self.allowed_instrument_types = {'EQUITY', 'FUTIDX', 'FUTSTK', 'OPTIDX', 'OPTSTK', 'FUTCOM', 'OPTCOM'}
        self.allowed_product_types = {'MIS', 'NORMAL'}
    
    def calculate_all_charges(
        self,
        buy_price: float,
        sell_price: float,
        quantity: int,
        exchange_segment: str,
        product_type: str,
        instrument_type: str,
        brokerage_flat: float = 20.0,
        brokerage_percent: float = 0.0,
        is_option: bool = False,
        is_commodity: bool = False,
        is_agricultural_commodity: bool = False,
        option_exercised: bool = False,
        apply_dp_charges: bool = False,
    ) -> Dict[str, float]:
        """
        Calculate all charges for a closed position.
        
        Args:
            buy_price: Entry/buy price
            sell_price: Exit/sell price
            quantity: Position quantity
            exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, MCX_COMM
            product_type: MIS (intraday) or NORMAL (delivery)
            instrument_type: EQUITY, FUTIDX, OPTIDX, FUTSTK, OPTSTK, FUTCOM, OPTCOM
            brokerage_flat: Flat fee (e.g., 20)
            brokerage_percent: Percentage fee (e.g., 0.002 for 0.2%)
            is_option: Whether this is an option contract
            is_commodity: Whether this is a commodity
            is_agricultural_commodity: Whether commodity is agricultural
            option_exercised: Whether option was exercised (affects STT)
            apply_dp_charges: Whether DP charges apply (delivery equity sell)
        
        Returns:
            Dict with all charge components
        """
        try:
            exchange_segment = exchange_segment.upper().strip()
            product_type = product_type.upper().strip()
            instrument_type = instrument_type.upper().strip()

            logger.info(
                "Charge input routing: exchange_segment=%s, product_type=%s, instrument_type=%s, is_option=%s, is_commodity=%s",
                exchange_segment,
                product_type,
                instrument_type,
                is_option,
                is_commodity,
            )

            if exchange_segment not in self.allowed_exchange_segments:
                raise ValueError(
                    f"Invalid exchange_segment '{exchange_segment}'. Allowed: {sorted(self.allowed_exchange_segments)}"
                )

            if instrument_type not in self.allowed_instrument_types:
                raise ValueError(
                    f"Invalid instrument_type '{instrument_type}'. Allowed: {sorted(self.allowed_instrument_types)}"
                )

            if product_type not in self.allowed_product_types:
                raise ValueError(
                    f"Invalid product_type '{product_type}'. Allowed: {sorted(self.allowed_product_types)}"
                )

            # Convert inputs to Decimal for precision
            buy_price_dec = Decimal(str(buy_price))
            sell_price_dec = Decimal(str(sell_price))
            qty_dec = Decimal(str(abs(quantity)))
            brokerage_flat_dec = Decimal(str(brokerage_flat))
            brokerage_percent_dec = Decimal(str(brokerage_percent))
            
            # Calculate turnover
            buy_value = buy_price_dec * qty_dec
            sell_value = sell_price_dec * qty_dec
            total_turnover = buy_value + sell_value
            
            # For options, premium_turnover is the actual premium value
            # In this new structure, we receive actual buy/sell prices for options
            premium_turnover = total_turnover if is_option else None
            
            # === 1. BROKERAGE ===
            brokerage = self._calculate_brokerage(
                total_turnover,
                brokerage_flat_dec,
                brokerage_percent_dec
            )
            
            # === 2. STT/CTT ===
            stt_ctt = self._calculate_stt_ctt(
                buy_value=buy_value,
                sell_value=sell_value,
                premium_value=premium_turnover,
                exchange_segment=exchange_segment,
                product_type=product_type,
                instrument_type=instrument_type,
                is_option=is_option,
                is_commodity=is_commodity,
                is_agricultural_commodity=is_agricultural_commodity,
                option_exercised=option_exercised
            )
            
            # === 3. STAMP DUTY ===
            stamp_duty = self._calculate_stamp_duty(
                buy_value=buy_value,
                premium_value=premium_turnover if is_option else None,
                product_type=product_type,
                instrument_type=instrument_type,
                is_option=is_option,
                is_commodity=is_commodity
            )
            
            # === 4. EXCHANGE TRANSACTION CHARGES ===
            exchange_charge = self._calculate_exchange_charge(
                total_turnover=total_turnover,
                premium_value=premium_turnover,
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                is_option=is_option
            )
            
            # === 5. SEBI CHARGES ===
            sebi_charge = self._calculate_sebi_charge(total_turnover)
            
            # === 6. DP CHARGES (Delivery equity sell) ===
            dp_charge = Decimal('0')
            if apply_dp_charges and exchange_segment in {'NSE_EQ', 'BSE_EQ'} and product_type == 'NORMAL':
                # Flat ₹13.50 per ISIN on sell side (delivery)
                dp_charge = self.rates.DP_CHARGE_PER_ISIN
            
            # === 7. CLEARING CHARGES ===
            clearing_charge = self._calculate_clearing_charge(
                total_turnover=total_turnover,
                instrument_type=instrument_type
            )
            
            # === 8. GST CALCULATION ===
            # GST = 18% on (Brokerage + Exchange + SEBI + Clearing + DP)
            # NOT on STT, Stamp Duty (already tax)
            gst_taxable = brokerage + exchange_charge + sebi_charge + clearing_charge + dp_charge
            gst_charge = gst_taxable * self.rates.GST_RATE
            
            # === 9. TOTALS ===
            # platform_cost = what broker keeps (brokerage only)
            # trade_expense = all regulatory/statutory charges
            platform_cost = brokerage
            trade_expense = stt_ctt + stamp_duty + exchange_charge + sebi_charge + clearing_charge + dp_charge + gst_charge
            total_charges = platform_cost + trade_expense
            
            # Round to 2 decimal places
            result = {
                'brokerage_charge': self._round_to_2decimals(brokerage),
                'stt_ctt_charge': self._round_to_0decimals(stt_ctt),
                'stamp_duty': self._round_to_2decimals(stamp_duty),
                'exchange_charge': self._round_to_2decimals(exchange_charge),
                'sebi_charge': self._round_to_2decimals(sebi_charge),
                'dp_charge': self._round_to_2decimals(dp_charge),
                'clearing_charge': self._round_to_2decimals(clearing_charge),
                'ipft_charge': Decimal('0'),  # IPFT = Investments & Profits Fund Tax (rarely applied)
                'gst_charge': self._round_to_2decimals(gst_charge),
                'platform_cost': self._round_to_2decimals(platform_cost),
                'trade_expense': self._round_to_2decimals(trade_expense),
                'total_charges': self._round_to_2decimals(total_charges),
            }
            
            logger.info(f"Charges calculated: {result}")
            return result
            
        except Exception as e:
            logger.error(f"Error calculating charges: {e}", exc_info=True)
            raise
    
    # ========== CALCULATION METHODS ==========
    
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
        buy_value: Decimal,
        sell_value: Decimal,
        premium_value: Optional[Decimal],
        exchange_segment: str,
        product_type: str,
        instrument_type: str,
        is_option: bool,
        is_commodity: bool,
        is_agricultural_commodity: bool,
        option_exercised: bool
    ) -> Decimal:
        """
        Calculate STT/CTT based on segment, product type and instrument.
        
        Key Rules:
        - Equity Intraday: 0.025% on SELL side only
        - Equity Delivery: 0.1% on BOTH buy and sell
        - Futures: 0.0125% on SELL side
        - Options: On premium value, sell side
        - Commodities: Different rates for agri vs non-agri
        """
        
        # EQUITY - Intraday
        if exchange_segment in {'NSE_EQ', 'BSE_EQ'} and product_type == 'MIS' and instrument_type == 'EQUITY':
            # Only 0.025% on sell side
            return sell_value * self.rates.STT_RATES['EQ_INTRADAY_SELL']
        
        # EQUITY - Delivery
        if exchange_segment in {'NSE_EQ', 'BSE_EQ'} and product_type == 'NORMAL' and instrument_type == 'EQUITY':
            # 0.1% on BUY + 0.1% on SELL
            buy_stt = buy_value * self.rates.STT_RATES['EQ_DELIVERY_BUY']
            sell_stt = sell_value * self.rates.STT_RATES['EQ_DELIVERY_SELL']
            return buy_stt + sell_stt
        
        # FUTURES - Index and Stock
        if exchange_segment == 'NSE_FNO' and instrument_type in {'FUTIDX', 'FUTSTK'} and not is_commodity:
            # 0.0125% on sell side only
            return sell_value * self.rates.STT_RATES['FUT_INTRADAY_SELL']
        
        # OPTIONS - Index and Stock
        if exchange_segment == 'NSE_FNO' and instrument_type in {'OPTIDX', 'OPTSTK'} and is_option and not is_commodity:
            if premium_value is None:
                return Decimal('0')
            
            # Premium is already buy + sell, so half is sell side
            premium_sell = premium_value / Decimal('2')
            
            if option_exercised:
                # 0.125% on sell premium if exercised
                return premium_sell * self.rates.STT_RATES['OPT_EXERCISED_SELL']
            else:
                # 0.0625% on sell premium (normal/expiry)
                return premium_sell * self.rates.STT_RATES['OPT_NORMAL_SELL']
        
        # COMMODITIES - Futures
        if exchange_segment == 'MCX_COMM' and instrument_type == 'FUTCOM' and is_commodity:
            if is_agricultural_commodity:
                # 0.05% for agricultural commodities
                rate = self.rates.STT_RATES['COM_FUT_SELL_AGRI']
            else:
                # 0.01% for non-agricultural commodities
                rate = self.rates.STT_RATES['COM_FUT_SELL_NONAGRI']
            return sell_value * rate
        
        # COMMODITIES - Options
        if exchange_segment == 'MCX_COMM' and instrument_type == 'OPTCOM' and is_option and is_commodity:
            if premium_value is None:
                return Decimal('0')
            
            premium_sell = premium_value / Decimal('2')
            return premium_sell * self.rates.STT_RATES['COM_OPT_SELL']
        
        return Decimal('0')
    
    def _calculate_stamp_duty(
        self,
        buy_value: Decimal,
        premium_value: Optional[Decimal],
        product_type: str,
        instrument_type: str,
        is_option: bool,
        is_commodity: bool
    ) -> Decimal:
        """
        Calculate stamp duty based on segment and product type.
        
        Key Rules:
        - Equity Intraday: 0.003% on BUY side
        - Equity Delivery: 0.015% on BUY side
        - Futures: 0.002% on BUY side
        - Options: 0.003% on BUY premium
        - Commodities: Similar structure
        """
        
        # EQUITY
        if instrument_type == 'EQUITY':
            if product_type == 'MIS':
                # 0.003% on buy side for intraday
                rate = self.rates.STAMP_DUTY_RATES['EQ_INTRADAY']
            else:
                # 0.015% on buy side for delivery
                rate = self.rates.STAMP_DUTY_RATES['EQ_DELIVERY']
            return buy_value * rate
        
        # FUTURES (both equity and commodity)
        if instrument_type in {'FUTIDX', 'FUTSTK', 'FUTCOM'}:
            # 0.002% on buy side
            rate = self.rates.STAMP_DUTY_RATES['FUT'] if not is_commodity else self.rates.STAMP_DUTY_RATES['COM_FUT']
            return buy_value * rate
        
        # OPTIONS (both equity and commodity)
        if is_option and instrument_type in {'OPTIDX', 'OPTSTK', 'OPTCOM'}:
            # Buy premium only (buy side premium value)
            premium_buy = buy_value
            rate = self.rates.STAMP_DUTY_RATES['OPT'] if not is_commodity else self.rates.STAMP_DUTY_RATES['COM_OPT']
            return premium_buy * rate
        
        return Decimal('0')
    
    def _calculate_exchange_charge(
        self,
        total_turnover: Decimal,
        premium_value: Optional[Decimal],
        exchange_segment: str,
        instrument_type: str,
        is_option: bool
    ) -> Decimal:
        """
        Calculate exchange transaction charges.
        
        Rules:
        - Equity: ~0.00325% on total turnover
        - Futures: ~0.002% on total turnover
        - Options: ~0.035% on premium value
        - Commodities: Varies
        """
        
        # EQUITY
        if exchange_segment == 'NSE_EQ':
            rate = self.rates.EXCHANGE_RATES['NSE_EQ_INTRADAY']
            return total_turnover * rate
        if exchange_segment == 'BSE_EQ':
            rate = self.rates.EXCHANGE_RATES['BSE_EQ']
            return total_turnover * rate
        
        # OPTIONS (charged on premium)
        if exchange_segment == 'NSE_FNO' and is_option:
            if premium_value is None:
                return Decimal('0')
            rate = self.rates.EXCHANGE_RATES['NSE_FNO_OPTIONS']
            return premium_value * rate

        if exchange_segment == 'BSE_EQ' and is_option:
            if premium_value is None:
                return Decimal('0')
            rate = self.rates.EXCHANGE_RATES['BSE_FNO_OPTIONS']
            return premium_value * rate
        
        # FUTURES
        if exchange_segment == 'NSE_FNO' and instrument_type in {'FUTIDX', 'FUTSTK'}:
            rate = self.rates.EXCHANGE_RATES['NSE_FNO_FUTURES']
            return total_turnover * rate

        if exchange_segment == 'MCX_COMM' and instrument_type == 'FUTCOM':
            rate = self.rates.EXCHANGE_RATES['MCX_COMMODITY_FUTURES']
            return total_turnover * rate

        if exchange_segment == 'MCX_COMM' and instrument_type == 'OPTCOM':
            if premium_value is None:
                return Decimal('0')
            rate = self.rates.EXCHANGE_RATES['MCX_COMMODITY_OPTIONS']
            return premium_value * rate
        
        # Default
        raise ValueError(
            f"Unsupported exchange routing combination: exchange_segment={exchange_segment}, "
            f"instrument_type={instrument_type}, is_option={is_option}"
        )
    
    def _calculate_sebi_charge(self, turnover: Decimal) -> Decimal:
        """
        Calculate SEBI charges.
        
        Rate: ₹10 per crore = (turnover / 100,000,000) × 10 = turnover × 0.000001
        """
        return turnover * self.rates.SEBI_RATE
    
    def _calculate_clearing_charge(
        self,
        total_turnover: Decimal,
        instrument_type: str
    ) -> Decimal:
        """
        Calculate clearing charges (if applicable).
        
        Usually low or zero (often included in brokerage for futures/options).
        """
        # For equity, slight clearing charge
        if instrument_type == 'EQUITY':
            return total_turnover * self.rates.CLEARING_CHARGE_EQ
        
        # Futures and options typically no separate clearing charge
        return Decimal('0')
    
    def _round_to_2decimals(self, value: Decimal) -> float:
        """Round Decimal to 2 decimal places using banker's rounding."""
        rounded = value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return float(rounded)

    def _round_to_0decimals(self, value: Decimal) -> float:
        """Round Decimal to nearest rupee (0 decimal places)."""
        rounded = value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        return float(rounded)


# Global singleton instance
charge_calculator = ChargeCalculator()


def calculate_position_charges(
    quantity: int,
    buy_price: float,
    sell_price: float,
    exchange_segment: str,
    product_type: str,
    instrument_type: str,
    brokerage_flat: float = 20.0,
    brokerage_percent: float = 0.0,
    is_option: bool = False,
    is_commodity: bool = False,
    is_agricultural_commodity: bool = False,
    option_exercised: bool = False,
    apply_dp_charges: bool = False,
) -> Dict[str, float]:
    """
    Convenience function to calculate charges for a closed position.
    
    Args:
        quantity: Position quantity
        buy_price: Entry/buy price
        sell_price: Exit/sell price
        exchange_segment: NSE_EQ, NSE_FNO, BSE_EQ, MCX_COMM
        product_type: MIS (intraday) or NORMAL (delivery)
        instrument_type: EQUITY, FUTIDX, OPTIDX, FUTSTK, OPTSTK, FUTCOM, OPTCOM
        brokerage_flat: Flat brokerage fee
        brokerage_percent: Percentage brokerage
        is_option: Whether this is an option
        is_commodity: Whether this is a commodity
        is_agricultural_commodity: For MCX, whether commodity is agricultural
        option_exercised: Whether option was exercised
        apply_dp_charges: Whether DP charges apply (delivery equity sell)
    
    Returns:
        Dictionary with all charge components
    """
    return charge_calculator.calculate_all_charges(
        buy_price=buy_price,
        sell_price=sell_price,
        quantity=quantity,
        exchange_segment=exchange_segment,
        product_type=product_type,
        instrument_type=instrument_type,
        brokerage_flat=brokerage_flat,
        brokerage_percent=brokerage_percent,
        is_option=is_option,
        is_commodity=is_commodity,
        is_agricultural_commodity=is_agricultural_commodity,
        option_exercised=option_exercised,
        apply_dp_charges=apply_dp_charges,
    )
