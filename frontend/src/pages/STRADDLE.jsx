import React, { useState, useEffect, useMemo, useRef, memo, useCallback } from 'react';
import { apiService } from '../services/apiService';
import { useAuthoritativeOptionChain } from '../hooks/useAuthoritativeOptionChain';
import normalizeUnderlying from '../utils/underlying';
import { getLotSize as getConfiguredLotSize } from '../config/tradingConfig';
import { formatOptionLabel } from '../utils/formatInstrumentLabel';

const resolveOptionSegment = (underlyingSymbol) => {
  const upper = String(underlyingSymbol || '').toUpperCase();
  if (upper === 'SENSEX' || upper === 'BANKEX') return 'BSE_FNO';
  return 'NSE_FNO';
};

const StraddleMatrix = ({ handleOpenOrderModal, selectedIndex = 'NIFTY 50', expiry = null }) => {
  const [centerStrike, setCenterStrike] = useState(null);
  const [underlyingPrice, setUnderlyingPrice] = useState(null);
  const [snapshotStrikes, setSnapshotStrikes] = useState([]);
  const listRef = useRef(null);
  const didInitialScroll = useRef(false);

  // Convert selectedIndex to symbol for API calls
  const symbol = normalizeUnderlying(selectedIndex);

  // ✨ Use the authoritative hook to fetch realtime cached data
  const {
    data: chainData,
    loading: chainLoading,
    error: chainError,
    refresh: refreshChain,
    recalibrate: recalibrateChain,
  } = useAuthoritativeOptionChain(symbol, expiry, {
    autoRefresh: true,
    refreshInterval: 1000, // 1 second real-time updates
  });

  // Ignore stale payloads from the previous symbol/expiry while the new request is in flight.
  const chainMatchesSelection =
    !!chainData &&
    chainData.underlying === symbol &&
    (expiry == null || chainData.expiry === expiry);
  const activeChainData = chainMatchesSelection ? chainData : null;

  // Keep displayed LTP aligned only with the authoritative /options/live payload.
  // Do not fall back to /market/underlying-ltp because it can be stale.
  useEffect(() => {
    const ltp = Number(activeChainData?.underlying_ltp || 0);
    if (ltp > 0) {
      setUnderlyingPrice(ltp);
      return;
    }

    setUnderlyingPrice(null);
  }, [activeChainData?.underlying_ltp]);

  // ATM RULE: backend-provided ATM (straddle-min CE+PE) is authoritative.
  const straddleAtmStrike = useMemo(() => {
    const backendAtm = activeChainData?.atm_strike || activeChainData?.atm || null;
    return (typeof backendAtm === 'number' && backendAtm > 0) ? backendAtm : null;
  }, [activeChainData?.atm_strike, activeChainData?.atm]);

  // Reset center when context changes so a new symbol/expiry gets correct initial anchoring.
  useEffect(() => {
    setCenterStrike(null);
    didInitialScroll.current = false;
  }, [symbol, expiry]);

  // Legacy snapshot endpoint is removed in v2 backend; keep fallback list empty.
  useEffect(() => {
    setSnapshotStrikes([]);
  }, [symbol, expiry]);

  // Convert authoritative chain data to straddle format
  const straddles = useMemo(() => {
    if (!activeChainData || !activeChainData.strikes) {
      if (snapshotStrikes.length) {
        const configuredLot = getConfiguredLotSize(symbol);
        const lotSize = activeChainData?.lot_size && activeChainData.lot_size > 0 ? activeChainData.lot_size : configuredLot;
        return snapshotStrikes
          .map((s) => {
            const strike = Number(s.strike);
            const ceLtp = Number(s.ltpCE || 0);
            const peLtp = Number(s.ltpPE || 0);
            const hasCe = ceLtp > 0;
            const hasPe = peLtp > 0;
            const isDisplayValid = hasCe || hasPe;
            const isTradeReady = hasCe && hasPe;
            return {
              strike,
              isATM: false,
              ce_ltp: ceLtp,
              pe_ltp: peLtp,
              straddle_premium: (ceLtp + peLtp).toFixed(2),
              lot_size: lotSize,
              ceSymbol: `${symbol} ${strike} CE`,
              peSymbol: `${symbol} ${strike} PE`,
              ceToken: null,
              peToken: null,
              exchange_segment: resolveOptionSegment(symbol),
              timestamp: new Date().toISOString(),
              price_source: 'snapshot',
              isValid: isDisplayValid,
              trade_ready: isTradeReady,
            };
          })
          .sort((a, b) => a.strike - b.strike);
      }
      return [];
    }

    const atmStrike = centerStrike ?? straddleAtmStrike;
    const highlightedAtmStrike = straddleAtmStrike ?? centerStrike;
    const configuredLot = getConfiguredLotSize(symbol);
    const lotSize = activeChainData?.lot_size && activeChainData.lot_size > 0 ? activeChainData.lot_size : configuredLot;
    const snapshotMap = Object.fromEntries(
      (snapshotStrikes || []).map((s) => [Number(s.strike), s])
    );

    return Object.entries(activeChainData.strikes)
      .map(([strikeStr, strikeData]) => {
        const strike = parseFloat(strikeStr);
        let ceLtp = Number(
          strikeData.CE?.ltp ??
          strikeData.CE?.close ??
          strikeData.CE?.last_price ??
          0
        );
        let peLtp = Number(
          strikeData.PE?.ltp ??
          strikeData.PE?.close ??
          strikeData.PE?.last_price ??
          0
        );
        if (ceLtp <= 0 && snapshotMap[strike]?.ltpCE) {
          ceLtp = Number(snapshotMap[strike].ltpCE);
        }
        if (peLtp <= 0 && snapshotMap[strike]?.ltpPE) {
          peLtp = Number(snapshotMap[strike].ltpPE);
        }
        const hasCe = ceLtp > 0;
        const hasPe = peLtp > 0;
        const isDisplayValid = hasCe || hasPe;
        const isTradeReady = hasCe && hasPe;

        return {
          strike,
          isATM: highlightedAtmStrike && strike === highlightedAtmStrike,
          ce_ltp: ceLtp,
          pe_ltp: peLtp,
          straddle_premium: (ceLtp + peLtp).toFixed(2),
          lot_size: lotSize,
          ceSymbol: `${symbol} ${strike} CE`,
          peSymbol: `${symbol} ${strike} PE`,
          ceToken: strikeData.CE?.instrument_token,
          peToken: strikeData.PE?.instrument_token,
          exchange_segment: resolveOptionSegment(symbol),
          timestamp: new Date().toISOString(),
          price_source: (strikeData.CE?.source || 'live_cache') + (snapshotMap[strike] ? '|snapshot_merge' : ''),
          isValid: isDisplayValid,
          trade_ready: isTradeReady,
        };
      })
      .sort((a, b) => a.strike - b.strike);
  }, [activeChainData, symbol, centerStrike, straddleAtmStrike, snapshotStrikes]);

  // Keep center stable and only move it when ATM drift is meaningful.
  // This avoids 1-tick ATM flip-flops causing visible list jitter.
  useEffect(() => {
    if (!straddles.length) return;

    const strikesSorted = straddles.map((s) => s.strike).sort((a, b) => a - b);
    const liveAtm = (typeof straddleAtmStrike === 'number' && straddleAtmStrike > 0) ? straddleAtmStrike : null;
    const intervalFromChain = Number(activeChainData?.strike_interval || 0);
    const inferredInterval = strikesSorted.length > 1
      ? Math.min(...strikesSorted.slice(1).map((v, i) => Math.abs(v - strikesSorted[i])).filter((n) => n > 0))
      : 0;
    const strikeInterval = intervalFromChain > 0 ? intervalFromChain : inferredInterval;

    const nearestStrike = (target) => {
      if (target == null) return strikesSorted[Math.floor(strikesSorted.length / 2)] || null;
      let nearest = strikesSorted[0];
      let minDiff = Math.abs(nearest - target);
      strikesSorted.forEach((v) => {
        const d = Math.abs(v - target);
        if (d < minDiff) {
          minDiff = d;
          nearest = v;
        }
      });
      return nearest;
    };

    if (centerStrike == null) {
      const initial = nearestStrike(liveAtm);
      if (initial != null) {
        setCenterStrike(initial);
        console.log(`📍 [STRADDLE] Center strike (ATM): ${initial}`);
      }
      return;
    }

    // Guard against stale lock: realign when center drifts materially from live ATM.
    if (liveAtm != null && strikeInterval > 0) {
      const drift = Math.abs(centerStrike - liveAtm);
      if (drift >= strikeInterval * 2) {
        const corrected = nearestStrike(liveAtm);
        if (corrected != null && corrected !== centerStrike) {
          setCenterStrike(corrected);
          console.log(`📍 [STRADDLE] Center realigned: ${centerStrike} -> ${corrected} (live ATM ${liveAtm})`);
        }
      }
    }
  }, [straddles, activeChainData?.strike_interval, straddleAtmStrike, centerStrike]);

  const displayedStraddles = useMemo(() => {
    if (!straddles.length) return [];
    const strikesSorted = straddles.map(s => s.strike).sort((a, b) => a - b);
    const atm = centerStrike ?? (straddleAtmStrike || null);
    if (atm == null) return straddles;
    let centerIdx = strikesSorted.findIndex(v => v === atm);
    if (centerIdx < 0) {
      let nearest = 0;
      let minDiff = Infinity;
      strikesSorted.forEach((v, i) => {
        const d = Math.abs(v - atm);
        if (d < minDiff) {
          minDiff = d;
          nearest = i;
        }
      });
      centerIdx = nearest;
    }
    const total = 31;
    let start = Math.max(0, centerIdx - 15);
    let end = start + total - 1;
    if (end > strikesSorted.length - 1) {
      end = strikesSorted.length - 1;
      start = Math.max(0, end - total + 1);
    }
    const allowed = new Set(strikesSorted.slice(start, end + 1));
    return straddles.filter(s => allowed.has(s.strike));
  }, [straddles, centerStrike, straddleAtmStrike]);

  useEffect(() => {
    if (didInitialScroll.current) return;
    const el = listRef.current;
    if (!el) return;
    const atmEl = el.querySelector('[data-atm="true"]');
    if (!atmEl) return;
    const elRect = el.getBoundingClientRect();
    const rowRect = atmEl.getBoundingClientRect();
    const delta = rowRect.top - elRect.top;
    const target = el.scrollTop + delta - (el.clientHeight / 2) + (atmEl.clientHeight / 2);
    el.scrollTo({ top: Math.max(target, 0), behavior: 'smooth' });
    didInitialScroll.current = true;
  }, [displayedStraddles]);

  const handleRefresh = useCallback(() => {
    refreshChain();
  }, [refreshChain]);

  const handleRecalibrate = useCallback(() => {
    setCenterStrike(null);
    didInitialScroll.current = false;
    recalibrateChain();
  }, [recalibrateChain]);

  return (
    <div className="flex flex-col h-full" style={{ background: 'var(--surface)', color: 'var(--text)' }}>
      {/* Header with center strike info */}
      <div className="p-3 flex justify-between items-center text-xs" style={{ background: 'var(--surface2)', borderBottom: '1px solid var(--border)' }}>
        <div className="flex items-center space-x-2">
          <span className="font-bold">{symbol} Straddles</span>
          {centerStrike && (
            <span className="text-indigo-600 font-semibold">
              ATM: {centerStrike}
            </span>
          )}
          {underlyingPrice && (
            <span className="text-green-600 font-bold">
              LTP: {underlyingPrice.toFixed(2)}
            </span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <button
            onClick={handleRecalibrate}
            disabled={chainLoading}
            className="px-2 py-0.5 text-xs font-semibold rounded border border-indigo-400 text-indigo-600 hover:bg-indigo-50 transition-colors disabled:opacity-50"
            style={{ willChange: 'transform' }}
            title="Re-centre strikes to current ATM"
          >
            Re-centre
          </button>
          <button
            onClick={handleRefresh}
            disabled={chainLoading}
            className="p-1 text-gray-500 hover:text-gray-700 hover:bg-gray-200 rounded transition-colors disabled:opacity-50"
            title="Refresh data"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>
      </div>

      {/* Loading state */}
      {chainLoading && !straddles.length && (
        <div className="flex items-center justify-center p-8">
          <div className="text-gray-500">
            <div className="animate-spin inline-block mr-2">⚙️</div>
            Loading straddle data...
          </div>
        </div>
      )}

      {/* Error state */}
      {chainError && !straddles.length && (
        <div className="flex items-center justify-center p-8">
          <div className="text-red-500 text-center">
            <div className="font-bold">Unable to Load</div>
            <div className="text-sm">{chainError}</div>
            <button
              onClick={handleRefresh}
              className="mt-2 px-3 py-1 bg-blue-500 text-white rounded text-sm hover:bg-blue-600"
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {/* Straddle data */}
      {displayedStraddles.length > 0 && (
        <div className="overflow-y-auto flex-grow" ref={listRef} style={{ maxHeight: '640px' }}>
          <div className="flex items-center px-2 py-1 text-[10px] sm:text-xs font-bold uppercase sticky top-0 z-10" style={{ background: 'var(--surface2)', color: 'var(--muted)', borderBottom: '1px solid var(--border)' }}>
            <div className="flex-1 text-left">Strike</div>
            <div className="flex-1 text-center">Trade</div>
            <div className="flex-1 text-right">Premium</div>
          </div>

          {displayedStraddles.map((straddle) => {
            const isValidStraddle = straddle.isValid;
            const isTradeReady = straddle.trade_ready;
            const displayValue = isValidStraddle ? parseFloat(straddle.straddle_premium).toFixed(2) : '0.00';

            return (
              <div
                key={straddle.strike}
                data-atm={straddle.isATM ? 'true' : 'false'}
                className={`flex items-center p-2 text-xs h-10 sm:h-10 ${!isValidStraddle ? 'opacity-50' : ''} ${straddle.isATM ? 'font-bold' : ''}`}
                style={{ borderBottom: '1px solid var(--border)', background: straddle.isATM ? 'oklch(90% 0.002 286)' : 'var(--surface)', color: straddle.isATM ? '#000' : 'var(--text)' }}
              >
                <div className="flex-1 text-left text-xs sm:text-xs pr-2">
                  <div className="font-semibold">
                    {straddle.strike}
                  </div>
                  <div style={{ color: '#a1a1aa' }} className="text-[10px]">
                    {' CE: ' + (straddle.ce_ltp > 0 ? straddle.ce_ltp.toFixed(2) : '0.00') +
                      ' | PE: ' + (straddle.pe_ltp > 0 ? straddle.pe_ltp.toFixed(2) : '0.00')}
                  </div>
                </div>

                <div className="flex-1 flex justify-center">
                  <button
                    onClick={() => {
                      if (!isTradeReady) return;
                      // Format display symbols with expiry date
                      const ceDisplaySymbol = formatOptionLabel({
                        instrumentType: 'OPTION',
                        symbol: straddle.ceSymbol,
                        expiryDate: expiry,
                        strikePrice: straddle.strike,
                        optionType: 'CE',
                        underlying: symbol,
                      });
                      const peDisplaySymbol = formatOptionLabel({
                        instrumentType: 'OPTION',
                        symbol: straddle.peSymbol,
                        expiryDate: expiry,
                        strikePrice: straddle.strike,
                        optionType: 'PE',
                        underlying: symbol,
                      });
                      handleOpenOrderModal([
                        {
                          symbol: straddle.ceSymbol,
                          displaySymbol: ceDisplaySymbol,
                          action: 'BUY',
                          ltp: straddle.ce_ltp,
                          lotSize: straddle.lot_size,
                          underlying: symbol,
                          security_id: straddle.ceToken,
                          exchange_segment: straddle.exchange_segment,
                          strike: straddle.strike,
                          optionType: 'CE',
                          expiry,
                        },
                        {
                          symbol: straddle.peSymbol,
                          displaySymbol: peDisplaySymbol,
                          action: 'BUY',
                          ltp: straddle.pe_ltp,
                          lotSize: straddle.lot_size,
                          underlying: symbol,
                          security_id: straddle.peToken,
                          exchange_segment: straddle.exchange_segment,
                          strike: straddle.strike,
                          optionType: 'PE',
                          expiry,
                        },
                      ]);
                    }}
                    disabled={!isTradeReady}
                    className="trade-btn buy"
                  >
                    BUY
                  </button>
                  <button
                    onClick={() => {
                      if (!isTradeReady) return;
                      // Format display symbols with expiry date
                      const ceDisplaySymbol = formatOptionLabel({
                        instrumentType: 'OPTION',
                        symbol: straddle.ceSymbol,
                        expiryDate: expiry,
                        strikePrice: straddle.strike,
                        optionType: 'CE',
                        underlying: symbol,
                      });
                      const peDisplaySymbol = formatOptionLabel({
                        instrumentType: 'OPTION',
                        symbol: straddle.peSymbol,
                        expiryDate: expiry,
                        strikePrice: straddle.strike,
                        optionType: 'PE',
                        underlying: symbol,
                      });
                      handleOpenOrderModal([
                        {
                          symbol: straddle.ceSymbol,
                          displaySymbol: ceDisplaySymbol,
                          action: 'SELL',
                          ltp: straddle.ce_ltp,
                          lotSize: straddle.lot_size,
                          underlying: symbol,
                          security_id: straddle.ceToken,
                          exchange_segment: straddle.exchange_segment,
                          strike: straddle.strike,
                          optionType: 'CE',
                          expiry,
                        },
                        {
                          symbol: straddle.peSymbol,
                          displaySymbol: peDisplaySymbol,
                          action: 'SELL',
                          ltp: straddle.pe_ltp,
                          lotSize: straddle.lot_size,
                          underlying: symbol,
                          security_id: straddle.peToken,
                          exchange_segment: straddle.exchange_segment,
                          strike: straddle.strike,
                          optionType: 'PE',
                          expiry,
                        },
                      ]);
                    }}
                    disabled={!isTradeReady}
                    className="trade-btn sell"
                  >
                    SELL
                  </button>
                </div>

                <div className="flex-1 text-right font-bold text-xs sm:text-xs pl-2">
                  <div>{displayValue}</div>
                  {!isValidStraddle && (
                    <div className="text-[10px] text-red-500">No data</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* No data state */}
      {!chainLoading && !chainError && straddles.length === 0 && (
        <div className="flex items-center justify-center p-8 text-gray-500">
          No straddle data available
        </div>
      )}
    </div>
  );
};

export default StraddleMatrix;
