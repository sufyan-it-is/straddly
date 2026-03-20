/**
 * Trading configuration — lot sizes, strike intervals, and index metadata.
 */

const TRADING_CONFIG = {
  NIFTY: {
    lotSize:         65,
    strikeInterval:  50,
    weekly:          true,
    exchange:        'NSE_FNO',
    displayName:     'NIFTY 50',
  },
  BANKNIFTY: {
    lotSize:         30,
    strikeInterval:  100,
    weekly:          true,
    exchange:        'NSE_FNO',
    displayName:     'BANK NIFTY',
  },
  SENSEX: {
    lotSize:         20,
    strikeInterval:  100,
    weekly:          true,
    exchange:        'BSE_FNO',
    displayName:     'SENSEX',
  },
  MIDCPNIFTY: {
    lotSize:         75,
    strikeInterval:  25,
    weekly:          true,
    exchange:        'NSE_FNO',
    displayName:     'MIDCAP NIFTY',
  },
  FINNIFTY: {
    lotSize:         65,
    strikeInterval:  50,
    weekly:          true,
    exchange:        'NSE_FNO',
    displayName:     'FIN NIFTY',
  },
  BANKEX: {
    lotSize:         15,
    strikeInterval:  100,
    weekly:          false,
    exchange:        'BSE_FNO',
    displayName:     'BANKEX',
  },
};

export function getLotSize(underlying) {
  const key = (underlying || '').toUpperCase().trim();
  return TRADING_CONFIG[key]?.lotSize || 1;
}

export function getStrikeInterval(underlying) {
  const key = (underlying || '').toUpperCase().trim();
  return TRADING_CONFIG[key]?.strikeInterval || 50;
}

export function isWeeklyIndex(underlying) {
  const key = (underlying || '').toUpperCase().trim();
  return TRADING_CONFIG[key]?.weekly || false;
}

export function getExchange(underlying) {
  const key = (underlying || '').toUpperCase().trim();
  return TRADING_CONFIG[key]?.exchange || 'NSE_FNO';
}

export function getDisplayName(underlying) {
  const key = (underlying || '').toUpperCase().trim();
  return TRADING_CONFIG[key]?.displayName || underlying;
}

export const UNDERLYINGS = Object.keys(TRADING_CONFIG);

export default TRADING_CONFIG;
