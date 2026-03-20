import React, { useState, useEffect, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useAuth } from '../contexts/AuthContext';

const today   = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD
const daysAgo = (n) => {
  const d = new Date(); d.setDate(d.getDate() - n);
  return d.toLocaleDateString("en-CA");
};
const INR = (v) => {
  const n = Number(v);
  return (n < 0 ? "-₹" : "₹") + Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const csvEscape = (value) => {
  if (value === null || value === undefined) return "";
  const str = String(value).replace(/"/g, '""');
  return /[",\n]/.test(str) ? `"${str}"` : str;
};

const downloadCsv = (filename, headers, rows) => {
  const csv = [
    headers.map(csvEscape).join(","),
    ...rows.map((row) => row.map(csvEscape).join(",")),
  ].join("\n");

  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

const LedgerPage = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === "ADMIN" || user?.role === "SUPER_ADMIN" || user?.role === "SUPER_USER";
  const canSaveCsv = user?.role === "ADMIN" || user?.role === "SUPER_ADMIN";
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);
  const [entries,  setEntries]  = useState([]);
  const [unrealizedPnl, setUnrealizedPnl] = useState(0);
  const [loading,  setLoading]  = useState(true);
  const [fromDate, setFromDate] = useState(daysAgo(30)); // default: last 30 days
  const [toDate,   setToDate]   = useState(today());
  const [targetUid, setTargetUid] = useState(""); // "" = self
  const [userList,  setUserList]  = useState([]);
  const [searchQ,   setSearchQ]   = useState("");
  const [error,     setError]     = useState("");

  // Load full user list for admin
  useEffect(() => {
    if (!isAdmin) return;
    apiService.get("/admin/users").then(res => {
      setUserList(res?.data?.data || res?.data || []);
    }).catch(() => {});
  }, [isAdmin]);

  const fetchLedger = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = { from_date: fromDate, to_date: toDate };
      if (isAdmin && targetUid) {
        params.user_id = targetUid;
      } else if (user?.id) {
        params.user_id = String(user.id);
      }
      const res = await apiService.get('/ledger', params);
      setEntries(res?.data || []);

      // Unrealised P&L must come from MTM of currently open positions.
      try {
        const pnlSummary = await apiService.get('/portfolio/positions/pnl/summary', {
          user_id: params.user_id,
        });
        setUnrealizedPnl(Number(pnlSummary?.unrealized_pnl || 0));
      } catch (pnlErr) {
        console.warn('Unable to fetch unrealized P&L summary:', pnlErr);
        setUnrealizedPnl(0);
      }
    } catch (err) {
      console.error('Error fetching ledger:', err);
      setError(err?.data?.detail || err?.message || "Failed to load ledger.");
      setEntries([]);
      setUnrealizedPnl(0);
    } finally {
      setLoading(false);
    }
  }, [fromDate, toDate, targetUid, isAdmin, user?.id]);

  useEffect(() => {
    fetchLedger();
    // eslint-disable-next-line
  }, [user]);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Filter user list by search query
  const filteredUsers = searchQ.trim()
    ? userList.filter(u => {
        const q = searchQ.toLowerCase();
        return (
          (u.first_name || u.name || "").toLowerCase().includes(q) ||
          (u.last_name  || "").toLowerCase().includes(q) ||
          (u.mobile     || "").includes(q)
        );
      })
    : userList;

  // Totals for summary bar
  const creditSum = entries.reduce((s, e) => s + (e.credit != null ? Number(e.credit) : 0), 0);
  const totalDebit = entries.reduce((s, e) => s + (e.debit != null ? Number(e.debit) : 0), 0);
  const syntheticOpeningBalance = entries.find((e) => {
    const desc = String(e.description || '').trim().toLowerCase();
    return desc === 'opening balance' && e.debit == null && e.credit == null;
  });
  const openingBalance = syntheticOpeningBalance?.balance != null ? Number(syntheticOpeningBalance.balance) : 0;
  const totalCredit = creditSum + openingBalance;
  const currentBalance = totalCredit - totalDebit;

  const handleSaveAsCsv = () => {
    const rows = entries.map((e) => [
      e.date || "",
      e.description || "",
      e.type === "trade_pnl" ? "TRADE P&L" : "WALLET",
      e.debit ?? "",
      e.credit ?? "",
      e.balance ?? "",
    ]);

    downloadCsv(
      `ledger_${fromDate}_to_${toDate}.csv`,
      ["Date & Time", "Description", "Type", "Debits", "Credits", "Wallet Balance"],
      rows,
    );
  };

  const s = {
    page:      { padding: isMobile ? '12px' : '24px', fontFamily: 'system-ui,sans-serif', color: 'var(--text)' },
    header:    { display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '16px', flexWrap: 'wrap', gap: '12px' },
    title:     { fontSize: '20px', fontWeight: 700, margin: 0, color: 'var(--text)' },
    filterBar: { display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' },
    input:     { padding: '7px 10px', background: 'var(--control-bg)', border: '1px solid var(--border)', borderRadius: '6px', color: 'var(--text)', fontSize: '13px' },
    label:     { fontSize: '12px', color: 'var(--muted)' },
    button:    { padding: '8px 20px', borderRadius: '6px', border: 'none', background: '#2563eb', color: '#fff', fontWeight: '700', fontSize: '13px', cursor: 'pointer', opacity: loading ? 0.6 : 1 },
    csvButton: { padding: '8px 14px', borderRadius: '6px', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', fontWeight: '700', fontSize: '12px', cursor: 'pointer' },
    card:      { background: 'var(--surface)', borderRadius: '8px', border: '1px solid var(--border)', padding: isMobile ? '12px' : '20px' },
    th:        { padding: '10px 14px', textAlign: 'left', background: 'var(--surface2)', borderBottom: '1px solid var(--border)', fontWeight: '600', color: 'var(--muted)', fontSize: '12px', whiteSpace: 'nowrap' },
    td:        { padding: '9px 14px', borderBottom: '1px solid var(--border)', fontSize: '12px', color: 'var(--text)', verticalAlign: 'middle', whiteSpace: 'nowrap' },
    summaryGrid: { display: 'grid', gridTemplateColumns: isMobile ? '1fr 1fr' : 'repeat(4, 1fr)', gap: '10px', marginBottom: '16px' },
    summaryCard: (color) => ({ background: 'var(--surface)', border: `1px solid var(--border)`, borderLeft: `4px solid ${color}`, borderRadius: '8px', padding: '12px 14px' }),
    summaryLabel: { fontSize: '10px', color: 'var(--muted)', fontWeight: 600, textTransform: 'uppercase', marginBottom: '4px' },
    summaryValue: (n) => ({ fontSize: '16px', fontWeight: 700, color: n > 0 ? 'var(--positive-text, #22c55e)' : n < 0 ? 'var(--negative-text, #ef4444)' : 'var(--text)' }),
  };

  return (
    <div style={s.page}>
      {/* ── Header ── */}
      <div style={s.header}>
        <h1 style={s.title}>Ledger</h1>
        <div style={s.filterBar}>
          {/* Admin: user search + select */}
          {isAdmin && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', minWidth: isMobile ? '100%' : '200px' }}>
              <input
                type="text"
                placeholder="Search user by name / mobile…"
                value={searchQ}
                onChange={e => setSearchQ(e.target.value)}
                style={{ ...s.input, fontSize: '12px' }}
              />
              <select
                value={targetUid}
                onChange={e => { setTargetUid(e.target.value); setSearchQ(""); }}
                style={{ ...s.input, cursor: 'pointer' }}
              >
                <option value="">— My Account —</option>
                {filteredUsers.map(u => (
                  <option key={u.id} value={u.id}>
                    {u.first_name || u.name || u.mobile} {u.last_name || ""} ({u.mobile})
                  </option>
                ))}
              </select>
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={s.label}>From</span>
            <input type="date" value={fromDate} onChange={e => setFromDate(e.target.value)} style={s.input} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={s.label}>To</span>
            <input type="date" value={toDate} onChange={e => setToDate(e.target.value)} style={s.input} />
          </div>
          <button onClick={fetchLedger} disabled={loading} style={s.button}>
            {loading ? "Loading…" : "Apply"}
          </button>
          {canSaveCsv && (
            <button onClick={handleSaveAsCsv} style={s.csvButton}>
              save as csv
            </button>
          )}
        </div>
      </div>

      {/* ── Error ── */}
      {error && (
        <div style={{ marginBottom: '12px', padding: '10px 14px', background: '#7f1d1d33', border: '1px solid #ef4444', borderRadius: '8px', color: '#fca5a5', fontSize: '13px' }}>
          {error}
        </div>
      )}

      {/* ── Summary bar ── */}
      {!loading && entries.length > 0 && (
        <div style={s.summaryGrid}>
          <div style={s.summaryCard('#22c55e')}>
            <div style={s.summaryLabel}>Total Credits</div>
            <div style={s.summaryValue(totalCredit)}>{INR(totalCredit)}</div>
          </div>
          <div style={s.summaryCard('#ef4444')}>
            <div style={s.summaryLabel}>Total Debits</div>
            <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--negative-text, #ef4444)' }}>{INR(totalDebit)}</div>
          </div>
          <div style={s.summaryCard(unrealizedPnl >= 0 ? '#22c55e' : '#ef4444')}>
            <div style={s.summaryLabel}>Unrealised P&amp;L</div>
            <div style={s.summaryValue(unrealizedPnl)}>{INR(unrealizedPnl)}</div>
          </div>
          <div style={s.summaryCard(currentBalance >= 0 ? '#3b82f6' : '#f97316')}>
            <div style={s.summaryLabel}>Current Wallet Balance</div>
            <div style={{ fontSize: '16px', fontWeight: 700, color: 'var(--text)' }}>{INR(currentBalance)}</div>
          </div>
        </div>
      )}

      {/* ── Table ── */}
      <div style={s.card}>
        {loading ? (
          <div style={{ padding: '30px', textAlign: 'center', color: 'var(--muted)', fontSize: '13px' }}>Loading…</div>
        ) : entries.length === 0 ? (
          <div style={{ color: '#a1a1aa', fontSize: '13px', padding: '20px 0' }}>
            No ledger entries found for the selected date range.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', minWidth: '860px', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  {['Date & Time', 'Description', 'Type', 'Debits', 'Credits', 'Wallet Balance'].map(h => (
                    <th key={h} style={s.th}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {entries.map((e, i) => {
                  const isTradeEntry = e.type === 'trade_pnl';

                  // Format date/time
                  const rawDate = e.date || "";
                  const datePart = rawDate.split('T')[0] || rawDate;
                  const timePart = rawDate.includes('T')
                    ? new Date(rawDate).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
                    : '';

                  return (
                    <tr key={i} style={{ background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)' }}>
                      {/* Date */}
                      <td style={s.td}>
                        <div style={{ fontWeight: 600 }}>{datePart}</div>
                        {timePart && <div style={{ fontSize: '10px', color: 'var(--muted)' }}>{timePart}</div>}
                      </td>

                      {/* Description */}
                      <td style={{ ...s.td, maxWidth: '280px', whiteSpace: 'normal', lineHeight: 1.4 }}>
                        {e.description}
                      </td>

                      {/* Type badge */}
                      <td style={s.td}>
                        <span style={{
                          padding: '2px 8px',
                          borderRadius: '4px',
                          fontSize: '10px',
                          fontWeight: '600',
                          background: isTradeEntry ? '#059669' : '#4b5563',
                          color: '#fff',
                          whiteSpace: 'nowrap',
                        }}>
                          {isTradeEntry ? 'TRADE P&L' : 'WALLET'}
                        </span>
                      </td>

                      {/* Debit */}
                      <td style={{ ...s.td, fontVariantNumeric: 'tabular-nums', color: e.debit != null ? '#ef4444' : 'var(--muted)', fontWeight: e.debit != null ? 600 : 400 }}>
                        {e.debit != null ? INR(e.debit) : '—'}
                      </td>

                      {/* Credit */}
                      <td style={{ ...s.td, fontVariantNumeric: 'tabular-nums', color: e.credit != null ? '#22c55e' : 'var(--muted)', fontWeight: e.credit != null ? 600 : 400 }}>
                        {e.credit != null ? INR(e.credit) : '—'}
                      </td>

                      {/* Wallet balance */}
                      <td style={{ ...s.td, fontVariantNumeric: 'tabular-nums', color: e.balance != null ? (Number(e.balance) >= 0 ? '#22c55e' : '#ef4444') : 'var(--muted)', fontWeight: e.balance != null ? 600 : 400 }}>
                        {e.balance != null ? INR(e.balance) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default LedgerPage;
