import React, { useState, useEffect, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useAuth } from '../contexts/AuthContext';

const BasketsTab = () => {
  const { user } = useAuth();
  const [baskets, setBaskets] = useState([]);
  const [availableMargin, setAvailableMargin] = useState(0);

  const fetchData = useCallback(async () => {
    try {
      const params = user?.id ? { user_id: String(user.id) } : {};
      const [basketsResponse, marginResponse] = await Promise.all([
        apiService.get('/trading/basket-orders', params),
        apiService.get('/margin/account', params),
      ]);
      
      let basketsData = basketsResponse?.data || [];
      
      // Fetch margin for each basket
      if (basketsData.length > 0) {
        const basketsWithMargin = await Promise.all(
          basketsData.map(async (basket) => {
            try {
              const marginResp = await apiService.post(`/trading/basket-orders/${basket.id}/margin`);
              return {
                ...basket,
                requiredMargin: marginResp?.data?.total_required_margin || 0
              };
            } catch (err) {
              console.error(`Error fetching margin for basket ${basket.id}:`, err);
              return { ...basket, requiredMargin: 0 };
            }
          })
        );
        basketsData = basketsWithMargin;
      }
      
      setBaskets(basketsData);
      const serverAvailableMargin = Number(marginResponse?.data?.available_margin ?? 0);
      setAvailableMargin(Number.isFinite(serverAvailableMargin) ? serverAvailableMargin : 0);
    } catch (err) {
      console.error('Error fetching baskets data:', err);
      setBaskets([]);
      setAvailableMargin(0);
    }
  }, [user]);

  useEffect(() => { fetchData(); }, [fetchData]);

  useEffect(() => {
    const handleBasketsUpdated = () => fetchData();
    window.addEventListener('baskets:updated', handleBasketsUpdated);
    return () => window.removeEventListener('baskets:updated', handleBasketsUpdated);
  }, [fetchData]);

  const handleExecute = async (basketId) => {
    try {
      const basket = baskets.find(b => b.id === basketId);
      if (!basket) return;
      await apiService.post('/trading/basket-orders/execute', {
        basket_id: basketId,
        name: basket.name,
        orders: basket.legs.map(leg => ({
          security_id: leg.symbol,
          quantity: leg.qty,
          transaction_type: leg.side,
          order_type: 'LIMIT',
          product_type: leg.productType === 'NORMAL' ? 'DELIVERY' : 'INTRADAY',
          exchange: leg.exchange || 'NSE_EQ',
          price: leg.price
        }))
      });
    } catch (err) { console.error('Error executing basket:', err); }
  };

  const handleDeleteBasket = async (basketId) => {
    try {
      await apiService.delete('/trading/basket-orders/' + basketId);
      setBaskets((prev) => prev.filter((b) => b.id !== basketId));
    } catch (err) { console.error('Error deleting basket:', err); }
  };

  const handleDeleteLeg = (basketId, legId) => {
    setBaskets((prev) => prev.map((b) => b.id === basketId ? { ...b, legs: b.legs.filter((l) => l.id !== legId) } : b));
  };

  // styles
  const pageStyle = { minHeight: "100vh", margin: 0, padding: "24px", fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", background: "transparent" };
  const mainCardStyle = { maxWidth: "1200px", margin: "0 auto", background: "var(--surface)", borderRadius: "12px", boxShadow: "0 10px 30px rgba(0,0,0,0.3)", padding: "16px 16px 24px 16px", border: "1px solid var(--border)" };
  const basketWrapperStyle = { borderRadius: "10px", border: "1px solid var(--border)", marginTop: "8px", marginBottom: "12px", overflow: "hidden", background: "var(--surface)" };
  const basketHeaderStyle = { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", background: "var(--surface2)", borderBottom: "1px solid var(--border)" };
  const basketTitleStyle = { fontSize: "13px", fontWeight: 600, color: "var(--text)" };
  const marginInfoStyle = { fontSize: "11px", color: "var(--muted)", marginRight: "8px" };
  const marginValueStyle = { fontWeight: 600 };
  const headerRightStyle = { display: "flex", alignItems: "center", gap: "8px" };
  const executeButton = (canExecute) => ({ padding: "4px 16px", borderRadius: "8px", border: "1px solid var(--border)", background: canExecute ? "var(--surface2)" : "var(--surface2)", color: canExecute ? "#818cf8" : "var(--muted)", fontSize: "12px", fontWeight: 600, cursor: canExecute ? "pointer" : "default" });
  const deleteIconButton = { border: "none", background: "none", cursor: "pointer", padding: "4px", color: "#f97373", fontSize: "14px" };
  const tableStyle = { width: "100%", borderCollapse: "collapse", fontSize: "12px" };
  const theadStyle = { background: "var(--surface2)", borderBottom: "1px solid var(--border)" };
  const thStyle = { padding: "8px 14px", textAlign: "left", fontWeight: 600, color: "var(--muted)", whiteSpace: "nowrap" };
  const thRight = { ...thStyle, textAlign: "right" };
  const rowStyle = { borderBottom: "1px solid var(--border)", background: "var(--surface)" };
  const tdStyle = { padding: "8px 14px", color: "var(--text)", verticalAlign: "middle", whiteSpace: "nowrap" };
  const tdRight = { ...tdStyle, textAlign: "right" };
  const deleteLegButton = { border: "none", background: "none", cursor: "pointer", padding: "4px", color: "#f97373", fontSize: "14px" };

  const sideBadge = (side) => {
    const normalizedSide = String(side || '').toUpperCase();
    const base = { padding: "4px 10px", borderRadius: "999px", fontSize: "11px", fontWeight: 700, color: "#ffffff", display: "inline-block" };
    const bg = normalizedSide === "BUY" ? "linear-gradient(90deg, #3b82f6, #2563eb)" : "linear-gradient(90deg, #fb923c, #f97316)";
    return <span style={{ ...base, backgroundImage: bg }}>{normalizedSide || '-'}</span>;
  };

  const formatMoney = (v) => {
    const num = Number(v || 0);
    return "₹" + num.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  };

  return (
    <div style={pageStyle}>
      <div style={{ ...mainCardStyle, marginBottom: '16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px' }}>
          <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 'bold' }}>Basket Orders</h3>
          <button onClick={fetchData} style={{ background: 'none', border: '1px solid #d1d5db', borderRadius: '4px', padding: '6px 12px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '14px' }} title="Refresh baskets">
            <svg width="16" height="16" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg>
            Refresh
          </button>
        </div>
      </div>
      <div style={mainCardStyle}>
        {baskets.length === 0 && (
          <div style={{ textAlign: 'center', padding: '24px', color: '#a1a1aa' }}>No baskets yet. Create a basket order via the Order Modal.</div>
        )}
        {baskets.map((basket) => {
          const requiredMargin = Number(basket.requiredMargin || 0);
          const basketAvailable = Number(basket.availableMargin || availableMargin || 0);
          const canExecute = requiredMargin <= basketAvailable && requiredMargin > 0;
          return (
            <div key={basket.id} style={basketWrapperStyle}>
              <div style={basketHeaderStyle}>
                <div style={basketTitleStyle}>{basket.name}</div>
                <div style={headerRightStyle}>
                  <div style={marginInfoStyle}>
                    Required Margin: <span style={marginValueStyle}>{formatMoney(requiredMargin)}</span> | Available Margin: <span style={marginValueStyle}>{formatMoney(basketAvailable)}</span>
                  </div>
                  <button style={executeButton(canExecute)} onClick={() => canExecute && handleExecute(basket.id)}>Execute</button>
                  <button style={deleteIconButton} onClick={() => handleDeleteBasket(basket.id)} title="Delete basket">🗑</button>
                </div>
              </div>
              <table style={tableStyle}>
                <thead style={theadStyle}>
                  <tr>
                    <th style={thStyle}>Type</th>
                    <th style={thStyle}>Symbol</th>
                    <th style={thStyle}>Product</th>
                    <th style={thRight}>Qty</th>
                    <th style={thRight}>Price</th>
                    <th style={thRight}></th>
                  </tr>
                </thead>
                <tbody>
                  {basket.legs.length === 0 ? (
                    <tr style={rowStyle}><td style={tdStyle} colSpan={6}></td></tr>
                  ) : (
                    basket.legs.map((leg) => (
                      <tr key={leg.id} style={rowStyle}>
                        <td style={tdStyle}>{sideBadge(leg.side || leg.transaction_type)}</td>
                        <td style={tdStyle}>{leg.symbol}</td>
                        <td style={tdStyle}>{leg.productType || leg.product_type || '-'}</td>
                        <td style={tdRight}>{Number(leg.qty || leg.quantity || 0).toLocaleString("en-IN")}</td>
                        <td style={tdRight}>{Number(leg.price || 0).toFixed(2)}</td>
                        <td style={tdRight}><button style={deleteLegButton} onClick={() => handleDeleteLeg(basket.id, leg.id)} title="Delete leg">🗑</button></td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default BasketsTab;
