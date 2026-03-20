import React, { useState, useEffect, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useMarketPulse } from '../hooks/useMarketPulse';
import { useAuth } from '../contexts/AuthContext';

const isDerivativePosition = (position = {}) => {
  const exchange = String(position.exchange || '').toUpperCase();
  const symbol = String(position.symbol || '').toUpperCase();

  const isExplicitEquity =
    exchange === 'NSE_EQ' ||
    exchange === 'BSE_EQ' ||
    (exchange === 'NSE' && !symbol.endsWith('CE') && !symbol.endsWith('PE') && !symbol.includes('FUT')) ||
    (exchange === 'BSE' && !symbol.endsWith('CE') && !symbol.endsWith('PE') && !symbol.includes('FUT'));

  if (isExplicitEquity) return false;

  return (
    exchange.includes('FNO') ||
    exchange.includes('FO') ||
    exchange.includes('OPT') ||
    exchange.includes('FUT') ||
    exchange.includes('COMM') ||
    symbol.endsWith('CE') ||
    symbol.endsWith('PE') ||
    symbol.includes('FUT')
  );
};

const csvEscape = (value) => {
  if (value === null || value === undefined) return '';
  const str = String(value).replace(/"/g, '""');
  return /[",\n]/.test(str) ? `"${str}"` : str;
};

const downloadCsv = (filename, headers, rows) => {
  const csv = [
    headers.map(csvEscape).join(','),
    ...rows.map((row) => row.map(csvEscape).join(',')),
  ].join('\n');

  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
};

const PositionsTab = ({ productFilter = null }) => {
  const { user } = useAuth();
  const isAdmin = user?.role === 'ADMIN' || user?.role === 'SUPER_ADMIN';
  const isAdminScopedView = Boolean(isAdmin && productFilter);
  const { pulse, marketActive } = useMarketPulse();
  const [positions, setPositions] = useState([]);
  const [selectedOpenIds, setSelectedOpenIds] = useState(new Set());
  const [exitModalOpen, setExitModalOpen] = useState(false);
  const [exitRow, setExitRow] = useState(null);
  const [exitQty, setExitQty] = useState('');
  const [exitOrderType, setExitOrderType] = useState('MARKET');
  const [exitLimitPrice, setExitLimitPrice] = useState('');
  const [exitTriggerPrice, setExitTriggerPrice] = useState('');
  const [exitSubmitting, setExitSubmitting] = useState(false);
  const [exitError, setExitError] = useState('');
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);
  const [allUsers, setAllUsers] = useState([]);
  const [selectedUserIds, setSelectedUserIds] = useState([]);
  const [userSelectOpen, setUserSelectOpen] = useState(false);
  const [pendingOrders, setPendingOrders] = useState([]);
  const [loadingOrders, setLoadingOrders] = useState(false);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const fetchPositions = useCallback(async () => {
    try {
      if (isAdminScopedView) {
        const res = await apiService.get('/admin/positions/userwise');
        const users = res?.data?.data || res?.data || [];
        const normalizedFilter = String(productFilter || 'MIS').toUpperCase();
        const mapped = [];

        // Also populate users list for filter dropdown
        const usersList = users.map(u => ({
          user_id: String(u.user_id || ''),
          mobile: String(u.mobile || u.user_no || ''),
          name: u.display_name || u.mobile || String(u.user_id || '')
        }));
        setAllUsers(usersList);

        users.forEach((userRow) => {
          const userId = String(userRow.user_id || '');
          const userNo = userRow.user_no;
          const userName = userRow.display_name || userRow.mobile || userId;

          (userRow.positions || []).forEach((position, index) => {
            const positionProduct = String(position.product_type || 'MIS').toUpperCase();
            if (positionProduct !== normalizedFilter) return;

            const status = String(position.status || 'OPEN').toUpperCase();
            if (status !== 'OPEN' && status !== 'CLOSED') return;

            const qty = Number(position.quantity || 0);
            const avgEntry = Number(position.avg_price || 0);
            const currentLtp = Number(position.ltp || avgEntry);
            const pnl = Number(position.pnl || 0);
            const token = Number(position.instrument_token || 0);
            const positionId = position.position_id || null;

            mapped.push({
              id: `${userId}:${token}:${position.opened_at || index}:${status}`,
              closeId: positionId || token,
              instrumentToken: token,
              lotSize: Number(position.lot_size || 1),
              userId,
              userNo,
              userName,
              productType: positionProduct,
              exchangeSegment: position.exchange_segment || '',
              symbol: position.symbol || '—',
              qty,
              avgEntry: avgEntry.toFixed ? avgEntry.toFixed(2) : avgEntry,
              currentLtp: currentLtp.toFixed ? currentLtp.toFixed(2) : currentLtp,
              mtm: status === 'OPEN' ? pnl : 0,
              realizedPnl: status === 'CLOSED' ? pnl : 0,
              status,
            });
          });
        });

        setPositions(mapped);
      } else {
        const res = await apiService.get('/portfolio/positions');
        const ownPositions = Array.isArray(res?.data) ? res.data : [];
        const mapped = ownPositions.map((position, index) => {
          const qty = Number(position.quantity || 0);
          const avgEntry = Number(position.avg_price || 0);
          const currentLtp = Number(position.ltp || avgEntry);
          const mtm = Number(position.mtm || 0);
          const realizedPnl = Number(position.realized_pnl || 0);
          const status = String(position.status || 'OPEN').toUpperCase();
          const productType = String(position.product_type || 'MIS').toUpperCase();

          return {
            id: `${position.id || index}:${status}`,
            closeId: position.id,
            instrumentToken: Number(position.instrument_token || 0),
            lotSize: Number(position.lot_size || 1),
            userId: String(user?.id || ''),
            userNo: user?.mobile || '—',
            userName: user?.name || user?.mobile || 'Trader',
            productType,
            exchangeSegment: position.exchange_segment || '',
            symbol: position.symbol || '—',
            qty,
            avgEntry: avgEntry.toFixed ? avgEntry.toFixed(2) : avgEntry,
            currentLtp: currentLtp.toFixed ? currentLtp.toFixed(2) : currentLtp,
            mtm,
            realizedPnl,
            status,
          };
        });

        setPositions(mapped);
      }
    } catch (err) { console.error('Error fetching positions:', err); }
  }, [isAdminScopedView, productFilter, user?.id, user?.mobile, user?.name]);

  const fetchPendingOrders = useCallback(async () => {
    if (!isAdminScopedView) {
      setPendingOrders([]);
      return;
    }

    setLoadingOrders(true);
    try {
      // Fetch for all users; selected users are filtered client-side.
      const res = await apiService.get('/admin/positions/userwise');
      const users = res?.data?.data || res?.data || [];
      const allOrders = [];

      for (const userRow of users) {
        const uid = String(userRow.user_id || '');
        try {
          const ordersRes = await apiService.get(`/admin/positions/userwise/${uid}/active-orders`);
          const userOrders = ordersRes?.data?.data || ordersRes?.data || [];
          if (Array.isArray(userOrders)) {
            userOrders.forEach(order => {
              allOrders.push({
                ...order,
                userName: userRow.display_name || userRow.mobile || uid,
                userMobile: userRow.mobile || userRow.user_no || ''
              });
            });
          }
        } catch (err) {
          console.error(`Error fetching orders for user ${uid}:`, err);
        }
      }

      setPendingOrders(allOrders);
    } catch (err) {
      console.error('Error fetching pending orders:', err);
      setPendingOrders([]);
    } finally {
      setLoadingOrders(false);
    }
  }, [isAdminScopedView]);

  useEffect(() => { 
    fetchPositions();
    if (isAdminScopedView) {
      fetchPendingOrders();
    }
  }, [fetchPositions, isAdminScopedView, fetchPendingOrders]);

  useEffect(() => {
    setSelectedOpenIds(new Set());
  }, [selectedUserIds]);

  useEffect(() => {
    const handlePositionsUpdated = () => fetchPositions();
    window.addEventListener('positions:updated', handlePositionsUpdated);
    return () => window.removeEventListener('positions:updated', handlePositionsUpdated);
  }, [fetchPositions]);

  // Live tick — update LTP/MTM in-place from pulse prices, no API round-trip (avoids blink)
  useEffect(() => {
    if (!marketActive || !pulse?.prices) return;
    const prices = pulse.prices;
    setPositions(prev => {
      let changed = false;
      const next = prev.map(p => {
        if (p.status !== 'OPEN') return p;
        const newLtpRaw = prices[p.symbol] ?? prices[String(p.instrumentToken)];
        if (newLtpRaw === undefined) return p;
        const newLtp = Number(newLtpRaw);
        const oldLtp = Number(p.currentLtp);
        if (newLtp === oldLtp) return p;
        changed = true;
        const avgEntry = Number(p.avgEntry);
        const qty = Number(p.qty);
        const newMtm = (newLtp - avgEntry) * qty;
        return { ...p, currentLtp: newLtp.toFixed(2), mtm: newMtm };
      });
      return changed ? next : prev;
    });
  }, [pulse?.prices, marketActive]);

  const userOptions = allUsers.map((u) => {
    const id = String(u?.user_id || '').trim();
    if (!id) return null;
    const label = `${u?.name || id}${u?.mobile ? ` (${u.mobile})` : ''}`;
    return { id, label };
  }).filter(Boolean);

  const allUsersSelected = userOptions.length > 0 && selectedUserIds.length === userOptions.length;
  const toggleUserSelection = (uid) => {
    setSelectedUserIds((prev) => (prev.includes(uid) ? prev.filter((id) => id !== uid) : [...prev, uid]));
  };
  const selectAllUsers = () => setSelectedUserIds(userOptions.map((u) => u.id));
  const clearAllUsers = () => setSelectedUserIds([]);

  const filteredPositions = (isAdminScopedView && selectedUserIds.length > 0)
    ? positions.filter((p) => selectedUserIds.includes(String(p.userId || '')))
    : positions;

  const openPositions = filteredPositions.filter((p) => p.status === "OPEN");
  const closedPositions = filteredPositions.filter((p) => p.status === "CLOSED");
  const filteredPendingOrders = (isAdminScopedView && selectedUserIds.length > 0)
    ? pendingOrders.filter((o) => selectedUserIds.includes(String(o.user_id || o.userId || '')))
    : pendingOrders;
  const totalMtm = openPositions.reduce((sum, p) => sum + parseFloat(p.mtm), 0);
  const totalClosed = closedPositions.reduce((sum, p) => sum + p.realizedPnl, 0);

  const handleSaveAsCsv = () => {
    const rows = [
      ...openPositions.map((p) => [
        'OPEN',
        p.productType || '',
        isAdminScopedView ? `${p.userName || ''} (${p.userNo || '—'})` : '',
        p.symbol || '',
        Number(p.qty || 0),
        Number(p.avgEntry || 0),
        Number(p.currentLtp || 0),
        Number(p.mtm || 0),
      ]),
      ...closedPositions.map((p) => [
        'CLOSED',
        p.productType || '',
        isAdminScopedView ? `${p.userName || ''} (${p.userNo || '—'})` : '',
        p.symbol || '',
        Number(p.qty || 0),
        Number(p.avgEntry || 0),
        Number(p.exitPrice || 0),
        Number(p.realizedPnl || 0),
      ]),
    ];

    const suffix = String(productFilter || 'positions').toLowerCase();
    downloadCsv(
      `${suffix}_positions.csv`,
      ['Status', 'Product', 'User', 'Instrument', 'Qty', 'Avg', 'LTP/Exit', 'P&L'],
      rows,
    );
  };

  const toggleSelectOne = (id) => {
    setSelectedOpenIds((prev) => { const next = new Set(prev); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  };
  const toggleSelectAllOpen = () => {
    setSelectedOpenIds((prev) => prev.size === openPositions.length ? new Set() : new Set(openPositions.map((p) => p.id)));
  };
  const openExitModal = (row) => {
    const maxQty = Math.max(1, Math.abs(Number(row?.qty || 0)));
    setExitRow(row);
    setExitQty(String(maxQty));
    setExitOrderType('MARKET');
    setExitLimitPrice('');
    setExitTriggerPrice('');
    setExitError('');
    setExitModalOpen(true);
  };
  const closeExitModal = () => {
    if (exitSubmitting) return;
    setExitModalOpen(false);
    setExitRow(null);
    setExitError('');
  };
  const handleExitOne = (id) => {
    const row = openPositions.find((p) => p.id === id);
    if (!row) return;
    openExitModal(row);
  };
  const handleExitSelected = () => { if (!selectedOpenIds.size) return; exitPositions(selectedOpenIds); };

  const submitExitOrder = async () => {
    if (!exitRow || exitSubmitting) return;

    const maxQty = Math.max(1, Math.abs(Number(exitRow.qty || 0)));
    const lotSize = Math.max(1, Number(exitRow?.lotSize || exitRow?.lot_size || 1));
    const qtyNum = Number(exitQty);
    const orderType = String(exitOrderType || 'MARKET').toUpperCase();

    if (!Number.isInteger(qtyNum) || qtyNum <= 0 || qtyNum > maxQty) {
      setExitError(`Qty must be an integer between 1 and ${maxQty}`);
      return;
    }

    if (qtyNum % lotSize !== 0) {
      setExitError(`Qty must be in multiples of lot size (${lotSize})`);
      return;
    }

    if ((orderType === 'LIMIT' || orderType === 'SLL') && (!exitLimitPrice || Number(exitLimitPrice) <= 0)) {
      setExitError('Valid limit price is required');
      return;
    }

    if ((orderType === 'SLM' || orderType === 'SLL') && (!exitTriggerPrice || Number(exitTriggerPrice) <= 0)) {
      setExitError('Valid trigger price is required');
      return;
    }

    setExitSubmitting(true);
    setExitError('');
    try {
      const isLong = Number(exitRow.qty || 0) >= 0;
      const payload = {
        user_id: isAdminScopedView ? String(exitRow.userId || '') : String(user?.id || ''),
        symbol: exitRow.symbol,
        security_id: Number(exitRow.instrumentToken || 0) || undefined,
        instrument_token: Number(exitRow.instrumentToken || 0) || undefined,
        exchange_segment: exitRow.exchangeSegment || 'NSE_EQ',
        transaction_type: isLong ? 'SELL' : 'BUY',
        quantity: qtyNum,
        order_type: orderType,
        product_type: String(exitRow.productType || 'MIS').toUpperCase(),
      };

      if (orderType === 'LIMIT') {
        payload.limit_price = Number(exitLimitPrice);
        payload.price = Number(exitLimitPrice);
      }
      if (orderType === 'SLM') {
        payload.trigger_price = Number(exitTriggerPrice);
      }
      if (orderType === 'SLL') {
        payload.trigger_price = Number(exitTriggerPrice);
        payload.limit_price = Number(exitLimitPrice);
        payload.price = Number(exitLimitPrice);
      }

      await apiService.post('/trading/orders', payload);
      window.dispatchEvent(new CustomEvent('orders:updated'));
      window.dispatchEvent(new CustomEvent('positions:updated'));
      await new Promise((resolve) => setTimeout(resolve, 500));
      await fetchPositions();
      setSelectedOpenIds(new Set());
      closeExitModal();
    } catch (err) {
      setExitError(err?.data?.detail || err?.message || 'Failed to place exit order');
    } finally {
      setExitSubmitting(false);
    }
  };

  const exitPositions = async (idsSet) => {
    try {
      const selectedRows = openPositions.filter((p) => idsSet.has(p.id));
      for (const row of selectedRows) {
        const qtyNum = Math.max(1, Math.abs(Number(row?.qty || 0)));
        const isLong = Number(row?.qty || 0) >= 0;
        const payload = {
          user_id: isAdminScopedView ? String(row.userId || '') : String(user?.id || ''),
          symbol: row.symbol,
          security_id: Number(row.instrumentToken || 0) || undefined,
          instrument_token: Number(row.instrumentToken || 0) || undefined,
          exchange_segment: row.exchangeSegment || 'NSE_EQ',
          transaction_type: isLong ? 'SELL' : 'BUY',
          quantity: qtyNum,
          order_type: 'MARKET',
          product_type: String(row.productType || 'MIS').toUpperCase(),
        };
        await apiService.post('/trading/orders', payload);
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
      await fetchPositions();
      setSelectedOpenIds(new Set());
    } catch (err) {
      console.error('Error exiting positions:', err);
      const errorMsg = err?.data?.detail || err?.message || 'Failed to close position';
      alert(`❌ Position Exit Failed\n\n${errorMsg}\n\nPlease try again or contact support if the issue persists.`);
      await fetchPositions(); // Refresh to show actual state
      setSelectedOpenIds(new Set());
    }
  };

  // styles
  const pageStyle = { minHeight: "100vh", margin: 0, padding: isMobile ? "12px" : "24px", fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", background: "transparent" };
  const mainCardStyle = { maxWidth: "1200px", margin: "0 auto", background: "var(--surface)", borderRadius: "12px", boxShadow: "0 10px 30px rgba(0,0,0,0.3)", padding: isMobile ? "14px" : "24px 24px 32px 24px", border: "1px solid var(--border)" };
  const sectionHeaderRowStyle = { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px", marginTop: "8px", flexWrap: "wrap", gap: "8px" };
  const topHeaderRowStyle = { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "14px", flexWrap: "wrap", gap: "8px" };
  const sectionTitleStyle = { fontSize: "14px", fontWeight: 600, color: "var(--text)" };
  const totalTextStyle = { fontSize: "13px", fontWeight: 600, color: "var(--text)" };
  const totalValueStyle = { fontWeight: 700 };
  const tableOuterStyle = { borderRadius: "8px", border: "1px solid var(--border)", overflowX: "auto", overflowY: "hidden", background: "var(--surface)" };
  const tableStyle = { width: "100%", minWidth: isAdminScopedView ? "980px" : "860px", borderCollapse: "collapse", fontSize: "12px" };
  const theadStyle = { background: "var(--surface2)", borderBottom: "1px solid var(--border)" };
  const thStyle = { padding: "10px 12px", textAlign: "left", fontWeight: 600, color: "var(--muted)", whiteSpace: "nowrap" };
  const thRight = { ...thStyle, textAlign: "right" };
  const rowStyle = { borderBottom: "1px solid var(--border)", background: "var(--surface)" };
  const tdStyle = { padding: "10px 12px", color: "var(--text)", verticalAlign: "middle", whiteSpace: "nowrap" };
  const tdRight = { ...tdStyle, textAlign: "right" };
  const checkboxStyle = { width: 14, height: 14 };
  const exitButtonStyle = { border: "1px solid var(--border)", borderRadius: "6px", padding: "4px 12px", fontSize: "12px", background: "var(--surface2)", color: "var(--text)", cursor: "pointer" };
  const exitSelectedButtonStyle = { border: "1px solid var(--border)", borderRadius: "6px", padding: "4px 12px", fontSize: "12px", background: selectedOpenIds.size ? "#f97316" : "var(--surface2)", color: selectedOpenIds.size ? "#ffffff" : "var(--muted)", cursor: selectedOpenIds.size ? "pointer" : "default" };
  const qtyTextStyle = { fontVariantNumeric: "tabular-nums" };
  const plPositive = { color: "var(--positive-text)" };
  const plNegative = { color: "var(--negative-text)" };
  const totalRowStyle = { ...rowStyle, background: "var(--surface2)", fontWeight: 600 };
  const modalOverlayStyle = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.45)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' };
  const modalCardStyle = { width: '420px', maxWidth: '92vw', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '10px', boxShadow: '0 14px 32px rgba(0,0,0,0.35)', padding: '16px' };
  const modalTitleStyle = { fontSize: '14px', fontWeight: 700, color: 'var(--text)', marginBottom: '10px' };
  const inputStyle = { width: '100%', border: '1px solid var(--border)', background: 'var(--surface2)', color: 'var(--text)', borderRadius: '6px', padding: '8px 10px', fontSize: '12px' };
  const labelStyle = { fontSize: '11px', color: 'var(--muted)', marginBottom: '4px', display: 'block' };
  const btnBase = { border: '1px solid var(--border)', borderRadius: '6px', padding: '8px 12px', fontSize: '12px', cursor: 'pointer' };
  const csvButtonStyle = { border: '1px solid var(--border)', borderRadius: '6px', padding: '8px 12px', fontSize: '12px', cursor: 'pointer', fontWeight: 700, background: 'var(--surface2)', color: 'var(--text)' };

  const formatMoney = (v) => "₹" + Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  const filterSelectStyle = {
    border: '1px solid var(--border)',
    background: 'var(--surface2)',
    color: 'var(--text)',
    borderRadius: '6px',
    padding: '8px 10px',
    fontSize: '12px',
    cursor: 'pointer'
  };
  
  return (
    <div style={pageStyle}>
      <div style={mainCardStyle}>
        {isAdminScopedView && (
          <div style={topHeaderRowStyle}>
            <div style={{ ...sectionTitleStyle, fontSize: '16px' }}>{`P.${String(productFilter || '').toLowerCase()}`}</div>
            <button onClick={handleSaveAsCsv} style={csvButtonStyle}>
              save as csv
            </button>
          </div>
        )}

        {isAdminScopedView && (
          <div style={{...sectionHeaderRowStyle, marginBottom: '14px'}}>
            <div style={labelStyle}>Filter by User:</div>
            <div style={{ position: 'relative', minWidth: isMobile ? '100%' : '320px' }}>
              <button
                type="button"
                onClick={() => setUserSelectOpen((prev) => !prev)}
                style={{ ...filterSelectStyle, width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <span>
                  {selectedUserIds.length === 0
                    ? 'All Users'
                    : `${selectedUserIds.length} user${selectedUserIds.length === 1 ? '' : 's'} selected`}
                </span>
                <span style={{ fontSize: '11px', color: 'var(--muted)' }}>{userSelectOpen ? '▲' : '▼'}</span>
              </button>

              {userSelectOpen && (
                <div style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, zIndex: 20, width: '100%', maxHeight: '280px', overflowY: 'auto', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '8px', boxShadow: '0 10px 25px rgba(0,0,0,0.2)', padding: '8px' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', padding: '6px' }}>
                    <input
                      type="checkbox"
                      checked={allUsersSelected}
                      onChange={(e) => (e.target.checked ? selectAllUsers() : clearAllUsers())}
                    />
                    Select All
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', padding: '6px', borderBottom: '1px solid var(--border)', marginBottom: '6px' }}>
                    <input type="checkbox" checked={selectedUserIds.length === 0} onChange={clearAllUsers} />
                    Clear Filter (All Users)
                  </label>

                  {userOptions.map((u) => (
                    <label key={u.id} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', padding: '6px' }}>
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
          </div>
        )}

        <div style={sectionHeaderRowStyle}>
          <div style={{ ...sectionTitleStyle, display: 'flex', alignItems: 'center', gap: '8px' }}>
            {isAdminScopedView
              ? `Open ${productFilter} Derivatives Positions (${openPositions.length})`
              : `Open Positions (${openPositions.length})`}
            <button onClick={fetchPositions} style={{ background: 'none', border: '1px solid #d1d5db', borderRadius: '4px', padding: '4px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }} title="Refresh positions">
              <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
            </button>
          </div>
          <div style={totalTextStyle}>
            Total MTM:{" "}
            <span style={{ ...totalValueStyle, backgroundColor: "var(--surface)", ...(totalMtm >= 0 ? plPositive : plNegative) }}>
              {formatMoney(totalMtm)}
            </span>
          </div>
        </div>

        <div style={tableOuterStyle}>
          <table style={tableStyle}>
            <thead style={theadStyle}>
              <tr>
                <th style={thStyle}><input type="checkbox" style={checkboxStyle} checked={openPositions.length > 0 && selectedOpenIds.size === openPositions.length} onChange={toggleSelectAllOpen} /></th>
                <th style={thStyle}>Product</th>
                {isAdminScopedView && <th style={thStyle}>User</th>}
                <th style={thStyle}>Instrument</th>
                <th style={thRight}>Qty.</th>
                <th style={thRight}>Avg.</th>
                <th style={thRight}>LTP</th>
                <th style={thRight}>P&L</th>
                <th style={{ ...thRight }}><button style={exitSelectedButtonStyle} onClick={handleExitSelected}>Exit Selected</button></th>
              </tr>
            </thead>
            <tbody>
              {openPositions.length === 0 ? (
                <tr><td style={tdStyle} colSpan={isAdminScopedView ? 9 : 8}>No open positions.</td></tr>
              ) : (
                <>
                  {openPositions.map((p) => (
                    <tr key={p.id} style={rowStyle}>
                      <td style={tdStyle}><input type="checkbox" style={checkboxStyle} checked={selectedOpenIds.has(p.id)} onChange={() => toggleSelectOne(p.id)} /></td>
                      <td style={tdStyle}>{p.productType}</td>
                      {isAdminScopedView && <td style={tdStyle}>{p.userName} ({p.userNo || '—'})</td>}
                      <td style={tdStyle}>{p.symbol}</td>
                      <td style={{ ...tdRight, ...qtyTextStyle }}>{p.qty.toLocaleString("en-IN")}</td>
                      <td style={tdRight}>{p.avgEntry}</td>
                      <td style={tdRight}>{p.currentLtp}</td>
                      <td style={{ ...tdRight, backgroundColor: "var(--surface)", ...(parseFloat(p.mtm) >= 0 ? plPositive : plNegative) }}>{formatMoney(parseFloat(p.mtm))}</td>
                      <td style={{ ...tdStyle }}><button style={exitButtonStyle} onClick={() => handleExitOne(p.id)}>Exit</button></td>
                    </tr>
                  ))}
                  <tr style={totalRowStyle}>
                    <td style={tdStyle}></td><td style={tdStyle}></td>{isAdminScopedView && <td style={tdStyle}></td>}<td style={tdStyle}></td>
                    <td style={tdRight}></td><td style={tdRight}></td>
                    <td style={{ ...tdRight, color: "var(--text)", backgroundColor: "var(--surface2)" }}>Total</td>
                    <td style={{ ...tdRight, backgroundColor: "var(--surface2)", ...(totalMtm >= 0 ? plPositive : plNegative) }}>{formatMoney(totalMtm)}</td>
                    <td style={tdStyle}></td>
                  </tr>
                </>
              )}
            </tbody>
          </table>
        </div>

        <div style={{ ...sectionHeaderRowStyle, marginTop: "24px" }}>
          <div style={sectionTitleStyle}>
            {isAdminScopedView
              ? `Intraday Closed ${productFilter} Derivatives Positions (${closedPositions.length})`
              : `Closed Positions (${closedPositions.length})`}
          </div>
        </div>

        <div style={tableOuterStyle}>
          <table style={tableStyle}>
            <thead style={theadStyle}>
              <tr>
                <th style={thStyle}><input type="checkbox" style={checkboxStyle} disabled /></th>
                <th style={thStyle}>Product</th>
                {isAdminScopedView && <th style={thStyle}>User</th>}
                <th style={thStyle}>Instrument</th>
                <th style={thRight}>Qty.</th>
                <th style={thRight}>Avg.</th>
                <th style={thRight}>LTP</th>
                <th style={thRight}>P&L</th>
              </tr>
            </thead>
            <tbody>
              {closedPositions.length === 0 ? (
                <tr><td style={tdStyle} colSpan={isAdminScopedView ? 8 : 7}>{isAdminScopedView ? 'No intraday closed positions yet.' : 'No closed positions yet.'}</td></tr>
              ) : (
                <>
                  {closedPositions.map((p) => (
                    <tr key={p.id} style={rowStyle}>
                      <td style={tdStyle}><input type="checkbox" style={checkboxStyle} disabled /></td>
                      <td style={tdStyle}>{p.productType}</td>
                      {isAdminScopedView && <td style={tdStyle}>{p.userName} ({p.userNo || '—'})</td>}
                      <td style={tdStyle}>{p.symbol}</td>
                      <td style={{ ...tdRight, ...qtyTextStyle }}>{p.qty.toLocaleString("en-IN")}</td>
                      <td style={tdRight}>{p.avgEntry}</td>
                      <td style={tdRight}>{p.exitPrice || "0.00"}</td>
                      <td style={{ ...tdRight, backgroundColor: "var(--surface)", ...(p.realizedPnl >= 0 ? plPositive : plNegative) }}>{formatMoney(p.realizedPnl)}</td>
                    </tr>
                  ))}
                  <tr style={totalRowStyle}>
                    <td style={tdStyle}></td><td style={tdStyle}></td>{isAdminScopedView && <td style={tdStyle}></td>}
                    <td style={{ ...tdStyle, color: "#111827" }}>Total</td>
                    <td style={tdRight}></td><td style={tdRight}></td><td style={tdRight}></td>
                    <td style={{ ...tdRight, backgroundColor: "var(--surface2)", ...(totalClosed >= 0 ? plPositive : plNegative) }}>{formatMoney(totalClosed)}</td>
                  </tr>
                </>
              )}
            </tbody>
          </table>
        </div>

        {isAdminScopedView && (
          <>
            <div style={{ ...sectionHeaderRowStyle, marginTop: "24px" }}>
              <div style={{...sectionTitleStyle, display: 'flex', alignItems: 'center', gap: '8px'}}>
                Pending/Active Orders {selectedUserIds.length > 0 && `(${selectedUserIds.length} selected)`} ({filteredPendingOrders.length})
                <button 
                  onClick={() => fetchPendingOrders()} 
                  style={{ background: 'none', border: '1px solid #d1d5db', borderRadius: '4px', padding: '4px', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }} 
                  title="Refresh orders"
                >
                  <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
                </button>
              </div>
            </div>

            {loadingOrders ? (
              <div style={{padding: '16px', textAlign: 'center', color: 'var(--muted)'}}>Loading orders...</div>
            ) : (
              <div style={tableOuterStyle}>
                <table style={tableStyle}>
                  <thead style={theadStyle}>
                    <tr>
                      <th style={thStyle}>User</th>
                      <th style={thStyle}>Symbol</th>
                      <th style={thStyle}>Side</th>
                      <th style={thStyle}>Type</th>
                      <th style={thStyle}>Product</th>
                      <th style={thRight}>Qty</th>
                      <th style={thRight}>Filled</th>
                      <th style={thRight}>Unfilled</th>
                      <th style={thRight}>Price</th>
                      <th style={thStyle}>Status</th>
                      <th style={thStyle}>Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredPendingOrders.length === 0 ? (
                      <tr><td style={tdStyle} colSpan="11">No pending/active orders.</td></tr>
                    ) : (
                      filteredPendingOrders.map((order, idx) => {
                        const placeTime = order.placed_at 
                          ? new Date(order.placed_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
                          : '—';
                        return (
                          <tr key={`${order.order_id}:${idx}`} style={rowStyle}>
                            <td style={tdStyle}>{order.userName || order.user_id}</td>
                            <td style={tdStyle}>{order.symbol}</td>
                            <td style={{...tdStyle, fontWeight: 600, color: order.side === 'BUY' ? '#10b981' : '#ef4444'}}>
                              {order.side}
                            </td>
                            <td style={tdStyle}>{order.order_type}</td>
                            <td style={tdStyle}>{order.product_type}</td>
                            <td style={{...tdRight, ...qtyTextStyle}}>{Number(order.quantity || 0).toLocaleString('en-IN')}</td>
                            <td style={{...tdRight, ...qtyTextStyle}}>{Number(order.filled_qty || 0).toLocaleString('en-IN')}</td>
                            <td style={{...tdRight, ...qtyTextStyle, color: Number(order.unfilled_qty || 0) > 0 ? '#f97316' : 'var(--text)'}}>
                              {Number(order.unfilled_qty || 0).toLocaleString('en-IN')}
                            </td>
                            <td style={tdRight}>{order.price ? Number(order.price).toFixed(2) : (order.trigger_price ? `(T: ${Number(order.trigger_price).toFixed(2)})` : '—')}</td>
                            <td style={{...tdStyle, fontWeight: 500, color: order.status === 'PENDING' ? '#f97316' : order.status === 'PARTIAL' || order.status === 'PARTIAL_FILL' ? '#3b82f6' : '#8b5cf6'}}>
                              {order.status}
                            </td>
                            <td style={tdStyle}>{placeTime}</td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      {exitModalOpen && exitRow && (
        <div style={modalOverlayStyle} onClick={closeExitModal}>
          <div style={modalCardStyle} onClick={(e) => e.stopPropagation()}>
            <div style={modalTitleStyle}>Exit Order — {exitRow.symbol}</div>
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginBottom: '10px' }}>
              Open Qty: {Math.abs(Number(exitRow.qty || 0)).toLocaleString('en-IN')} ({Number(exitRow.qty || 0) >= 0 ? 'Long' : 'Short'})
              {' · '}Lot Size: {Math.max(1, Number(exitRow?.lotSize || exitRow?.lot_size || 1))}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', gap: '10px' }}>
              <div>
                <label style={labelStyle}>Exit Qty</label>
                <input
                  type="number"
                  min={1}
                  max={Math.max(1, Math.abs(Number(exitRow?.qty || 0)))}
                  step={1}
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
                  <option value="SLM">SLM</option>
                  <option value="SLL">SLL</option>
                </select>
              </div>
            </div>

            {(exitOrderType === 'LIMIT' || exitOrderType === 'SLL') && (
              <div style={{ marginTop: '10px' }}>
                <label style={labelStyle}>Limit Price</label>
                <input
                  type="number"
                  min={0}
                  step="0.05"
                  value={exitLimitPrice}
                  onChange={(e) => setExitLimitPrice(e.target.value)}
                  style={inputStyle}
                />
              </div>
            )}

            {(exitOrderType === 'SLM' || exitOrderType === 'SLL') && (
              <div style={{ marginTop: '10px' }}>
                <label style={labelStyle}>Trigger Price</label>
                <input
                  type="number"
                  min={0}
                  step="0.05"
                  value={exitTriggerPrice}
                  onChange={(e) => setExitTriggerPrice(e.target.value)}
                  style={inputStyle}
                />
              </div>
            )}

            {exitError && (
              <div style={{ marginTop: '10px', fontSize: '11px', color: '#ef4444' }}>{exitError}</div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '14px' }}>
              <button onClick={closeExitModal} disabled={exitSubmitting} style={{ ...btnBase, background: 'var(--surface2)', color: 'var(--text)' }}>
                Cancel
              </button>
              <button
                onClick={submitExitOrder}
                disabled={exitSubmitting}
                style={{ ...btnBase, background: '#f97316', color: '#fff', borderColor: '#f97316' }}
              >
                {exitSubmitting ? 'Placing…' : 'Place Exit Order'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default PositionsTab;
