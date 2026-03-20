import React, { useState, useEffect, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useMarketPulse } from '../hooks/useMarketPulse';
import { useAuth } from '../contexts/AuthContext';

const Portfolio = () => {
  const { user } = useAuth();
  const { pulse, marketActive } = useMarketPulse();

  const [holdings, setHoldings] = useState([]);
  const [loading, setLoading] = useState(false);
  const [exitModalOpen, setExitModalOpen] = useState(false);
  const [exitRow, setExitRow] = useState(null);
  const [exitQty, setExitQty] = useState('');
  const [exitOrderType, setExitOrderType] = useState('MARKET');
  const [exitLimitPrice, setExitLimitPrice] = useState('');
  const [exitSubmitting, setExitSubmitting] = useState(false);
  const [exitError, setExitError] = useState('');
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const fetchHoldings = useCallback(async () => {
    setLoading(true);
    try {
      const params = {};
      const res = await apiService.get('/portfolio/positions/equity-holdings', params);
      const rows = Array.isArray(res?.data) ? res.data : [];
      setHoldings(rows.map((r, idx) => ({
        id: `${r.id || r.instrument_token || idx}`,
        instrumentToken: Number(r.instrument_token || 0),
        symbol: r.symbol || '—',
        exchangeSegment: r.exchange_segment || '',
        qty: Number(r.quantity || 0),
        avgPrice: Number(r.avg_price || 0),
        ltp: Number(r.ltp || r.avg_price || 0),
        mtm: Number(r.mtm || 0),
        investedValue: Number(r.invested_value || (r.quantity * r.avg_price) || 0),
        currentValue: Number(r.current_value || (r.quantity * (r.ltp || r.avg_price)) || 0),
        openedAt: r.opened_at || null,
        productType: r.product_type || 'NORMAL',
      })));
    } catch (err) {
      console.error('Portfolio fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchHoldings(); }, [fetchHoldings]);

  useEffect(() => {
    const handle = () => fetchHoldings();
    window.addEventListener('positions:updated', handle);
    return () => window.removeEventListener('positions:updated', handle);
  }, [fetchHoldings]);

  // Live tick — update LTP in-place from pulse prices, no API round-trip (avoids blink)
  useEffect(() => {
    if (!marketActive || !pulse?.prices) return;
    const prices = pulse.prices;
    setHoldings(prev => {
      let changed = false;
      const next = prev.map(h => {
        const newLtp = prices[h.symbol] ?? prices[String(h.instrumentToken)] ?? h.ltp;
        if (newLtp === h.ltp) return h;
        changed = true;
        const currentValue = h.qty * newLtp;
        const mtm = currentValue - h.investedValue;
        return { ...h, ltp: newLtp, currentValue, mtm };
      });
      return changed ? next : prev;
    });
  }, [pulse?.prices, marketActive]);

  // ── Totals ────────────────────────────────────────────────────────────────
  const totalInvested = holdings.reduce((s, h) => s + h.investedValue, 0);
  const totalCurrent  = holdings.reduce((s, h) => s + h.currentValue, 0);
  const totalPnl      = holdings.reduce((s, h) => s + h.mtm, 0);
  const pnlPct        = totalInvested > 0 ? (totalPnl / totalInvested) * 100 : 0;

  // ── Exit modal helpers ─────────────────────────────────────────────────────
  const openExitModal = (row) => {
    setExitRow(row);
    setExitQty(String(Math.max(1, row.qty)));
    setExitOrderType('MARKET');
    setExitLimitPrice('');
    setExitError('');
    setExitModalOpen(true);
  };
  const closeExitModal = () => {
    if (exitSubmitting) return;
    setExitModalOpen(false);
    setExitRow(null);
    setExitError('');
  };

  const submitExitOrder = async () => {
    if (!exitRow || exitSubmitting) return;
    const qtyNum = Number(exitQty);
    if (!Number.isInteger(qtyNum) || qtyNum <= 0 || qtyNum > exitRow.qty) {
      setExitError(`Qty must be between 1 and ${exitRow.qty}`);
      return;
    }
    if (exitOrderType === 'LIMIT' && (!exitLimitPrice || Number(exitLimitPrice) <= 0)) {
      setExitError('Valid limit price is required');
      return;
    }
    setExitSubmitting(true);
    setExitError('');
    try {
      const payload = {
        user_id: String(user?.id || ''),
        symbol: exitRow.symbol,
        security_id: exitRow.instrumentToken || undefined,
        instrument_token: exitRow.instrumentToken || undefined,
        exchange_segment: exitRow.exchangeSegment || 'NSE_EQ',
        transaction_type: 'SELL',
        quantity: qtyNum,
        order_type: exitOrderType,
        product_type: 'NORMAL',
      };
      if (exitOrderType === 'LIMIT') {
        payload.limit_price = Number(exitLimitPrice);
        payload.price = Number(exitLimitPrice);
      }
      await apiService.post('/trading/orders', payload);
      window.dispatchEvent(new CustomEvent('orders:updated'));
      window.dispatchEvent(new CustomEvent('positions:updated'));
      await new Promise(r => setTimeout(r, 600));
      await fetchHoldings();
      closeExitModal();
    } catch (err) {
      setExitError(err?.data?.detail || err?.response?.data?.detail || err?.message || 'Failed to place exit order');
    } finally {
      setExitSubmitting(false);
    }
  };

  // ── Square-off ALL ─────────────────────────────────────────────────────────
  const squareOffAll = async () => {
    if (!holdings.length) return;
    if (!window.confirm(`Square off all ${holdings.length} equity holding(s)? This will place MARKET SELL orders for each.`)) return;
    for (const row of holdings) {
      try {
        await apiService.post('/trading/orders', {
          user_id: String(user?.id || ''),
          symbol: row.symbol,
          security_id: row.instrumentToken || undefined,
          instrument_token: row.instrumentToken || undefined,
          exchange_segment: row.exchangeSegment || 'NSE_EQ',
          transaction_type: 'SELL',
          quantity: row.qty,
          order_type: 'MARKET',
          product_type: 'NORMAL',
        });
      } catch (err) {
        console.error(`Failed to close ${row.symbol}:`, err);
      }
    }
    window.dispatchEvent(new CustomEvent('orders:updated'));
    window.dispatchEvent(new CustomEvent('positions:updated'));
    await new Promise(r => setTimeout(r, 800));
    await fetchHoldings();
  };

  // ── Styles ─────────────────────────────────────────────────────────────────
  const page  = { minHeight: '100vh', padding: isMobile ? '12px' : '24px', fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", background: 'transparent' };
  const card  = { maxWidth: '1200px', margin: '0 auto', background: 'var(--surface)', borderRadius: '12px', boxShadow: '0 10px 30px rgba(0,0,0,0.3)', padding: isMobile ? '14px' : '24px', border: '1px solid var(--border)' };
  const titleRow = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px', marginBottom: '20px' };
  const titleStyle = { fontSize: '18px', fontWeight: 700, color: 'var(--text)', display: 'flex', alignItems: 'center', gap: '8px' };
  const summaryGrid = { display: 'grid', gridTemplateColumns: isMobile ? '1fr 1fr' : 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' };
  const summaryCard = (accent) => ({ background: 'var(--surface2)', border: `1px solid var(--border)`, borderRadius: '10px', padding: '14px 16px', borderLeft: `4px solid ${accent}` });
  const summaryLabel = { fontSize: '11px', color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' };
  const summaryValue = (positive) => ({ fontSize: '18px', fontWeight: 700, color: positive === null ? 'var(--text)' : (positive ? 'var(--positive-text, #22c55e)' : 'var(--negative-text, #ef4444)') });
  const tableOuter = { borderRadius: '8px', border: '1px solid var(--border)', overflowX: 'auto', background: 'var(--surface)' };
  const tableStyle = { width: '100%', minWidth: '760px', borderCollapse: 'collapse', fontSize: '12px' };
  const thStyle    = { padding: '10px 12px', textAlign: 'left', fontWeight: 600, color: 'var(--muted)', background: 'var(--surface2)', whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' };
  const thRight    = { ...thStyle, textAlign: 'right' };
  const tdStyle    = { padding: '10px 12px', color: 'var(--text)', verticalAlign: 'middle', whiteSpace: 'nowrap', borderBottom: '1px solid var(--border)' };
  const tdRight    = { ...tdStyle, textAlign: 'right' };
  const exitBtn    = { border: '1px solid var(--border)', borderRadius: '6px', padding: '4px 12px', fontSize: '12px', background: 'var(--surface2)', color: 'var(--text)', cursor: 'pointer' };
  const totalRow   = { background: 'var(--surface2)', fontWeight: 600 };
  const plColor    = (v) => ({ color: v >= 0 ? 'var(--positive-text, #22c55e)' : 'var(--negative-text, #ef4444)' });
  const btnDanger  = { padding: '6px 14px', borderRadius: '7px', border: 'none', background: '#dc2626', color: '#fff', fontSize: '12px', fontWeight: 600, cursor: 'pointer' };
  const btnRefresh = { background: 'none', border: '1px solid var(--border)', borderRadius: '5px', padding: '5px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' };
  const modalOverlay = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' };
  const modalCard = { width: '380px', maxWidth: '92vw', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '10px', boxShadow: '0 14px 32px rgba(0,0,0,0.35)', padding: '18px' };
  const inputStyle = { width: '100%', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', borderRadius: '6px', padding: '8px 10px', fontSize: '12px', boxSizing: 'border-box' };
  const labelStyle = { fontSize: '11px', color: 'var(--muted)', marginBottom: '4px', display: 'block' };
  const btnBase = { border: '1px solid var(--border)', borderRadius: '6px', padding: '8px 12px', fontSize: '12px', cursor: 'pointer' };

  const fmt  = (v) => '₹' + Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  const fmtS = (v) => (v >= 0 ? '+' : '-') + '₹' + Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 2 });

  return (
    <div style={page}>
      <div style={card}>

        {/* Header row */}
        <div style={titleRow}>
          <div style={titleStyle}>
            <span>📂</span>
            <span>Portfolio — Equity Holdings</span>
            <button onClick={fetchHoldings} style={btnRefresh} title="Refresh">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
            </button>
          </div>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <span style={{ fontSize: '11px', color: 'var(--muted)' }}>
              Overnight NORMAL equity positions
            </span>
            {holdings.length > 0 && (
              <button onClick={squareOffAll} style={btnDanger}>
                Square Off All
              </button>
            )}
          </div>
        </div>

        {/* Summary cards */}
        <div style={summaryGrid}>
          <div style={summaryCard('#3b82f6')}>
            <div style={summaryLabel}>Holdings</div>
            <div style={summaryValue(null)}>{holdings.length}</div>
          </div>
          <div style={summaryCard('#8b5cf6')}>
            <div style={summaryLabel}>Invested</div>
            <div style={summaryValue(null)}>{fmt(totalInvested)}</div>
          </div>
          <div style={summaryCard('#06b6d4')}>
            <div style={summaryLabel}>Current Value</div>
            <div style={summaryValue(null)}>{fmt(totalCurrent)}</div>
          </div>
          <div style={summaryCard(totalPnl >= 0 ? '#22c55e' : '#ef4444')}>
            <div style={summaryLabel}>Unrealised P&L</div>
            <div style={summaryValue(totalPnl >= 0)}>
              {fmtS(totalPnl)}
              <span style={{ fontSize: '12px', fontWeight: 400, marginLeft: '6px', color: 'var(--muted)' }}>
                ({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%)
              </span>
            </div>
          </div>
        </div>

        {/* Info banner */}
        <div style={{ background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.2)', borderRadius: '8px', padding: '10px 14px', marginBottom: '16px', fontSize: '12px', color: 'var(--muted)', lineHeight: '1.7' }}>
          <strong style={{ color: 'var(--text)' }}>How Portfolio works:</strong>
          {' '}NORMAL (delivery) equity positions show on the <em>Positions</em> tab on the day they are bought.
          From the next trading day onwards, they appear here exclusively. Use <em>Exit</em> to place a NORMAL SELL order during market hours.
        </div>

        {/* Holdings table */}
        {loading ? (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--muted)', fontSize: '13px' }}>Loading holdings…</div>
        ) : (
          <div style={tableOuter}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Symbol</th>
                  <th style={thStyle}>Exchange</th>
                  <th style={thRight}>Qty</th>
                  <th style={thRight}>Avg. Cost</th>
                  <th style={thRight}>LTP</th>
                  <th style={thRight}>Invested</th>
                  <th style={thRight}>Cur. Value</th>
                  <th style={thRight}>P&L</th>
                  <th style={thRight}>% Chg</th>
                  <th style={thStyle}>Held Since</th>
                  <th style={{ ...thStyle, textAlign: 'right' }}></th>
                </tr>
              </thead>
              <tbody>
                {holdings.length === 0 ? (
                  <tr>
                    <td colSpan={11} style={{ ...tdStyle, textAlign: 'center', padding: '40px', color: 'var(--muted)' }}>
                      No equity holdings. Buy equities with the NORMAL product type to build your portfolio.
                    </td>
                  </tr>
                ) : (
                  <>
                    {holdings.map((h) => {
                      const chgPct = h.avgPrice > 0 ? ((h.ltp - h.avgPrice) / h.avgPrice) * 100 : 0;
                      const heldSince = h.openedAt
                        ? new Date(h.openedAt).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: '2-digit' })
                        : '—';
                      return (
                        <tr key={h.id} style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
                          <td style={{ ...tdStyle, fontWeight: 600 }}>{h.symbol}</td>
                          <td style={{ ...tdStyle, fontSize: '11px', color: 'var(--muted)' }}>{h.exchangeSegment}</td>
                          <td style={{ ...tdRight, fontVariantNumeric: 'tabular-nums' }}>{h.qty.toLocaleString('en-IN')}</td>
                          <td style={tdRight}>₹{h.avgPrice.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
                          <td style={{ ...tdRight, fontWeight: 600 }}>₹{h.ltp.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</td>
                          <td style={tdRight}>{fmt(h.investedValue)}</td>
                          <td style={tdRight}>{fmt(h.currentValue)}</td>
                          <td style={{ ...tdRight, ...plColor(h.mtm) }}>{fmtS(h.mtm)}</td>
                          <td style={{ ...tdRight, ...plColor(chgPct) }}>{chgPct >= 0 ? '+' : ''}{chgPct.toFixed(2)}%</td>
                          <td style={{ ...tdStyle, fontSize: '11px', color: 'var(--muted)' }}>{heldSince}</td>
                          <td style={tdRight}>
                            <button style={exitBtn} onClick={() => openExitModal(h)}>Exit</button>
                          </td>
                        </tr>
                      );
                    })}
                    {/* Total row */}
                    <tr style={totalRow}>
                      <td style={{ ...tdStyle, fontWeight: 700 }} colSpan={5}>Total</td>
                      <td style={{ ...tdRight, fontWeight: 700 }}>{fmt(totalInvested)}</td>
                      <td style={{ ...tdRight, fontWeight: 700 }}>{fmt(totalCurrent)}</td>
                      <td style={{ ...tdRight, fontWeight: 700, ...plColor(totalPnl) }}>{fmtS(totalPnl)}</td>
                      <td style={{ ...tdRight, fontWeight: 700, ...plColor(pnlPct) }}>{pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%</td>
                      <td colSpan={2} style={tdStyle}></td>
                    </tr>
                  </>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Exit modal */}
      {exitModalOpen && exitRow && (
        <div style={modalOverlay} onClick={closeExitModal}>
          <div style={modalCard} onClick={(e) => e.stopPropagation()}>
            <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text)', marginBottom: '6px' }}>
              Exit — {exitRow.symbol}
            </div>
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginBottom: '14px' }}>
              Holding: <strong>{exitRow.qty}</strong> shares · Avg ₹{exitRow.avgPrice.toFixed(2)} · LTP ₹{exitRow.ltp.toFixed(2)}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '12px' }}>
              <div>
                <label style={labelStyle}>Quantity</label>
                <input
                  type="number"
                  min="1"
                  max={exitRow.qty}
                  value={exitQty}
                  onChange={(e) => setExitQty(e.target.value)}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Order Type</label>
                <select value={exitOrderType} onChange={(e) => setExitOrderType(e.target.value)} style={inputStyle}>
                  <option value="MARKET">MARKET</option>
                  <option value="LIMIT">LIMIT</option>
                </select>
              </div>
            </div>

            {exitOrderType === 'LIMIT' && (
              <div style={{ marginBottom: '12px' }}>
                <label style={labelStyle}>Limit Price</label>
                <input
                  type="number"
                  min="0"
                  step="0.05"
                  value={exitLimitPrice}
                  onChange={(e) => setExitLimitPrice(e.target.value)}
                  placeholder="0.00"
                  style={inputStyle}
                />
              </div>
            )}

            {/* NORMAL sell warning */}
            <div style={{ padding: '8px 10px', borderRadius: '6px', background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.2)', fontSize: '11px', color: 'var(--muted)', marginBottom: '12px' }}>
              This places a <strong>NORMAL SELL</strong> order. Market must be open.
            </div>

            {exitError && (
              <div style={{ marginBottom: '10px', fontSize: '11px', color: '#ef4444' }}>{exitError}</div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button onClick={closeExitModal} disabled={exitSubmitting} style={{ ...btnBase, background: 'var(--surface2)', color: 'var(--text)' }}>
                Cancel
              </button>
              <button
                onClick={submitExitOrder}
                disabled={exitSubmitting}
                style={{ ...btnBase, background: '#f97316', color: '#fff', borderColor: '#f97316' }}
              >
                {exitSubmitting ? 'Placing…' : 'Confirm Exit'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Portfolio;
