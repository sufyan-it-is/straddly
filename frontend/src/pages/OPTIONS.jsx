import React, { useState, useEffect, useRef } from 'react';
import { useAuthoritativeOptionChain } from '../hooks/useAuthoritativeOptionChain';
import normalizeUnderlying from '../utils/underlying';
import { apiService } from '../services/apiService';
import { getLotSize as getConfiguredLotSize } from '../config/tradingConfig';
import { useAuth } from '../contexts/AuthContext';
import { formatOptionLabel } from '../utils/formatInstrumentLabel';

const Options = ({ handleOpenOrderModal, selectedIndex = 'NIFTY 50', expiry }) => {
  const { user } = useAuth();
  const [underlyingPrice, setUnderlyingPrice] = useState(null);
  const [displayCenterStrike, setDisplayCenterStrike] = useState(null);
  const [isCenterLocked, setIsCenterLocked] = useState(false);
  const [openActionMenuKey, setOpenActionMenuKey] = useState(null);
  const [message, setMessage] = useState(null);
  const [bidAskModal, setBidAskModal] = useState(null);
  const listRef = useRef(null);
  const didInitialScroll = useRef(false);
  const lastScrolledAtmRef = useRef(null);

  const symbol = normalizeUnderlying(selectedIndex);

  const resolveOptionSegment = (underlyingSymbol) => {
    const upper = String(underlyingSymbol || '').toUpperCase();
    if (upper === 'SENSEX' || upper === 'BANKEX') return 'BSE_FNO';
    return 'NSE_FNO';
  };

  const { data: chainData, loading: chainLoading, error: chainError, refresh: refreshChain, recalibrate: recalibrateChain, getATMStrike } =
    useAuthoritativeOptionChain(symbol, expiry, { autoRefresh: true, refreshInterval: 1000 });

  // Ignore stale payloads from the previous symbol/expiry while the next request is in flight.
  const chainMatchesSelection =
    !!chainData &&
    chainData.underlying === symbol &&
    (expiry == null || chainData.expiry === expiry);
  const activeChainData = chainMatchesSelection ? chainData : null;

  const lotSize = React.useMemo(() => {
    const configured = getConfiguredLotSize(symbol);
    const fromChain = activeChainData?.lot_size;
    return fromChain && fromChain > 0 ? fromChain : configured;
  }, [symbol, activeChainData?.lot_size]);

  useEffect(() => {
    setUnderlyingPrice(null);
    setDisplayCenterStrike(null);
    setIsCenterLocked(false);
    didInitialScroll.current = false;
    lastScrolledAtmRef.current = null;
  }, [symbol, expiry]);

  useEffect(() => {
    const ltp = activeChainData?.underlying_ltp;
    if (typeof ltp === 'number' && ltp > 0) setUnderlyingPrice(ltp);
  }, [activeChainData?.underlying_ltp]);

  const effectiveAtmStrike = React.useMemo(() => {
    // Primary ATM source = underlying LTP rounded to strike interval (hook helper).
    const fromLtp = getATMStrike();
    if (typeof fromLtp === 'number' && fromLtp > 0) return fromLtp;

    // Fallback = backend ATM cache.
    const backendAtm = activeChainData?.atm_strike || activeChainData?.atm || null;
    return (typeof backendAtm === 'number' && backendAtm > 0) ? backendAtm : null;
  }, [getATMStrike, activeChainData?.atm_strike, activeChainData?.atm]);

  const visualAtmStrike = (isCenterLocked ? displayCenterStrike : null) ?? effectiveAtmStrike;
  const highlightedAtmStrike = effectiveAtmStrike ?? visualAtmStrike;

  const strikes = React.useMemo(() => {
    if (!activeChainData || !activeChainData.strikes) return [];
    return Object.entries(activeChainData.strikes)
      .map(([strikeStr, strikeData]) => {
        const strike = parseFloat(strikeStr);
        return {
          strike,
          isATM: highlightedAtmStrike && strike === highlightedAtmStrike,
          ltpCE: strikeData.CE?.ltp || 0,
          ltpPE: strikeData.PE?.ltp || 0,
          bidCE: strikeData.CE?.bid || 0,
          askCE: strikeData.CE?.ask || 0,
          bidPE: strikeData.PE?.bid || 0,
          askPE: strikeData.PE?.ask || 0,
          depthCE: strikeData.CE?.depth || null,
          depthPE: strikeData.PE?.depth || null,
          ceToken: strikeData.CE?.instrument_token || null,
          peToken: strikeData.PE?.instrument_token || null,
          ceGreeks: strikeData.CE?.greeks || {},
          peGreeks: strikeData.PE?.greeks || {},
          ceSource: strikeData.CE?.source || 'live',
          peSource: strikeData.PE?.source || 'live',
          lotSize,
        };
      })
      .sort((a, b) => a.strike - b.strike);
  }, [activeChainData, highlightedAtmStrike, lotSize]);

  const atmStrike = effectiveAtmStrike;

  useEffect(() => {
    if (!strikes.length) return;

    const strikeValues = strikes.map((s) => s.strike).sort((a, b) => a - b);
    const liveAtm = (typeof atmStrike === 'number' && atmStrike > 0) ? atmStrike : null;
    const intervalFromChain = Number(activeChainData?.strike_interval || 0);
    const inferredInterval = strikeValues.length > 1
      ? Math.min(...strikeValues.slice(1).map((v, i) => Math.abs(v - strikeValues[i])).filter((n) => n > 0))
      : 0;
    const strikeInterval = intervalFromChain > 0 ? intervalFromChain : inferredInterval;

    const nearestStrike = (target) => {
      if (target == null) return strikeValues[Math.floor(strikeValues.length / 2)] || null;
      let nearest = strikeValues[0];
      let minDiff = Math.abs(nearest - target);
      strikeValues.forEach((v) => {
        const d = Math.abs(v - target);
        if (d < minDiff) {
          minDiff = d;
          nearest = v;
        }
      });
      return nearest;
    };

    const hasTradablePremiums = strikes.some((s) => Number(s.ltpCE) > 0 || Number(s.ltpPE) > 0);

    if (displayCenterStrike == null) {
      // Avoid locking to a potentially stale center during transient empty/zero snapshots.
      if (!hasTradablePremiums && liveAtm == null) return;
      setDisplayCenterStrike(nearestStrike(liveAtm));
      setIsCenterLocked(true);
      return;
    }

    // Guard against stale lock: realign when center drifts materially from live ATM.
    // This keeps the highlighted row aligned with the current ATM after data source updates.
    if (liveAtm != null && strikeInterval > 0) {
      const drift = Math.abs(displayCenterStrike - liveAtm);
      if (drift >= strikeInterval * 2) {
        setDisplayCenterStrike(nearestStrike(liveAtm));
      }
    }
  }, [strikes, activeChainData?.strike_interval, atmStrike, displayCenterStrike]);

  const displayedStrikes = React.useMemo(() => {
    if (!strikes.length) return [];
    const sorted = strikes.map(s => s.strike).sort((a, b) => a - b);
    const atm = highlightedAtmStrike ?? displayCenterStrike ?? atmStrike ?? null;
    if (atm == null) return strikes;
    let centerIdx = sorted.findIndex(v => v === atm);
    if (centerIdx < 0) {
      let nearest = 0; let minDiff = Infinity;
      sorted.forEach((v, i) => { const d = Math.abs(v - atm); if (d < minDiff) { minDiff = d; nearest = i; } });
      centerIdx = nearest;
    }
    const total = 31;
    let start = Math.max(0, centerIdx - 15);
    let end = start + total - 1;
    if (end > sorted.length - 1) { end = sorted.length - 1; start = Math.max(0, end - total + 1); }
    const allowed = new Set(sorted.slice(start, end + 1));
    return strikes.filter(s => allowed.has(s.strike));
  }, [strikes, highlightedAtmStrike, atmStrike, displayCenterStrike]);

  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const atmEl = el.querySelector('[data-atm="true"]');
    if (!atmEl) return;

    // Re-center whenever ATM row changes for a symbol/expiry, not only once.
    const currentAtm = highlightedAtmStrike ?? displayCenterStrike ?? atmStrike ?? null;
    if (didInitialScroll.current && lastScrolledAtmRef.current === currentAtm) return;

    const elRect = el.getBoundingClientRect();
    const rowRect = atmEl.getBoundingClientRect();
    const delta = rowRect.top - elRect.top;
    const target = el.scrollTop + delta - (el.clientHeight / 2) + (atmEl.clientHeight / 2);
    el.scrollTo({ top: Math.max(target, 0), behavior: 'smooth' });
    didInitialScroll.current = true;
    lastScrolledAtmRef.current = currentAtm;
  }, [displayedStrikes, highlightedAtmStrike, displayCenterStrike, atmStrike]);

  useEffect(() => {
    const handleOutsideClick = (event) => {
      if (!event.target.closest('.leg-action-root')) setOpenActionMenuKey(null);
    };
    document.addEventListener('mousedown', handleOutsideClick);
    return () => document.removeEventListener('mousedown', handleOutsideClick);
  }, []);

  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(null), 2200);
    return () => clearTimeout(timer);
  }, [message]);

  const isIndexSymbol = (value) => {
    const indexSet = new Set(['NIFTY', 'BANKNIFTY', 'SENSEX', 'FINNIFTY', 'MIDCPNIFTY', 'BANKEX']);
    return indexSet.has(String(value || '').toUpperCase());
  };

  const addLegToWatchlist = async ({ optionType, strikeData }) => {
    try {
      if (!user?.id) { setMessage({ type: 'error', text: 'User not available for watchlist action' }); return; }
      const token = optionType === 'CE' ? strikeData.ceToken : strikeData.peToken;
      if (!token) { setMessage({ type: 'error', text: 'Instrument token not available for this strike' }); return; }
      const optionSymbol = `${symbol} ${strikeData.strike} ${optionType}`;
      const exchange = resolveOptionSegment(symbol);
      const payload = { user_id: String(user.id), token: String(token), symbol: optionSymbol, exchange };
      await apiService.post('/watchlist/add', payload);
      setMessage({ type: 'success', text: `${optionSymbol} added to watchlist` });
      window.dispatchEvent(new CustomEvent('tn-watchlist-refresh'));
    } catch (error) {
      setMessage({ type: 'error', text: error?.message || 'Failed to add to watchlist' });
    }
  };

  const openBidAsk = ({ optionType, strikeData }) => {
    const bid = optionType === 'CE' ? Number(strikeData.bidCE || 0) : Number(strikeData.bidPE || 0);
    const ask = optionType === 'CE' ? Number(strikeData.askCE || 0) : Number(strikeData.askPE || 0);
    setBidAskModal({ symbol, strike: strikeData.strike, optionType, bid, ask });
  };

  const legMenuKey = (strike, optionType) => `${strike}-${optionType}`;

  const LegActionMenu = ({ strikeData, optionType }) => {
    const key = legMenuKey(strikeData.strike, optionType);
    const isOpen = openActionMenuKey === key;
    const btnRef = useRef(null);
    const [align, setAlign] = useState('right');

    useEffect(() => {
      if (!isOpen) return;
      const rect = btnRef.current?.getBoundingClientRect();
      if (!rect) return;
      const menuWidth = 160;
      if (rect.left < menuWidth) {
        setAlign('left');
        return;
      }
      if (rect.right + menuWidth > window.innerWidth) {
        setAlign('right');
        return;
      }
      setAlign(optionType === 'CE' ? 'left' : 'right');
    }, [isOpen, optionType]);

    return (
      <div className="relative leg-action-root">
        <button
          type="button"
          ref={btnRef}
          onClick={() => setOpenActionMenuKey(isOpen ? null : key)}
          className="leg-action-btn"
          title="More actions"
        >
          ⋮
        </button>
        {isOpen && (
          <div
            className="leg-action-menu"
            style={align === 'left' ? { left: 0 } : { right: 0 }}
          >
            <button
              type="button"
              className="leg-action-item"
              onClick={async () => { setOpenActionMenuKey(null); await addLegToWatchlist({ optionType, strikeData }); }}
            >
              Add to watchlist
            </button>
            <button
              type="button"
              className="leg-action-item"
              onClick={() => { setOpenActionMenuKey(null); openBidAsk({ optionType, strikeData }); }}
            >
              Show bid-ask
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full" style={{ background: 'var(--surface)', color: 'var(--text)' }}>
      <div className="p-3 flex justify-between items-center" style={{ background: 'var(--surface2)', borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center space-x-2">
          <h3 className="font-semibold" style={{ color: 'var(--text)' }}>{symbol} OPTIONS</h3>
          {underlyingPrice && <span className="text-xs font-medium" style={{ color: 'var(--muted)' }}>LTP: {underlyingPrice.toFixed(2)}</span>}
          {getATMStrike() && <span className="text-xs text-green-600 font-medium">ATM: {getATMStrike()}</span>}
          {lotSize && <span className="text-xs text-blue-600 font-medium">Lot: {lotSize}</span>}
          {expiry && <span className="text-xs text-orange-600 font-medium">Exp: {expiry}</span>}
        </div>
        <button onClick={() => { setDisplayCenterStrike(null); setIsCenterLocked(false); recalibrateChain(); }} disabled={chainLoading} className="px-2 py-0.5 text-xs font-semibold rounded border border-indigo-400 text-indigo-600 hover:bg-indigo-50 transition-colors disabled:opacity-50" title="Re-centre strikes to current ATM">
          Re-centre
        </button>
        <button onClick={refreshChain} disabled={chainLoading} className="p-1 hover:bg-gray-200 rounded transition-colors disabled:opacity-50" title="Refresh data">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
        </button>
      </div>

      <div className="overflow-y-auto flex-grow" ref={listRef} style={{ maxHeight: '640px' }}>
        {message && (
          <div className={`m-2 p-2 rounded text-xs border ${message.type === 'success' ? 'bg-green-50 border-green-200 text-green-700' : 'bg-red-50 border-red-200 text-red-700'}`}>
            {message.text}
          </div>
        )}
        {chainLoading && !strikes.length && (
          <div className="m-2 p-3 bg-blue-50 border border-blue-200 rounded text-blue-700 text-sm text-center">
            <div className="animate-spin inline-block mr-2">⚙️</div>Loading option chain...
          </div>
        )}
        {chainError && !strikes.length && (
          <div className="m-2 p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm text-center">
            <strong>Error:</strong> {chainError}
            <button onClick={refreshChain} className="ml-2 px-2 py-1 bg-red-600 text-white rounded text-xs hover:bg-red-700">Retry</button>
          </div>
        )}
        {!chainLoading && !chainError && strikes.length === 0 && (
          <div className="m-2 p-3 bg-yellow-50 border border-yellow-200 rounded text-yellow-700 text-sm text-center">
            {expiry ? 'No strikes available for this expiry.' : 'Select an expiry date to view options.'}
          </div>
        )}
        {displayedStrikes.length > 0 && (
          <div className="grid grid-cols-3 p-2 text-xs font-bold uppercase sticky top-0 z-10" style={{ background: 'var(--surface2)', color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
            <div className="text-left">CE Premium</div>
            <div className="text-center">Strike</div>
            <div className="text-right">PE Premium</div>
          </div>
        )}
        {displayedStrikes.map((strikeData) => (
          <div key={strikeData.strike} data-atm={strikeData.isATM ? 'true' : 'false'} className={`grid grid-cols-3 p-1.5 items-center h-9 ${strikeData.isATM ? 'font-bold' : ''}`} style={{ borderBottom: '1px solid var(--border)', background: strikeData.isATM ? 'oklch(90% 0.002 286)' : 'var(--surface)', color: strikeData.isATM ? '#000' : 'var(--text)', fontSize: '11px' }}>
            {/* CE Column */}
            <div className="flex items-center justify-between pr-2">
              <div className="flex items-center gap-1">
                <span className="font-semibold" style={{ color: strikeData.isATM ? '#000' : 'var(--text)' }}>{strikeData.ltpCE > 0 ? strikeData.ltpCE.toFixed(2) : '0.00'}</span>
                <LegActionMenu strikeData={strikeData} optionType="CE" />
              </div>
              <div className="flex space-x-1 ml-2">
                <button onClick={() => { if (strikeData.ltpCE <= 0) return; const displaySymbol = formatOptionLabel({ instrumentType: 'OPTION', symbol: `${symbol} ${strikeData.strike} CE`, expiryDate: expiry, strikePrice: strikeData.strike, optionType: 'CE', underlying: symbol }); handleOpenOrderModal([{ symbol: `${symbol} ${strikeData.strike} CE`, displaySymbol: displaySymbol, action: 'BUY', ltp: strikeData.ltpCE, lotSize: strikeData.lotSize, underlying: symbol, security_id: strikeData.ceToken, exchange_segment: resolveOptionSegment(symbol), bid: strikeData.bidCE, ask: strikeData.askCE, strike: strikeData.strike, optionType: 'CE', depth: strikeData.depthCE, expiry }]); }} disabled={strikeData.ltpCE <= 0} className="trade-btn buy" title="Buy CE">B</button>
                <button onClick={() => { if (strikeData.ltpCE <= 0) return; const displaySymbol = formatOptionLabel({ instrumentType: 'OPTION', symbol: `${symbol} ${strikeData.strike} CE`, expiryDate: expiry, strikePrice: strikeData.strike, optionType: 'CE', underlying: symbol }); handleOpenOrderModal([{ symbol: `${symbol} ${strikeData.strike} CE`, displaySymbol: displaySymbol, action: 'SELL', ltp: strikeData.ltpCE, lotSize: strikeData.lotSize, underlying: symbol, security_id: strikeData.ceToken, exchange_segment: resolveOptionSegment(symbol), bid: strikeData.bidCE, ask: strikeData.askCE, strike: strikeData.strike, optionType: 'CE', depth: strikeData.depthCE, expiry }]); }} disabled={strikeData.ltpCE <= 0} className="trade-btn sell" title="Sell CE">S</button>
              </div>
            </div>
            {/* Strike Column */}
            <div className="flex items-center justify-center">
              <span className="font-bold" style={{ color: strikeData.isATM ? '#000' : 'var(--text)' }}>{strikeData.strike}</span>
            </div>
            {/* PE Column */}
            <div className="flex items-center justify-between pl-2">
              <div className="flex space-x-1 mr-2">
                <button onClick={() => { if (strikeData.ltpPE <= 0) return; const displaySymbol = formatOptionLabel({ instrumentType: 'OPTION', symbol: `${symbol} ${strikeData.strike} PE`, expiryDate: expiry, strikePrice: strikeData.strike, optionType: 'PE', underlying: symbol }); handleOpenOrderModal([{ symbol: `${symbol} ${strikeData.strike} PE`, displaySymbol: displaySymbol, action: 'BUY', ltp: strikeData.ltpPE, lotSize: strikeData.lotSize, underlying: symbol, security_id: strikeData.peToken, exchange_segment: resolveOptionSegment(symbol), bid: strikeData.bidPE, ask: strikeData.askPE, strike: strikeData.strike, optionType: 'PE', depth: strikeData.depthPE, expiry }]); }} disabled={strikeData.ltpPE <= 0} className="trade-btn buy" title="Buy PE">B</button>
                <button onClick={() => { if (strikeData.ltpPE <= 0) return; const displaySymbol = formatOptionLabel({ instrumentType: 'OPTION', symbol: `${symbol} ${strikeData.strike} PE`, expiryDate: expiry, strikePrice: strikeData.strike, optionType: 'PE', underlying: symbol }); handleOpenOrderModal([{ symbol: `${symbol} ${strikeData.strike} PE`, displaySymbol: displaySymbol, action: 'SELL', ltp: strikeData.ltpPE, lotSize: strikeData.lotSize, underlying: symbol, security_id: strikeData.peToken, exchange_segment: resolveOptionSegment(symbol), bid: strikeData.bidPE, ask: strikeData.askPE, strike: strikeData.strike, optionType: 'PE', depth: strikeData.depthPE, expiry }]); }} disabled={strikeData.ltpPE <= 0} className="trade-btn sell" title="Sell PE">S</button>
              </div>
              <div className="flex items-center gap-1">
                <LegActionMenu strikeData={strikeData} optionType="PE" />
                <span className="font-semibold" style={{ color: strikeData.isATM ? '#000' : 'var(--text)' }}>{strikeData.ltpPE > 0 ? strikeData.ltpPE.toFixed(2) : '0.00'}</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {bidAskModal && (
        <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-40">
          <div className="rounded-lg shadow-lg p-4 w-72" style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text)' }}>
            <div className="text-sm font-semibold mb-3" style={{ color: 'var(--text)' }}>{bidAskModal.symbol} {bidAskModal.strike} {bidAskModal.optionType}</div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>Bid</span><span className="font-semibold text-green-500">{bidAskModal.bid.toFixed(2)}</span></div>
              <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>Ask</span><span className="font-semibold text-red-500">{bidAskModal.ask.toFixed(2)}</span></div>
            </div>
            <button type="button" onClick={() => setBidAskModal(null)} className="mt-4 w-full px-3 py-2 text-sm rounded" style={{ background: 'var(--surface2)', color: 'var(--text)', border: '1px solid var(--border)' }}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
};

export default Options;
