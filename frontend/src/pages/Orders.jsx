import React, { useState, useEffect, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useAuth } from '../contexts/AuthContext';

// ── helpers ────────────────────────────────────────────────────────────────────

const todayISO = () => new Date().toLocaleDateString("en-CA");

const fmtTime = (iso) => {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" });
};

const fmtDateTime = (iso) => {
  if (!iso) return "--";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--";
  return (
    d.toLocaleDateString("en-IN", { day: "2-digit", month: "2-digit", year: "numeric", timeZone: "Asia/Kolkata" }) +
    " " +
    d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Kolkata" })
  );
};

const mapOrder = (order) => {
  const createdAt  = order.placed_at || order.created_at || null;
  const updatedAt  = order.filled_at || order.executed_at || order.updated_at || createdAt;
  const qty        = Number(order.quantity ?? order.qty ?? 0);
  const filledQty  = Number(order.filled_qty ?? order.executedQty ?? 0);
  const rejectedQty= Number(order.rejected_qty ?? 0);
  const pendingQty = Math.max(0, qty - filledQty - rejectedQty);
  const execPrice  = Number(order.execution_price ?? order.avg_execution_price ?? order.fill_price ?? order.price ?? 0);
  const inputPrice = Number(order.price ?? execPrice ?? 0);
  const rawStatus  = String(order.status || "PENDING").toUpperCase();

  let displayStatus;
  if (rawStatus === "FILLED" || rawStatus === "EXECUTED") {
    displayStatus = "EXECUTED";
  } else if (rawStatus === "REJECTED" || rawStatus === "CANCELLED") {
    displayStatus = rawStatus;
  } else if (rawStatus === "PARTIAL_FILL" || rawStatus === "PARTIAL" || rawStatus === "PARTIALLY_FILLED") {
    displayStatus = "PARTIAL";
  } else if (rawStatus === "COMPLETE") {
    displayStatus = "EXECUTED";
  } else {
    displayStatus = "PENDING";
  }

  return {
    id:              order.id ?? order.order_id ?? String(Math.random()),
    orderId:         order.id ?? order.order_id ?? "--",
    uniqueId:        order.unique_id ?? order.uniqueId ?? "--",
    exchangeOrderId: order.exchange_order_id ?? order.exchangeOrderId ?? "--",
    createdAt,
    updatedAt,
    time:            fmtTime(createdAt),
    exTime:          fmtTime(updatedAt),
    orderDateTime:   fmtDateTime(createdAt),
    exchangeTime:    fmtDateTime(updatedAt),
    executionTime:   fmtDateTime(updatedAt),
    side:            String(order.transaction_type || order.side || "BUY").toUpperCase(),
    symbol:          order.symbol || "UNKNOWN",
    orderMode:       order.order_type  || "MARKET",
    productType:     order.product_type || "MIS",
    qty,
    filledQty,
    rejectedQty,
    pendingQty,
    inputPrice,
    executionPrice:  execPrice,
    triggerPrice:    Number(order.trigger_price ?? 0),
    target:          Number(order.target_price ?? 0),
    stopLoss:        Number(order.stop_loss_price ?? 0),
    rawStatus,
    displayStatus,
    rejectionReason: order.remarks || order.rejection_reason || order.reason || "",
  };
};

// ── bucket helpers ──────────────────────────────────────────────────────────────

const isPending  = (o) => o.displayStatus === "PENDING"  && o.filledQty === 0 && o.rejectedQty === 0;
const isInFlight = (o) => o.displayStatus === "PARTIAL"  || (o.displayStatus === "PENDING" && o.filledQty > 0 && o.pendingQty > 0);
const isResolved = (o) => o.displayStatus === "EXECUTED" || o.displayStatus === "REJECTED" || o.displayStatus === "CANCELLED" || (o.filledQty + o.rejectedQty >= o.qty && o.qty > 0);

const getStatusLabel = (o) => {
  if (o.displayStatus === "REJECTED") return "REJECTED";
  if (o.displayStatus === "CANCELLED") {
    if (o.filledQty > 0) return "PARTIAL CANCELLED";
    return "CANCELLED";
  }
  if (o.displayStatus === "PARTIAL") return "PARTIAL";
  if (o.displayStatus === "PENDING") {
    if (o.filledQty > 0 && o.pendingQty > 0) return "PARTIAL";
    return "PENDING";
  }
  if (o.filledQty > 0 && o.rejectedQty > 0) return "PARTIAL REJECTED";
  return "EXECUTED";
};

const getDisplayExchangeOrderId = (o) => {
  const statusLabel = getStatusLabel(o);
  const hasRealExchangeId = o.exchangeOrderId && o.exchangeOrderId !== "--";
  const isActive = statusLabel === "PENDING" || statusLabel === "PARTIAL";

  if (isActive) return "--";
  if ((statusLabel === "EXECUTED" || statusLabel === "PARTIAL CANCELLED" || statusLabel === "PARTIAL REJECTED") && o.filledQty > 0) {
    return hasRealExchangeId ? o.exchangeOrderId : o.orderId;
  }
  return hasRealExchangeId ? o.exchangeOrderId : "--";
};

// ── small ui pieces ─────────────────────────────────────────────────────────────

const SideBadge = ({ side }) => (
  <span style={{
    padding: "3px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 700,
    color: "#fff", display: "inline-block",
    backgroundImage: side === "BUY" ? "linear-gradient(90deg,#3b82f6,#2563eb)" : "linear-gradient(90deg,#fb923c,#f97316)",
  }}>{side}</span>
);

const ProductBadge = ({ productType }) => (
  <span style={{
    borderRadius: "999px", padding: "2px 8px", fontSize: "10px", fontWeight: 600,
    border: "1px solid var(--border)", color: "var(--muted)",
    background: "var(--surface2)", marginLeft: 6, display: "inline-block",
  }}>{productType}</span>
);

const StatusBadge = ({ label }) => {
  const l = String(label || "").toUpperCase();
  const color =
    l === "EXECUTED"         ? "#16a34a" :
    l === "REJECTED"         ? "#dc2626" :
    l === "PARTIAL REJECTED" ? "#d97706" :
    l === "CANCELLED"        ? "#6b7280" :
    l === "PARTIAL" || l === "PARTIAL CANCELLED" ? "#d97706" : "var(--muted)";
  return <span style={{ fontWeight: 700, fontSize: "12px", color }}>{label}</span>;
};

// ── main component ─────────────────────────────────────────────────────────────

const OrdersTab = () => {
  const { user } = useAuth();
  const [orders,       setOrders]       = useState([]);
  const [loading,      setLoading]      = useState(false);
  const [selectedId,   setSelectedId]   = useState(null);
  const [cancellingId, setCancellingId] = useState(null);
  const [isMobile,     setIsMobile]     = useState(() => window.innerWidth <= 900);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // ── fetch today's orders only ─────────────────────────────────────────────
  const fetchOrders = useCallback(async () => {
    setLoading(true);
    try {
      const today = todayISO();
      const isAdmin = user?.role === "ADMIN" || user?.role === "SUPER_ADMIN";
      const params = {
        from_date: today,
        to_date:   today,
        ...(!isAdmin && user?.id ? { user_id: String(user.id) } : {}),
      };
      const res = await apiService.get("/trading/orders", params);
      const raw = Array.isArray(res?.data) ? res.data : [];
      setOrders(raw.map(mapOrder));
    } catch (err) {
      console.error("Error fetching orders:", err);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => { fetchOrders(); }, [fetchOrders]);

  useEffect(() => {
    const handler = () => fetchOrders();
    window.addEventListener("orders:updated", handler);
    return () => window.removeEventListener("orders:updated", handler);
  }, [fetchOrders]);

  // ── cancel a pending order ────────────────────────────────────────────────
  const handleCancel = useCallback(async (orderId, e) => {
    e.stopPropagation();
    if (cancellingId) return;
    setCancellingId(orderId);
    try {
      await apiService.delete(`/trading/orders/${orderId}`);
      await fetchOrders();
    } catch (err) {
      console.error("Cancel order failed:", err);
      alert("Could not cancel order. " + (err?.message || ""));
    } finally {
      setCancellingId(null);
    }
  }, [cancellingId, fetchOrders]);

  // ── bucket ────────────────────────────────────────────────────────────────
  const pending  = orders.filter(isPending);
  const inFlight = orders.filter(isInFlight);
  const resolved = orders.filter(isResolved);

  const selectedOrder = orders.find((o) => o.id === selectedId) || null;

  // ── styles ────────────────────────────────────────────────────────────────
  const pageStyle = { margin: 0, padding: isMobile ? "12px" : "20px", fontFamily: "system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif", background: "transparent" };
  const layoutStyle = { display: "flex", flexDirection: isMobile ? "column" : "row", gap: "16px" };
  const tableCardStyle = { flex: "3 1 0", minWidth: 0, background: "var(--surface)", borderRadius: "12px", boxShadow: "0 10px 30px rgba(0,0,0,0.3)", padding: isMobile ? "14px" : "20px 20px 28px 20px", border: "1px solid var(--border)" };
  const detailsCardStyle = { flex: isMobile ? "1 1 auto" : "0 0 260px", width: isMobile ? "100%" : "260px", background: "var(--surface)", borderRadius: "12px", boxShadow: "0 10px 30px rgba(0,0,0,0.3)", padding: "14px 14px 14px 14px", border: "1px solid var(--border)", fontSize: "12px", color: "var(--text)", alignSelf: isMobile ? "stretch" : "flex-start", maxHeight: isMobile ? "none" : "520px", overflowY: "auto" };
  const tableOuterStyle = { borderRadius: "8px", border: "1px solid var(--border)", overflowX: "auto", background: "var(--surface)" };
  const tableStyle = { width: "100%", minWidth: "700px", borderCollapse: "collapse", fontSize: "12px" };
  const theadStyle = { background: "var(--surface2)", borderBottom: "1px solid var(--border)" };
  const thStyle    = { padding: "9px 12px", textAlign: "left", fontWeight: 600, color: "var(--muted)", whiteSpace: "nowrap" };
  const thRight    = { ...thStyle, textAlign: "right" };
  const tdStyle    = { padding: "9px 12px", color: "var(--text)", verticalAlign: "middle", whiteSpace: "nowrap" };
  const tdRight    = { ...tdStyle, textAlign: "right" };
  const rowBase    = { borderBottom: "1px solid var(--border)", background: "var(--surface)" };
  const rowClick   = { ...rowBase, cursor: "pointer" };
  const rowSelStyle= { ...rowClick, background: "var(--surface2)" };

  const sectionLabel = (txt, count) => (
    <div style={{ fontSize: "13px", fontWeight: 700, color: "var(--muted)", margin: "18px 0 8px", display: "flex", alignItems: "center", gap: "8px" }}>
      {txt}
      <span style={{ fontSize: "11px", fontWeight: 600, padding: "1px 7px", borderRadius: "999px", background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--muted)" }}>{count}</span>
    </div>
  );

  const emptyRow = (cols) => (
    <tr><td colSpan={cols} style={{ ...tdStyle, color: "var(--muted)", fontStyle: "italic", padding: "14px 12px" }}>None</td></tr>
  );

  // ── details panel ─────────────────────────────────────────────────────────
  const detailRowStyle   = { display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: "1px solid var(--border)" };
  const detailLabelStyle = { fontSize: "11px", color: "var(--muted)" };
  const detailValueStyle = { fontSize: "12px", fontWeight: 500, color: "var(--text)", textAlign: "right", maxWidth: "55%" };

  const renderDetailRow = (label, value) => (
    <div key={label} style={detailRowStyle}>
      <div style={detailLabelStyle}>{label}</div>
      <div style={detailValueStyle}>{value ?? "—"}</div>
    </div>
  );

  const detailsIcon = () => {
    if (!selectedOrder) return null;
    const lbl = getStatusLabel(selectedOrder);
    if (lbl === "EXECUTED") return (
      <div style={{ width: 26, height: 26, borderRadius: "999px", border: "2px solid #16a34a", display: "flex", alignItems: "center", justifyContent: "center", color: "#16a34a", fontSize: "14px" }}>✔</div>
    );
    if (lbl === "REJECTED" || lbl === "CANCELLED") return (
      <div style={{ width: 26, height: 26, borderRadius: "999px", border: "2px solid #dc2626", display: "flex", alignItems: "center", justifyContent: "center", color: "#dc2626", fontSize: "14px" }}>✕</div>
    );
    if (lbl === "PARTIAL REJECTED" || lbl === "PARTIAL CANCELLED") return (
      <div style={{ width: 26, height: 26, borderRadius: "999px", border: "2px solid #d97706", display: "flex", alignItems: "center", justifyContent: "center", color: "#d97706", fontSize: "13px", fontWeight: 700 }}>!</div>
    );
    return (
      <div style={{ width: 26, height: 26, borderRadius: "999px", border: "2px solid #3b82f6", display: "flex", alignItems: "center", justifyContent: "center", color: "#3b82f6", fontSize: "11px" }}>⏳</div>
    );
  };

  // ── section tables ────────────────────────────────────────────────────────

  const pendingTable = () => (
    <>
      {sectionLabel("Pending Orders", pending.length)}
      <div style={tableOuterStyle}>
        <table style={tableStyle}>
          <thead style={theadStyle}>
            <tr>
              <th style={thStyle}>Time</th>
              <th style={thStyle}>Type</th>
              <th style={thStyle}>Symbol</th>
              <th style={thStyle}>Order Type</th>
              <th style={thRight}>Qty</th>
              <th style={thRight}>Price</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Action</th>
            </tr>
          </thead>
          <tbody>
            {pending.length === 0 ? emptyRow(8) : pending.map((o) => (
              <tr key={o.id} style={selectedId === o.id ? rowSelStyle : rowClick}
                  onClick={() => setSelectedId(prev => prev === o.id ? null : o.id)}>
                <td style={tdStyle}>{o.time}</td>
                <td style={tdStyle}><SideBadge side={o.side} /></td>
                <td style={tdStyle}>{o.symbol}</td>
                <td style={tdStyle}>{o.orderMode}<ProductBadge productType={o.productType} /></td>
                <td style={tdRight}>{o.qty.toLocaleString("en-IN")}</td>
                <td style={tdRight}>{o.inputPrice > 0 ? o.inputPrice.toFixed(2) : "—"}</td>
                <td style={tdStyle}><span style={{ fontWeight: 700, fontSize: "12px", color: "#3b82f6" }}>PENDING</span></td>
                <td style={tdStyle} onClick={e => e.stopPropagation()}>
                  <button
                    onClick={(e) => handleCancel(o.id, e)}
                    disabled={cancellingId === o.id}
                    style={{
                      padding: "3px 10px",
                      borderRadius: "6px",
                      border: "1px solid #dc2626",
                      background: cancellingId === o.id ? "#fee2e2" : "transparent",
                      color: "#dc2626",
                      fontSize: "11px",
                      fontWeight: 700,
                      cursor: cancellingId === o.id ? "not-allowed" : "pointer",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {cancellingId === o.id ? "…" : "Cancel"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );

  const inFlightTable = () => (
    <>
      {sectionLabel("Open Orders", inFlight.length)}
      <div style={tableOuterStyle}>
        <table style={tableStyle}>
          <thead style={theadStyle}>
            <tr>
              <th style={thStyle}>Time</th>
              <th style={thStyle}>Type</th>
              <th style={thStyle}>Symbol</th>
              <th style={thStyle}>Order Type</th>
              <th style={thRight}>Filled / Total</th>
              <th style={thRight}>Price</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}>Action</th>
            </tr>
          </thead>
          <tbody>
            {inFlight.length === 0 ? emptyRow(8) : inFlight.map((o) => (
              <tr key={o.id} style={selectedId === o.id ? rowSelStyle : rowClick}
                  onClick={() => setSelectedId(prev => prev === o.id ? null : o.id)}>
                <td style={tdStyle}>{o.time}</td>
                <td style={tdStyle}><SideBadge side={o.side} /></td>
                <td style={tdStyle}>{o.symbol}</td>
                <td style={tdStyle}>{o.orderMode}<ProductBadge productType={o.productType} /></td>
                <td style={tdRight}>{o.filledQty.toLocaleString("en-IN")} / {o.qty.toLocaleString("en-IN")}</td>
                <td style={tdRight}>{o.executionPrice > 0 ? o.executionPrice.toFixed(2) : "—"}</td>
                <td style={tdStyle}><span style={{ fontWeight: 700, fontSize: "12px", color: "#d97706" }}>PARTIAL ({o.pendingQty} pending)</span></td>
                <td style={tdStyle} onClick={e => e.stopPropagation()}>
                  <button
                    onClick={(e) => handleCancel(o.id, e)}
                    disabled={cancellingId === o.id}
                    style={{
                      padding: "3px 10px",
                      borderRadius: "6px",
                      border: "1px solid #dc2626",
                      background: cancellingId === o.id ? "#fee2e2" : "transparent",
                      color: "#dc2626",
                      fontSize: "11px",
                      fontWeight: 700,
                      cursor: cancellingId === o.id ? "not-allowed" : "pointer",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {cancellingId === o.id ? "…" : "Cancel"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );

  const resolvedTable = () => (
    <>
      {sectionLabel("Executed Orders", resolved.length)}
      <div style={tableOuterStyle}>
        <table style={tableStyle}>
          <thead style={theadStyle}>
            <tr>
              <th style={thStyle}>Time</th>
              <th style={thStyle}>Ex. Time</th>
              <th style={thStyle}>Type</th>
              <th style={thStyle}>Symbol</th>
              <th style={thStyle}>Order Type</th>
              <th style={thRight}>Qty</th>
              <th style={thRight}>Exec. Price</th>
              <th style={thStyle}>Status</th>
              <th style={thStyle}></th>
            </tr>
          </thead>
          <tbody>
            {resolved.length === 0 ? emptyRow(9) : resolved.map((o) => {
              const lbl = getStatusLabel(o);
              const isSel = selectedId === o.id;
              return (
                <tr key={o.id} style={isSel ? rowSelStyle : rowClick}
                    onClick={() => setSelectedId(prev => prev === o.id ? null : o.id)}>
                  <td style={tdStyle}>{o.time}</td>
                  <td style={tdStyle}>{o.exTime}</td>
                  <td style={tdStyle}><SideBadge side={o.side} /></td>
                  <td style={tdStyle}>{o.symbol}</td>
                  <td style={tdStyle}>{o.orderMode}<ProductBadge productType={o.productType} /></td>
                  <td style={tdRight}>{(o.filledQty > 0 ? o.filledQty : o.qty).toLocaleString("en-IN")}</td>
                  <td style={tdRight}>{o.executionPrice > 0 ? o.executionPrice.toFixed(2) : "—"}</td>
                  <td style={tdStyle}><StatusBadge label={lbl} /></td>
                  {/* ▼ chevron — rotates when row is expanded */}
                  <td style={{ ...tdStyle, color: "var(--muted)", fontSize: "10px", paddingLeft: 4 }}>
                    <span style={{ display: "inline-block", transform: isSel ? "rotate(180deg)" : "rotate(0deg)", transition: "transform 0.2s" }}>▼</span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div style={pageStyle}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <h1 style={{ fontSize: "18px", fontWeight: 700, margin: 0, color: "var(--text)" }}>
          Today's Orders
          {orders.length > 0 && (
            <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--muted)", marginLeft: "10px" }}>
              ({orders.length} total)
            </span>
          )}
        </h1>
        <button onClick={fetchOrders} disabled={loading}
          style={{ background: "none", border: "1px solid var(--border)", borderRadius: "6px", padding: "6px 8px", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", opacity: loading ? 0.5 : 1 }}
          title="Refresh">
          <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
          </svg>
        </button>
      </div>

      <div style={layoutStyle}>
        {/* Tables */}
        <div style={tableCardStyle}>
          {loading ? (
            <div style={{ padding: "40px", textAlign: "center", color: "var(--muted)", fontSize: "13px" }}>Loading orders…</div>
          ) : (
            <>
              {pendingTable()}
              {inFlightTable()}
              {resolvedTable()}
            </>
          )}
        </div>

        {/* Details panel */}
        {selectedOrder && (
          <div style={detailsCardStyle}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
              <div style={{ fontSize: "14px", fontWeight: 700, color: "var(--text)" }}>{selectedOrder.symbol}</div>
              {detailsIcon()}
            </div>

            {/* Rejection reason block */}
            {selectedOrder.rejectionReason && (
              <div style={{ marginBottom: "12px", padding: "8px 10px", borderRadius: "6px", background: "rgba(220,38,38,0.08)", border: "1px solid rgba(220,38,38,0.25)", fontSize: "12px", color: "#fca5a5", lineHeight: 1.5 }}>
                <div style={{ fontWeight: 700, marginBottom: "3px", color: "#ef4444" }}>Rejection Reason</div>
                {selectedOrder.rejectionReason}
              </div>
            )}

            {renderDetailRow("Product",           selectedOrder.productType)}
            {renderDetailRow("Order Type",        selectedOrder.orderMode)}
            {renderDetailRow("Direction",         selectedOrder.side === "BUY" ? "Buy" : "Sell")}
            {renderDetailRow("Input Price",       selectedOrder.inputPrice > 0 ? selectedOrder.inputPrice.toFixed(2) : "—")}
            {renderDetailRow("Execution Price",   selectedOrder.executionPrice > 0 ? selectedOrder.executionPrice.toFixed(2) : "—")}
            {selectedOrder.triggerPrice > 0 && renderDetailRow("Trigger Price", selectedOrder.triggerPrice.toFixed(2))}
            {selectedOrder.target > 0          && renderDetailRow("Target",       selectedOrder.target.toFixed(2))}
            {selectedOrder.stopLoss > 0        && renderDetailRow("Stop Loss",    selectedOrder.stopLoss.toFixed(2))}
            {renderDetailRow("Quantity",          selectedOrder.qty.toLocaleString("en-IN"))}
            {renderDetailRow("Filled Qty",        selectedOrder.filledQty.toLocaleString("en-IN"))}
            {selectedOrder.rejectedQty > 0 && renderDetailRow("Rejected Qty", selectedOrder.rejectedQty.toLocaleString("en-IN"))}
            {renderDetailRow("Pending Qty",       selectedOrder.pendingQty.toLocaleString("en-IN"))}
            {renderDetailRow("Status",            (() => { const lbl = getStatusLabel(selectedOrder); return <StatusBadge label={lbl} />; })())}
            {renderDetailRow("Order Time",        selectedOrder.orderDateTime)}
            {renderDetailRow("Exchange Time",     selectedOrder.exchangeTime)}
            {renderDetailRow("Execution Time",    selectedOrder.executionTime)}
            {renderDetailRow("Order ID",          selectedOrder.orderId)}
            {renderDetailRow("Unique ID",         selectedOrder.uniqueId)}
            {renderDetailRow("Exchange Order ID", getDisplayExchangeOrderId(selectedOrder))}

            <button
              style={{ marginTop: "16px", width: "100%", padding: "8px 0", borderRadius: "6px", border: "1px solid var(--border)", background: "var(--surface2)", color: "var(--text)", fontSize: "12px", fontWeight: 600, cursor: "pointer" }}
              onClick={() => setSelectedId(null)}>
              CLOSE
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

export default OrdersTab;



