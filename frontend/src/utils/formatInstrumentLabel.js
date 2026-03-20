/**
 * Utility function to format instrument display labels with expiry dates
 * Used for options to show complete information when trading
 */

/**
 * Format an option instrument label to include expiry date
 * @param {Object} config - Configuration object
 * @param {string} config.instrumentType - Type of instrument (e.g., 'OPTION', 'OPTIDX')
 * @param {string} config.symbol - Base symbol (e.g., 'NIFTY 24600 CE')
 * @param {string} config.expiryDate - ISO expiry date string
 * @param {number} config.strikePrice - Strike price
 * @param {string} config.optionType - Option type ('CE' or 'PE')
 * @param {string} config.underlying - Underlying symbol (e.g., 'NIFTY')
 * @returns {string} Formatted display label (e.g., 'NIFTY 24600 CE 10 MAR')
 */
export const formatOptionLabel = (config) => {
  const {
    instrumentType = '',
    symbol = '',
    expiryDate = null,
    strikePrice = null,
    optionType = '',
    underlying = '',
  } = config;

  const it = String(instrumentType || '').toUpperCase();
  const isOption = it.startsWith('OPT') && expiryDate && strikePrice !== null && strikePrice !== undefined && optionType;

  if (!isOption) {
    // Not an option, return the raw symbol
    return symbol || '';
  }

  try {
    const d = new Date(expiryDate);
    if (isNaN(d.getTime())) {
      // Invalid date, return symbol without expiry
      return symbol || '';
    }
    const monthShort = d.toLocaleString('en-GB', { month: 'short' }).toUpperCase();
    const day = String(d.getDate()).padStart(2, '0');
    const strikeNum = Number(strikePrice);
    const strikeTxt = Number.isFinite(strikeNum) ? String(Math.trunc(strikeNum)) : String(strikePrice);
    const underlyingUpper = (underlying || '').toUpperCase();
    const optTypeUpper = String(optionType).toUpperCase();

    return `${underlyingUpper} ${strikeTxt} ${optTypeUpper} ${day}${monthShort}`;
  } catch (e) {
    console.warn('[formatOptionLabel] Error formatting:', e);
    return symbol || '';
  }
};
