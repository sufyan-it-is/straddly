/**
 * Normalize underlying symbol names to canonical form.
 * e.g. "NIFTY 50" → "NIFTY", "NIFTY BANK" → "BANKNIFTY"
 */
const SYMBOL_MAP = {
  'NIFTY 50':    'NIFTY',
  'NIFTY50':     'NIFTY',
  'NIFTY BANK':  'BANKNIFTY',
  'NIFTYBANK':   'BANKNIFTY',
  'BANKN IFTY':  'BANKNIFTY',
  'BSE SENSEX':  'SENSEX',
  'BSESENSEX':   'SENSEX',
  'SENSEX 30':   'SENSEX',
  'MIDCPNIFTY':  'MIDCPNIFTY',
  'MIDCAP NIFTY':'MIDCPNIFTY',
  'FINNIFTY':    'FINNIFTY',
  'FIN NIFTY':   'FINNIFTY',
};

export function normalizeUnderlying(symbol) {
  if (!symbol) return symbol;
  const upper = symbol.toUpperCase().trim();
  return SYMBOL_MAP[upper] || upper;
}

export function getDisplayName(symbol) {
  const normalized = normalizeUnderlying(symbol);
  const DISPLAY = {
    NIFTY:      'NIFTY 50',
    BANKNIFTY:  'BANK NIFTY',
    SENSEX:     'SENSEX',
    MIDCPNIFTY: 'MIDCAP NIFTY',
    FINNIFTY:   'FIN NIFTY',
  };
  return DISPLAY[normalized] || normalized;
}

export default normalizeUnderlying;
