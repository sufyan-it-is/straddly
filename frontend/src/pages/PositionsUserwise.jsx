import React, { useState, useEffect, useCallback, useRef } from "react";
import { apiService } from '../services/apiService';
import { useMarketPulse } from '../hooks/useMarketPulse';

// ── helpers ───────────────────────────────────────────────────────────────
const INR = (n) =>
  (Number(n) < 0 ? "-₹" : "₹") +
  Math.abs(Number(n)).toLocaleString("en-IN", { maximumFractionDigits: 2 });

const PCT = (n) => (Number(n) >= 0 ? "" : "") + Number(n).toFixed(2) + "%";

const numColor = (n) =>
  Number(n) > 0 ? "var(--positive-text)" : Number(n) < 0 ? "var(--negative-text)" : "var(--text)";

// ── styles ────────────────────────────────────────────────────────────────
const TH = {
  padding: "9px 11px",
  background: "var(--surface2)",
  borderBottom: "1px solid var(--border)",
  fontWeight: 700,
  fontSize: "10px",
  color: "var(--muted)",
  textTransform: "uppercase",
  whiteSpace: "nowrap",
  cursor: "pointer",
  userSelect: "none",
};
const TD = {
  padding: "9px 11px",
  borderBottom: "1px solid var(--border)",
  fontSize: "12px",
  color: "var(--text)",
  whiteSpace: "nowrap",
};
const SUB_TH = {
  padding: "7px 10px",
  background: "var(--surface2)",
  borderBottom: "1px solid var(--border)",
  fontWeight: 600,
  fontSize: "10px",
  color: "var(--muted)",
  textTransform: "uppercase",
  whiteSpace: "nowrap",
};
const SUB_TD = {
  padding: "7px 10px",
  borderBottom: "1px solid var(--border)",
  fontSize: "12px",
  color: "var(--text)",
  whiteSpace: "nowrap",
};

// ── sort helpers ──────────────────────────────────────────────────────────
const SORT_FIELDS = {
  "UserId(asc)":   { key: "user_no",          dir: 1  },
  "UserId(desc)":  { key: "user_no",          dir: -1 },
  "Name(asc)":     { key: "display_name",     dir: 1  },
  "Name(desc)":    { key: "display_name",     dir: -1 },
  "P&L(asc)":      { key: "profit",           dir: 1  },
  "P&L(desc)":     { key: "profit",           dir: -1 },
  "WalletBal(asc)": { key: "wallet_balance",  dir: 1  },
  "WalletBal(desc)": { key: "wallet_balance", dir: -1 },
};

function sortRows(rows, sortLabel) {
  const cfg = SORT_FIELDS[sortLabel] || SORT_FIELDS["UserId(asc)"];
  return [...rows].sort((a, b) => {
    const av = a[cfg.key] ?? 0;
    const bv = b[cfg.key] ?? 0;
    return cfg.dir * (Number(av) - Number(bv));
  });
}

// ── Position sub-table for one user ───────────────────────────────────────
function UserPositions({ row, onExitDone, liveTickByToken = {} }) {
  const [checked,  setChecked]  = useState({});  // instrument_token -> bool
  const [exitQty,  setExitQty]  = useState({});  // instrument_token -> qty string
  const [exiting,  setExiting]  = useState(false);

  // Show all positions returned by backend (OPEN + intraday CLOSED)
  const positions = (row.positions || []);
  const openPositions = positions.filter(p => p.status === "OPEN");
  const rowKey = (p) => p.position_id || p.instrument_token;
  const allOpen       = openPositions.map(rowKey);
  const anyChecked    = allOpen.some(t => checked[t]);

  const toggleAll = (e) => {
    const val = e.target.checked;
    const next = {};
    allOpen.forEach(t => { next[t] = val; });
    setChecked(next);
  };

  const toggle = (token) =>
    setChecked(prev => ({ ...prev, [token]: !prev[token] }));

  const handleExitSelected = async () => {
    const targets = allOpen.filter(t => checked[t]);
    if (!targets.length) return;
    setExiting(true);
    try {
      const selectedRows = openPositions.filter((p) => targets.includes(rowKey(p)));
      await Promise.all(selectedRows.map((p) => {
        const token = p.instrument_token;
        const rawQty = Number(exitQty[token] ?? Math.abs(p.quantity));
        const qty = Math.max(1, Math.min(Math.abs(Number(p.quantity || 0)), Math.floor(rawQty || 0)));
        const payload = {
          user_id: String(row.user_id || ''),
          symbol: p.symbol,
          security_id: Number(token || 0) || undefined,
          instrument_token: Number(token || 0) || undefined,
          exchange_segment: p.exchange_segment || p.exchange || 'NSE_EQ',
          transaction_type: Number(p.quantity || 0) >= 0 ? 'SELL' : 'BUY',
          quantity: qty,
          order_type: 'MARKET',
          product_type: String(p.product_type || 'MIS').toUpperCase(),
        };
        return apiService.post('/trading/orders', payload);
      }));
      setChecked({});
      if (onExitDone) onExitDone();
    } catch (err) {
      alert(err?.data?.detail || err?.message || "Failed to exit position(s).");
    } finally {
      setExiting(false);
    }
  };

  return (
    <div style={{ padding: "14px 18px", background: "var(--surface)" }}>
      {/* Panel header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "10px" }}>
        <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--text)" }}>
          Positions for{" "}
          <span style={{ color: "#1d4ed8" }}>{row.display_name}</span>
          {" "}(User ID: {row.user_no || row.user_id?.slice(0, 8)})
        </span>
        <button
          disabled={!anyChecked || exiting}
          onClick={handleExitSelected}
          style={{
            padding: "7px 18px",
            borderRadius: "6px",
            border: "none",
            background: anyChecked ? "#dc2626" : "#3f3f46",
            color: "#fff",
            fontWeight: 700,
            fontSize: "12px",
            cursor: anyChecked ? "pointer" : "not-allowed",
            opacity: exiting ? 0.6 : 1,
          }}
        >
          {exiting ? "Exiting…" : "EXIT Selected"}
        </button>
      </div>

      {/* Sub-table */}
      <div style={{ overflowX: "auto", borderRadius: "6px", border: "1px solid #3f3f46" }}>
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={SUB_TH}>
                <input
                  type="checkbox"
                  onChange={toggleAll}
                  checked={allOpen.length > 0 && allOpen.every(t => checked[t])}
                  style={{ accentColor: "#2563eb" }}
                />
              </th>
              {["Symbol","Exchange","Product","Quantity","Avg Price","LTP","P&L","Exit Qty","Type"].map(h => (
                <th key={h} style={SUB_TH}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {positions.length === 0 ? (
              <tr>
                <td colSpan={10} style={{ ...SUB_TD, textAlign: "center", color: "#a1a1aa", padding: "20px" }}>
                  No positions today.
                </td>
              </tr>
            ) : positions.map(p => {
              const isOpen  = p.status === "OPEN";
              const token   = p.instrument_token;
              const key     = rowKey(p);
              const curQty  = exitQty[token] ?? String(Math.abs(p.quantity));
              // Use live tick if available, fallback to position's ltp
              const liveTick = liveTickByToken[token];
              const currentLTP = liveTick?.ltp !== undefined && liveTick?.ltp !== null ? liveTick.ltp : p.ltp;
              const currentPnL = isOpen ? (currentLTP - p.avg_price) * p.quantity : p.pnl;
              return (
                <tr key={String(key)} style={{ background: checked[key] ? "#1e3a5f22" : "transparent" }}>
                  <td style={SUB_TD}>
                    {isOpen ? (
                      <input
                        type="checkbox"
                        checked={!!checked[key]}
                        onChange={() => toggle(key)}
                        style={{ accentColor: "#2563eb" }}
                      />
                    ) : null}
                  </td>
                  <td style={{ ...SUB_TD, fontWeight: 700 }}>{p.symbol || "—"}</td>
                  <td style={{ ...SUB_TD, color: "#a1a1aa" }}>{p.exchange || "—"}</td>
                  <td style={SUB_TD}>
                    <span style={{
                      padding: "2px 8px", borderRadius: "999px", fontSize: "10px",
                      fontWeight: 700, color: "#fff",
                      background: p.product_type === "NORMAL" ? "#1d4ed8" : "#7c3aed",
                    }}>
                      {p.product_type || "MIS"}
                    </span>
                  </td>
                  <td style={{ ...SUB_TD, fontVariantNumeric: "tabular-nums" }}>{p.quantity}</td>
                  <td style={{ ...SUB_TD, fontVariantNumeric: "tabular-nums" }}>{INR(p.avg_price)}</td>
                  <td style={{ 
                    ...SUB_TD, 
                    fontVariantNumeric: "tabular-nums",
                    color: liveTick ? "#fbbf24" : "var(--text)",
                    fontWeight: liveTick ? 600 : 400
                  }}>
                    {INR(currentLTP)}
                    {liveTick && <span style={{ fontSize: "9px", color: "#60a5fa", marginLeft: "4px" }}>●</span>}
                  </td>
                  <td style={{ ...SUB_TD, fontVariantNumeric: "tabular-nums", color: numColor(currentPnL), backgroundColor: "var(--surface)" }}>
                    {INR(currentPnL)}
                  </td>
                  <td style={SUB_TD}>
                    {isOpen ? (
                      <input
                        type="number"
                        min={1}
                        max={Math.abs(p.quantity)}
                        value={curQty}
                        onChange={e => setExitQty(prev => ({ ...prev, [token]: e.target.value }))}
                        style={{
                          width: "72px", padding: "4px 6px",
                          background: "var(--control-bg)", border: "1px solid var(--border)",
                          borderRadius: "4px", color: "var(--text)", fontSize: "12px",
                        }}
                      />
                    ) : (
                      <span style={{ color: "var(--muted)" }}>—</span>
                    )}
                  </td>
                  <td style={SUB_TD}>
                    <span style={{
                      padding: "2px 8px", borderRadius: "999px", fontSize: "10px", fontWeight: 700,
                      color:       isOpen ? "var(--positive-text)" : "var(--muted)",
                      background:  "var(--surface2)",
                      border: `1px solid ${isOpen ? "var(--positive-text)" : "var(--border)"}`,
                    }}>
                      {p.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────────────
const PositionsUserwise = () => {
  const [rows,       setRows]       = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [sortLabel,  setSortLabel]  = useState("UserId(asc)");
  const [expandedId, setExpandedId] = useState(null); // user_id or null
  const [expandedOrdersUserId, setExpandedOrdersUserId] = useState(null);
  const [userActiveOrders, setUserActiveOrders] = useState({});
  const [ordersLoadingByUser, setOrdersLoadingByUser] = useState({});
  const [liveTickByToken, setLiveTickByToken] = useState({}); // token -> tick data
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);
  
  // Market pulse hook for market status and prices
  const { pulse, readyState: pricesWSState, marketActive } = useMarketPulse();

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  
  // WebSocket connection for live ticks
  const wsRef = useRef(null);
  const mountedRef = useRef(true);
  const reconnectAttemptsRef = useRef(0);
  const maxReconnectAttemptsRef = useRef(5);

  const buildWSUrl = useCallback(() => {
    try {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      return `${protocol}//${host}/api/v2/ws/feed`;
    } catch {
      return null;
    }
  }, []);

  const connectWebSocket = useCallback(() => {
    if (!mountedRef.current) return;
    const url = buildWSUrl();
    if (!url) return;

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        reconnectAttemptsRef.current = 0;
        
        // Subscribe to all instrument tokens from all positions
        const allTokens = [];
        rows.forEach(row => {
          (row.positions || []).forEach(p => {
            if (p.instrument_token && !allTokens.includes(p.instrument_token)) {
              allTokens.push(p.instrument_token);
            }
          });
        });
        
        if (allTokens.length > 0) {
          ws.send(JSON.stringify({
            action: 'subscribe',
            tokens: allTokens.map(t => Number(t))
          }));
        }
      };

      ws.onmessage = (evt) => {
        if (!mountedRef.current) return;
        try {
          const msg = JSON.parse(evt.data);
          
          if (msg.type === 'snapshot' && Array.isArray(msg.data)) {
            // Initial snapshot of subscribed tokens
            const tickMap = {};
            msg.data.forEach(tick => {
              if (tick?.instrument_token) {
                tickMap[tick.instrument_token] = tick;
              }
            });
            setLiveTickByToken(prev => ({ ...prev, ...tickMap }));
          } else if (msg.type === 'tick' && msg.data?.instrument_token) {
            // Single tick update
            setLiveTickByToken(prev => ({
              ...prev,
              [msg.data.instrument_token]: msg.data
            }));
          }
        } catch (e) {
          console.warn('[PositionsUserwise WS] Parse error:', e);
        }
      };

      ws.onerror = () => { /* handled by onclose */ };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        
        if (reconnectAttemptsRef.current < maxReconnectAttemptsRef.current) {
          const delay = 1000 * Math.pow(2, reconnectAttemptsRef.current);
          reconnectAttemptsRef.current += 1;
          setTimeout(() => connectWebSocket(), delay);
        }
      };
    } catch (e) {
      console.warn('[PositionsUserwise WS] Connection error:', e);
    }
  }, [rows, buildWSUrl]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.get('/admin/positions/userwise');
      const data = res?.data?.data || res?.data || [];
      setRows(data);
    } catch (err) {
      console.error('Failed to load positions:', err);
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Re-subscribe when rows change (new positions loaded)
  useEffect(() => {
    if (rows.length > 0 && wsRef.current?.readyState === WebSocket.OPEN) {
      const allTokens = [];
      rows.forEach(row => {
        (row.positions || []).forEach(p => {
          if (p.instrument_token && !allTokens.includes(p.instrument_token)) {
            allTokens.push(p.instrument_token);
          }
        });
      });
      
      if (allTokens.length > 0) {
        wsRef.current.send(JSON.stringify({
          action: 'subscribe',
          tokens: allTokens.map(t => Number(t))
        }));
      }
    }
  }, [rows]);

  // Connect to WebSocket on mount
  useEffect(() => {
    mountedRef.current = true;
    connectWebSocket();
    
    return () => {
      mountedRef.current = false;
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connectWebSocket]);

  const sorted      = sortRows(rows, sortLabel);
  const toggleExpand = (uid) => setExpandedId(prev => prev === uid ? null : uid);

  const toggleActiveOrders = useCallback(async (uid) => {
    if (expandedOrdersUserId === uid) {
      setExpandedOrdersUserId(null);
      return;
    }
    setExpandedOrdersUserId(uid);
    if (userActiveOrders[uid]) return;

    setOrdersLoadingByUser(prev => ({ ...prev, [uid]: true }));
    try {
      const res = await apiService.get(`/admin/positions/userwise/${uid}/active-orders`);
      const data = res?.data?.data || [];
      setUserActiveOrders(prev => ({ ...prev, [uid]: data }));
    } catch (err) {
      console.error('Failed to load active orders:', err);
      setUserActiveOrders(prev => ({ ...prev, [uid]: [] }));
    } finally {
      setOrdersLoadingByUser(prev => ({ ...prev, [uid]: false }));
    }
  }, [expandedOrdersUserId, userActiveOrders]);

  const SortTH = ({ children, field }) => (
    <th style={TH} title={`Sort by ${field}`}>
      {children}
    </th>
  );

  return (
    <div style={{ padding: isMobile ? "12px" : "24px", fontFamily: "system-ui,sans-serif", color: "var(--text)", minHeight: "100vh" }}>

      {/* Top bar */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px", flexWrap: "wrap", gap: "10px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <h1 style={{ fontSize: "20px", fontWeight: 700, margin: 0, color: "var(--text)" }}>
            All Positions Userwise
          </h1>
          {/* Market Status Indicator */}
          <div style={{
            padding: "6px 12px",
            borderRadius: "999px",
            fontSize: "12px",
            fontWeight: 600,
            color: marketActive ? "var(--positive-text)" : "var(--muted)",
            background: "var(--surface2)",
            border: `1px solid ${marketActive ? "var(--positive-text)" : "var(--border)"}`,
            display: "flex",
            alignItems: "center",
            gap: "6px"
          }}>
            <span style={{
              display: "inline-block",
              width: "8px",
              height: "8px",
              borderRadius: "50%",
              background: marketActive ? "var(--positive-text)" : "var(--muted)",
              animation: marketActive ? "pulse 2s infinite" : "none"
            }}></span>
            {marketActive ? "Markets Open" : "Markets Closed"}
          </div>
        </div>
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          {/* Refresh */}
          <button
            onClick={load}
            title="Refresh"
            style={{
              width: "36px", height: "36px", borderRadius: "50%", border: "none",
              background: "#2563eb", color: "#fff", fontSize: "16px", cursor: "pointer",
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            ↺
          </button>
          {/* Sort */}
          <select
            value={sortLabel}
            onChange={e => setSortLabel(e.target.value)}
            style={{
              padding: "7px 10px", background: "var(--surface2)", border: "1px solid var(--border)",
              borderRadius: "6px", color: "var(--text)", fontSize: "13px", cursor: "pointer",
            }}
          >
            {Object.keys(SORT_FIELDS).map(l => <option key={l}>{l}</option>)}
          </select>
        </div>
      </div>

      {/* Main table */}
      <div style={{ background: "var(--surface)", borderRadius: "10px", border: "1px solid var(--border)", overflowX: "auto" }}>
        <table style={{ width: "100%", minWidth: "980px", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={TH}>User ID</th>
              <th style={TH}>Name</th>
              <th style={TH}>Overall P&L (Till date)</th>
              <th style={TH}>Wallet Balance</th>
              <th style={TH}>Margin Allotted</th>
              <th style={TH}>Used Margin (Open + Pending)</th>
              <th style={TH}>P&L (Open Only)</th>
              <th style={TH}>P&L % (Open Only)</th>
              <th style={{ ...TH, cursor: "default" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={9} style={{ ...TD, textAlign: "center", color: "var(--text)", padding: "40px" }}>Loading…</td></tr>
            ) : sorted.length === 0 ? (
              <tr><td colSpan={9} style={{ ...TD, textAlign: "center", color: "var(--text)", padding: "40px" }}>No data.</td></tr>
            ) : sorted.map(r => {
              const isExpanded = expandedId === r.user_id;
              
              // Calculate open-only P&L and P&L%
              const openPositions = (r.positions || []).filter(p => p.status === "OPEN");
              let openOnlyPnL = 0;
              openPositions.forEach(p => {
                const token = p.instrument_token;
                const liveTick = liveTickByToken[token];
                const currentLTP = liveTick?.ltp !== undefined && liveTick?.ltp !== null ? liveTick.ltp : p.ltp;
                const mtm = (currentLTP - p.avg_price) * p.quantity;
                openOnlyPnL += mtm;
              });
              
              // Calculate P&L% based on wallet balance
              const openOnlyPnLPct = (r.wallet_balance && r.wallet_balance !== 0) 
                ? (openOnlyPnL / r.wallet_balance) * 100 
                : 0;
              
              return (
                <React.Fragment key={r.user_id}>
                  {/* Summary row */}
                  <tr className={isExpanded ? "pu-row pu-row-expanded" : "pu-row"}>
                    <td style={{ ...TD, fontWeight: 700, color: "#1d4ed8" }}>
                      {r.user_no || r.user_id?.slice(0, 8)}
                    </td>
                    <td style={{ ...TD, fontWeight: 600 }}>
                      {r.display_name || "—"}
                    </td>
                    <td style={{ ...TD, color: numColor(r.profit), fontVariantNumeric: "tabular-nums" }}>
                      {INR(r.profit)}
                    </td>
                    <td style={{ ...TD, color: numColor(r.wallet_balance), fontVariantNumeric: "tabular-nums" }}>
                      {INR(r.wallet_balance)}
                    </td>
                    <td style={{ ...TD, color: numColor(r.margin_allotted), fontVariantNumeric: "tabular-nums" }}>
                      {INR(r.margin_allotted)}
                    </td>
                    <td style={{ ...TD, fontVariantNumeric: "tabular-nums" }}>
                      {INR(r.current_margin_usage)}
                    </td>
                    <td style={{ ...TD, color: numColor(openOnlyPnL), fontVariantNumeric: "tabular-nums" }}>
                      {INR(openOnlyPnL)}
                    </td>
                    <td style={{ ...TD, color: numColor(openOnlyPnLPct), fontVariantNumeric: "tabular-nums" }}>
                      {PCT(openOnlyPnLPct)}
                    </td>
                    <td style={TD}>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <button
                          onClick={() => toggleActiveOrders(r.user_id)}
                          title={expandedOrdersUserId === r.user_id ? 'Hide active orders' : 'Show active orders'}
                          style={{
                            padding: '6px 10px',
                            borderRadius: '6px',
                            border: '1px solid var(--border)',
                            background: expandedOrdersUserId === r.user_id ? '#7c3aed' : 'var(--surface2)',
                            color: expandedOrdersUserId === r.user_id ? '#fff' : 'var(--text)',
                            fontSize: '11px',
                            fontWeight: 700,
                            cursor: 'pointer',
                          }}
                        >
                          Orders ({r.pending_orders_count || 0})
                        </button>
                        <button
                          onClick={() => toggleExpand(r.user_id)}
                          title={isExpanded ? "Hide positions" : "Show positions"}
                          style={{
                            width: "32px", height: "32px", borderRadius: "6px", border: "none",
                            background: isExpanded ? "var(--surface2)" : "transparent",
                            color: "#60a5fa", fontSize: "18px", cursor: "pointer",
                            display: "flex", alignItems: "center", justifyContent: "center",
                            transition: "transform 0.2s, background 0.2s",
                            transform: isExpanded ? "rotate(180deg)" : "rotate(0deg)",
                          }}
                        >
                          ⌄
                        </button>
                      </div>
                    </td>
                  </tr>

                  {/* Expanded positions row */}
                  {isExpanded && (
                    <tr>
                      <td
                        colSpan={9}
                        style={{ padding: 0, borderBottom: "2px solid #2563eb" }}
                      >
                        <UserPositions row={r} onExitDone={load} liveTickByToken={liveTickByToken} />
                      </td>
                    </tr>
                  )}

                  {expandedOrdersUserId === r.user_id && (
                    <tr>
                      <td colSpan={9} style={{ padding: 0, borderBottom: "2px solid #7c3aed" }}>
                        <div style={{ padding: "12px 18px", background: "var(--surface)" }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                            <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--text)" }}>
                              Active Orders for <span style={{ color: "#7c3aed" }}>{r.display_name}</span>
                            </span>
                            <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                              Pending Count: {r.pending_orders_count || 0}
                            </span>
                          </div>

                          <div style={{ overflowX: "auto", borderRadius: "6px", border: "1px solid var(--border)" }}>
                            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: "780px" }}>
                              <thead>
                                <tr>
                                  {['Time','Symbol','Side','Type','Product','Qty','Filled','Pending','Price','Status'].map(h => (
                                    <th key={h} style={SUB_TH}>{h}</th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {ordersLoadingByUser[r.user_id] ? (
                                  <tr><td colSpan={10} style={{ ...SUB_TD, textAlign: 'center', color: 'var(--muted)' }}>Loading active orders…</td></tr>
                                ) : (userActiveOrders[r.user_id] || []).length === 0 ? (
                                  <tr><td colSpan={10} style={{ ...SUB_TD, textAlign: 'center', color: 'var(--muted)' }}>No active orders.</td></tr>
                                ) : (
                                  (userActiveOrders[r.user_id] || []).map((o) => (
                                    <tr key={o.order_id}>
                                      <td style={SUB_TD}>{o.placed_at ? new Date(o.placed_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Kolkata' }) : '--'}</td>
                                      <td style={{ ...SUB_TD, fontWeight: 700 }}>{o.symbol}</td>
                                      <td style={SUB_TD}>{o.side}</td>
                                      <td style={SUB_TD}>{o.order_type}</td>
                                      <td style={SUB_TD}>{o.product_type}</td>
                                      <td style={SUB_TD}>{o.quantity}</td>
                                      <td style={SUB_TD}>{o.filled_qty}</td>
                                      <td style={SUB_TD}>{o.unfilled_qty}</td>
                                      <td style={SUB_TD}>{o.price !== null && o.price !== undefined ? INR(o.price) : '--'}</td>
                                      <td style={SUB_TD}>{o.status}</td>
                                    </tr>
                                  ))
                                )}
                              </tbody>
                            </table>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Footer note */}
      <div style={{ marginTop: "10px", fontSize: "11px", color: "#52525b" }}>
        Displays all users with their open positions. Click the arrow (⌄) to expand and view details of open and intraday closed positions. Live prices update with a ● indicator when markets are open.
      </div>

      {/* Pulse animation + row hover/expand theming */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        tr.pu-row:hover > td {
          background: var(--hover-bg, var(--surface2)) !important;
        }
        tr.pu-row-expanded > td {
          background: var(--row-selected-bg, var(--surface2)) !important;
        }
      `}</style>
    </div>
  );
};

export default PositionsUserwise;
