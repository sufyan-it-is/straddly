import React, { useState, useEffect, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useAuth } from '../contexts/AuthContext';

// ── Helpers ───────────────────────────────────────────────────────────────
const today = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD

const daysAgo = (n) => {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toLocaleDateString("en-CA");
};

const INR = (n) => {
  const v = Number(n);
  return (v < 0 ? "-₹" : "₹") +
    Math.abs(v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
};

const numColor = (n) =>
  Number(n) > 0 ? "var(--positive-text)" : Number(n) < 0 ? "var(--negative-text)" : "var(--muted)";

const fmtDt = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) +
    " " + d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
};

const fmtDate = (iso) => {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
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

// ── Reusable styles ───────────────────────────────────────────────────────
const S = {
  input:  { padding: "7px 10px", background: "var(--control-bg)", border: "1px solid var(--border)", borderRadius: "6px", color: "var(--text)", fontSize: "13px" },
  select: { padding: "7px 10px", background: "var(--control-bg)", border: "1px solid var(--border)", borderRadius: "6px", color: "var(--text)", fontSize: "13px", cursor: "pointer" },
  th:     { padding: "9px 12px", background: "var(--surface2)", borderBottom: "1px solid var(--border)", fontWeight: 700, fontSize: "10px", color: "var(--muted)", textTransform: "uppercase", whiteSpace: "nowrap", textAlign: "left" },
  td:     { padding: "9px 12px", borderBottom: "1px solid var(--border)", fontSize: "12px", color: "var(--text)", whiteSpace: "nowrap" },
};

// ── Summary card ──────────────────────────────────────────────────────────
function SummaryCard({ label, value, colored = true, sub = null }) {
  const v = Number(value);
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "10px", padding: "18px 20px", minWidth: "160px", flex: "1 1 220px" }}>
      <div style={{ fontSize: "11px", color: "var(--muted)", fontWeight: 600, textTransform: "uppercase", marginBottom: "8px" }}>{label}</div>
      <div style={{ fontSize: "22px", fontWeight: 800, color: colored ? numColor(v) : "var(--text)" }}>
        {typeof value === "number" ? INR(v) : value}
      </div>
      {sub && <div style={{ fontSize: "11px", color: "#52525b", marginTop: "4px" }}>{sub}</div>}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────
const PandLPage = ({ hideUserSelect = false }) => {
  const { user } = useAuth();
  const isAdmin = user?.role === "ADMIN" || user?.role === "SUPER_ADMIN" || user?.role === "SUPER_USER";
  const canSaveCsv = user?.role === "ADMIN" || user?.role === "SUPER_ADMIN";
  const showAdminCostTotals = isAdmin && !hideUserSelect;
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);

  const [fromDate,  setFromDate]  = useState(daysAgo(90)); // default: last 90 days
  const [toDate,    setToDate]    = useState(today());
  const [selectedUserIds, setSelectedUserIds] = useState([]);
  const [userSelectOpen, setUserSelectOpen] = useState(false);
  const [userList,  setUserList]  = useState([]);
  const [data,      setData]      = useState(null);
  const [loading,   setLoading]   = useState(false);
  const [error,     setError]     = useState("");

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Load user list for admin dropdown
  useEffect(() => {
    if (!isAdmin || hideUserSelect) return;
    apiService.get("/admin/users").then(res => {
      setUserList(res?.data?.data || res?.data || []);
    }).catch(() => {});
  }, [isAdmin, hideUserSelect]);

  const userOptions = userList
    .map((u) => {
      const id = String(u?.id ?? u?.user_id ?? "").trim();
      if (!id) return null;
      const fullName = `${u?.first_name || u?.name || u?.mobile || id} ${u?.last_name || ""}`.trim();
      return { id, label: `${fullName}${u?.mobile ? ` (${u.mobile})` : ""}` };
    })
    .filter(Boolean);

  const allUsersSelected = userOptions.length > 0 && selectedUserIds.length === userOptions.length;

  const toggleUserSelection = (uid) => {
    setSelectedUserIds((prev) => (prev.includes(uid) ? prev.filter((id) => id !== uid) : [...prev, uid]));
  };

  const selectAllUsers = () => {
    setSelectedUserIds(userOptions.map((u) => u.id));
  };

  const clearAllUsers = () => {
    setSelectedUserIds([]);
  };

  // Auto-fetch on mount with today's range
  const fetchPnl = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const baseParams = { from_date: fromDate, to_date: toDate };

      if (isAdmin && !hideUserSelect && selectedUserIds.length > 0) {
        const allResponses = await Promise.all(
          selectedUserIds.map((uid) => apiService.get("/portfolio/positions/pnl/historic", { ...baseParams, user_id: uid }))
        );

        const merged = allResponses.map((res) => res?.data?.data || res?.data || {}).reduce(
          (acc, item) => {
            const closedRows = Array.isArray(item?.closed) ? item.closed : [];
            const realized = Number(item?.realized_pnl || 0);
            const net = Number(item?.net_realized_pnl ?? item?.net_pnl ?? 0);
            return {
              realized_pnl: acc.realized_pnl + realized,
              net_realized_pnl: acc.net_realized_pnl + net,
              net_pnl: acc.net_pnl + net,
              closed_count: acc.closed_count + closedRows.length,
              closed: acc.closed.concat(closedRows),
            };
          },
          { realized_pnl: 0, net_realized_pnl: 0, net_pnl: 0, closed_count: 0, closed: [] }
        );

        setData({
          ...merged,
          from_date: fromDate,
          to_date: toDate,
        });
      } else {
        const res = await apiService.get("/portfolio/positions/pnl/historic", baseParams);
        setData(res?.data?.data || res?.data || null);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || err?.data?.detail || err?.message || "Failed to load P&L data.");
      setData(null);
    } finally { setLoading(false); }
  }, [fromDate, toDate, isAdmin, hideUserSelect, selectedUserIds]);

  const handleSaveAsCsv = () => {
    const closed = Array.isArray(data?.closed) ? data.closed : [];
    const rows = closed.map((p) => {
      const tradeCharges = p.trade_expense != null
        ? Number(p.trade_expense)
        : (Number(p.total_charges || 0) - Number(p.platform_cost || 0));
      const platformCost = Number(p.platform_cost || 0);
      const netPnl = p.net_pnl != null ? Number(p.net_pnl) : (Number(p.realized_pnl || 0) - tradeCharges - platformCost);

      return [
        p.closed_at || p.report_date || "",
        p.symbol || "",
        Number(p.buy_qty || 0),
        Number(p.buy_price || 0),
        Number(p.buy_value || 0),
        Number(p.sell_qty || 0),
        Number(p.sell_price || 0),
        Number(p.sell_value || 0),
        platformCost,
        tradeCharges,
        netPnl,
      ];
    });

    downloadCsv(
      `pnl_${fromDate}_to_${toDate}.csv`,
      ["Date", "Symbol", "Buy Qty", "Buy Price", "Buy Value", "Sell Qty", "Sell Price", "Sell Value", "Platform Cost", "Trade Expense", "Net P&L"],
      rows,
    );
  };

  const closedRows = Array.isArray(data?.closed) ? data.closed : [];
  const tableTotals = closedRows.reduce(
    (sum, p) => {
      const tradeCharges = p.trade_expense != null
        ? Number(p.trade_expense)
        : (Number(p.total_charges || 0) - Number(p.platform_cost || 0));
      const platformCost = Number(p.platform_cost || 0);
      const netPnl = p.net_pnl != null ? Number(p.net_pnl) : (Number(p.realized_pnl || 0) - tradeCharges - platformCost);
      return {
        platformCost: sum.platformCost + platformCost,
        tradeExpense: sum.tradeExpense + tradeCharges,
        netPnl: sum.netPnl + netPnl,
      };
    },
    { platformCost: 0, tradeExpense: 0, netPnl: 0 }
  );

  useEffect(() => { fetchPnl(); }, []); // eslint-disable-line

  // ── Render ────────────────────────────────────────────────────────────
  return (
    <div style={{ padding: isMobile ? "12px" : "24px", fontFamily: "system-ui,sans-serif", color: "var(--text)", minHeight: "100vh" }}>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "20px", flexWrap: "wrap", gap: "12px" }}>
        <h1 style={{ fontSize: "20px", fontWeight: 700, margin: 0 }}>P&amp;L</h1>

        {/* Filter bar */}
        <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
          {isAdmin && !hideUserSelect && (
            <div style={{ position: "relative", minWidth: isMobile ? "100%" : "260px" }}>
              <button
                type="button"
                onClick={() => setUserSelectOpen((prev) => !prev)}
                style={{ ...S.select, width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center" }}
              >
                <span>
                  {selectedUserIds.length === 0
                    ? "My Account"
                    : `${selectedUserIds.length} user${selectedUserIds.length === 1 ? "" : "s"} selected`}
                </span>
                <span style={{ fontSize: "11px", color: "var(--muted)" }}>{userSelectOpen ? "▲" : "▼"}</span>
              </button>

              {userSelectOpen && (
                <div style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 20, width: "100%", maxHeight: "280px", overflowY: "auto", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "8px", boxShadow: "0 10px 25px rgba(0,0,0,0.2)", padding: "8px" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", padding: "6px" }}>
                    <input type="checkbox" checked={allUsersSelected} onChange={(e) => (e.target.checked ? selectAllUsers() : clearAllUsers())} />
                    Select All
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", padding: "6px", borderBottom: "1px solid var(--border)", marginBottom: "6px" }}>
                    <input type="checkbox" checked={selectedUserIds.length === 0} onChange={clearAllUsers} />
                    Select None (My Account)
                  </label>

                  {userOptions.map((u) => (
                    <label key={u.id} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", padding: "6px" }}>
                      <input
                        type="checkbox"
                        checked={selectedUserIds.includes(u.id)}
                        onChange={() => toggleUserSelection(u.id)}
                      />
                      <span>{u.label}</span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ fontSize: "12px", color: "var(--muted)" }}>From</span>
            <input
              type="date" value={fromDate}
              onChange={e => setFromDate(e.target.value)}
              style={S.input}
            />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
            <span style={{ fontSize: "12px", color: "var(--muted)" }}>To</span>
            <input
              type="date" value={toDate}
              onChange={e => setToDate(e.target.value)}
              style={S.input}
            />
          </div>
          <button
            onClick={fetchPnl}
            disabled={loading}
            style={{ padding: "8px 20px", borderRadius: "6px", border: "none", background: "#2563eb", color: "#fff", fontWeight: 700, fontSize: "13px", cursor: "pointer", opacity: loading ? 0.6 : 1 }}
          >
            {loading ? "Loading…" : "Apply"}
          </button>
          {canSaveCsv && (
            <button
              onClick={handleSaveAsCsv}
              style={{ padding: "8px 14px", borderRadius: "6px", border: "1px solid var(--border)", background: "var(--surface2)", color: "var(--text)", fontWeight: 700, fontSize: "12px", cursor: "pointer" }}
            >
              save as csv
            </button>
          )}
        </div>
      </div>

      {/* Error */}
      {error && (
        <div style={{ marginBottom: "16px", padding: "10px 14px", background: "#7f1d1d33", border: "1px solid #ef4444", borderRadius: "8px", color: "#fca5a5", fontSize: "13px" }}>
          {error}
        </div>
      )}

      {/* Summary cards */}
      {data && (
        <>
          <div style={{ display: "flex", gap: "14px", flexWrap: "wrap", marginBottom: "24px" }}>
            <SummaryCard label="Realized P&L"   value={data.realized_pnl}   sub={`${data.closed_count} closed position${data.closed_count !== 1 ? "s" : ""}`} />
            <SummaryCard label="Net Realized P&L" value={data.net_realized_pnl ?? data.net_pnl} />
            <SummaryCard
              label="Period"
              value={data.from_date === data.to_date ? data.from_date : `${data.from_date} → ${data.to_date}`}
              colored={false}
            />
          </div>

          {/* Closed positions table */}
          <Section title={`Closed Positions (${data.closed_count})`} defaultOpen={true}>
            {closedRows.length === 0 ? (
              <Empty msg="No closed positions in this date range." />
            ) : (
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse" }}>
                  <thead>
                    <tr>
                      {["Date","Symbol","Buy Qty","Buy Price","Buy Value","Sell Qty","Sell Price","Sell Value","Platform Cost","Trade Expense","Net P&L"].map(h => (
                        <th key={h} style={S.th}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {closedRows.map((p, i) => {
                      const tradeCharges = p.trade_expense != null 
                        ? Number(p.trade_expense) 
                        : (Number(p.total_charges || 0) - Number(p.platform_cost || 0));
                      const platformCost = Number(p.platform_cost || 0);
                      const netPnl = p.net_pnl != null ? Number(p.net_pnl) : (Number(p.realized_pnl || 0) - tradeCharges - platformCost);
                      return (
                        <tr key={i}>
                          <td style={{ ...S.td, color: "var(--muted)" }}>{fmtDate(p.closed_at || p.report_date)}</td>
                          <td style={{ ...S.td, fontWeight: 700 }}>{p.symbol || "—"}</td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>{Number(p.buy_qty || 0)}</td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>{INR(p.buy_price || 0)}</td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>{INR(p.buy_value || 0)}</td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>{Number(p.sell_qty || 0)}</td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>{INR(p.sell_price || 0)}</td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>{INR(p.sell_value || 0)}</td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums", color: "var(--muted)" }}>
                            {INR(platformCost)}
                          </td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums", color: "var(--muted)" }}>
                            {INR(tradeCharges)}
                          </td>
                          <td style={{ ...S.td, fontVariantNumeric: "tabular-nums", fontWeight: 700, color: numColor(netPnl) }}>
                            {INR(netPnl)}
                          </td>
                        </tr>
                      );
                    })}
                    {closedRows.length > 0 && (
                      <tr style={{ background: "var(--surface2)", fontWeight: 700 }}>
                        <td style={{ ...S.td, fontWeight: 700 }}>Total</td>
                        <td style={{ ...S.td }}></td>
                        <td style={{ ...S.td }}></td>
                        <td style={{ ...S.td }}></td>
                        <td style={{ ...S.td }}></td>
                        <td style={{ ...S.td }}></td>
                        <td style={{ ...S.td }}></td>
                        <td style={{ ...S.td }}></td>
                        <td style={{ ...S.td, fontVariantNumeric: "tabular-nums", color: "var(--muted)" }}>
                          {showAdminCostTotals ? INR(tableTotals.platformCost) : ""}
                        </td>
                        <td style={{ ...S.td, fontVariantNumeric: "tabular-nums", color: "var(--muted)" }}>
                          {showAdminCostTotals ? INR(tableTotals.tradeExpense) : ""}
                        </td>
                        <td style={{ ...S.td, fontVariantNumeric: "tabular-nums", color: numColor(tableTotals.netPnl) }}>
                          {INR(tableTotals.netPnl)}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </>
      )}

      {!data && !loading && !error && (
        <div style={{ color: "#52525b", textAlign: "center", marginTop: "60px", fontSize: "14px" }}>
          Select a date range and press Apply.
        </div>
      )}
    </div>
  );
};

// ── Collapsible section ───────────────────────────────────────────────────
function Section({ title, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "10px", marginBottom: "16px", overflow: "hidden" }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{ width: "100%", background: "none", border: "none", borderBottom: open ? "1px solid var(--border)" : "none", padding: "14px 18px", display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer", color: "var(--text)" }}
      >
        <span style={{ fontSize: "14px", fontWeight: 700 }}>{title}</span>
        <span style={{ fontSize: "14px", color: "var(--muted)", transform: open ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}>›</span>
      </button>
      {open && <div>{children}</div>}
    </div>
  );
}

function Empty({ msg }) {
  return (
    <div style={{ padding: "28px", textAlign: "center", color: "#52525b", fontSize: "13px" }}>{msg}</div>
  );
}

export default PandLPage;
