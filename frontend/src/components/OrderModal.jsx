import React, { useState, useEffect, useRef, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useAuth } from '../contexts/AuthContext';

const OrderModal = ({ isOpen, onClose, orderData, orderType = "BUY" }) => {
  const { user } = useAuth();

  // ── draggable ───────────────────────────────────────────────────────────────
  const modalRef = useRef(null);
  const dragOrigin = useRef(null);
  const [position, setPosition] = useState({ x: 0, y: 0 });

  const onMouseDown = (e) => {
    dragOrigin.current = { mx: e.clientX, my: e.clientY, px: position.x, py: position.y };
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  };
  const onMouseMove = useCallback((e) => {
    if (!dragOrigin.current) return;
    setPosition({ x: dragOrigin.current.px + (e.clientX - dragOrigin.current.mx), y: dragOrigin.current.py + (e.clientY - dragOrigin.current.my) });
  }, []);
  const onMouseUp = useCallback(() => {
    dragOrigin.current = null;
    document.removeEventListener("mousemove", onMouseMove);
    document.removeEventListener("mouseup", onMouseUp);
  }, [onMouseMove]);

  useEffect(() => { return () => { document.removeEventListener("mousemove", onMouseMove); document.removeEventListener("mouseup", onMouseUp); }; }, [onMouseMove, onMouseUp]);

  // ── form state ──────────────────────────────────────────────────────────────
  const [side, setSide] = useState(orderType || "BUY");
  const [quantity, setQuantity] = useState(1);
  const [productType, setProductType] = useState("MIS");
  const [priceType, setPriceType] = useState("MARKET");
  const [limitPrice, setLimitPrice] = useState("");
  const [isBasketOrder, setIsBasketOrder] = useState(false);
  const [isSuperOrder, setIsSuperOrder] = useState(false);
  const [targetPrice, setTargetPrice] = useState("");
  const [stopLossPrice, setStopLossPrice] = useState("");
  const [trailingJump, setTrailingJump] = useState("");
  const [margin, setMargin] = useState(null);
  const [availableMargin, setAvailableMargin] = useState(null);
  const [baskets, setBaskets] = useState([]);
  const [selectedBasketId, setSelectedBasketId] = useState("");
  const [newBasketName, setNewBasketName] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  const isMultiLeg = Array.isArray(orderData?.legs) && orderData.legs.length > 1;
  const lotSizePerLeg = Number(orderData?.lot_size || orderData?.lotSize || (isMultiLeg ? orderData?.legs?.[0]?.lotSize : 1) || 1);
  
  // Detect if this is an equity instrument (NSE_EQ, BSE_EQ, or just NSE/BSE)
  const exchangeSegment = String(orderData?.exchange_segment || orderData?.exchange || '').toUpperCase();
  const isEquityInstrument = exchangeSegment.includes('_EQ') || exchangeSegment === 'NSE' || exchangeSegment === 'BSE';

  // reset on open
  useEffect(() => {
    if (isOpen) {
      const defaultSide = orderType || orderData?.legs?.[0]?.action || "BUY";
      setSide(defaultSide);
      setProductType("MIS");
      setPriceType("MARKET");
      setLimitPrice("");
      setIsBasketOrder(false);
      setIsSuperOrder(false);
      setTargetPrice("");
      setStopLossPrice("");
      setTrailingJump("");
      setMargin(null);
      setError("");
      setSuccess("");
      setQuantity(1);
      // fetch baskets + margin
      apiService.get('/trading/basket-orders').then(res => { if (res?.data) { setBaskets(res.data); if (res.data.length > 0) setSelectedBasketId(res.data[0].id); } }).catch(() => {});
      apiService.get('/margin/account').then(res => { setAvailableMargin(Number(res?.data?.available_margin ?? 0)); }).catch(() => {});
    }
  }, [isOpen, orderType, orderData, user]);

  // calculate margin when key fields change
  useEffect(() => {
    if (!isOpen) return;
    const lots = Number(quantity) || 0;
    if (lots <= 0) { setMargin(null); return; }

    const legsToCalc = isMultiLeg
      ? (orderData?.legs || [])
      : [{
          symbol: orderData?.symbol,
          security_id: orderData?.security_id || orderData?.token,
          exchange_segment: orderData?.exchange_segment || orderData?.exchange,
          action: side,
          ltp: orderData?.ltp,
        }];

    if (!legsToCalc.length) { setMargin(null); return; }

    const timer = setTimeout(async () => {
      try {
          const perLegMargins = await Promise.all(legsToCalc.map((leg) => {
          const marketPriceHint = Number(leg?.ltp || 0);
          const legLotSize = Number(leg?.lotSize || leg?.lot_size || lotSizePerLeg || 1);
          const qtyUnits = Math.max(0, Math.trunc(lots * legLotSize));
          const payload = {
            symbol: leg.symbol,
              security_id: String(leg.security_id || leg.token || ''),
            exchange_segment: leg.exchange_segment || leg.exchange,
            transaction_type: side,
            quantity: qtyUnits,
            order_type: priceType,
            product_type: productType,
            price: priceType === "LIMIT" ? Number(limitPrice) || 0 : (marketPriceHint > 0 ? marketPriceHint : 0),
          };
          return apiService.post('/margin/calculate', payload)
            .then(res => Number(res?.data?.required_margin ?? res?.required_margin ?? 0))
            .catch(() => 0);
        }));
        const totalMargin = perLegMargins.reduce((acc, v) => acc + (Number.isFinite(v) ? v : 0), 0);
        setMargin(totalMargin);
      } catch {
        setMargin(null);
      }
    }, 400);

    return () => clearTimeout(timer);
  }, [isOpen, orderData, side, quantity, productType, priceType, limitPrice, user, isMultiLeg]);

  const handleSubmit = async () => {
    if (isSubmitting) return;
    setIsSubmitting(true);
    setError("");
    setSuccess("");
    try {
      const lots = Number(quantity);
      if (!lots || lots <= 0) throw new Error("Lots must be greater than 0");
      if (priceType === "LIMIT" && (!limitPrice || Number(limitPrice) <= 0)) throw new Error("Limit price required");
      
      // Validate that quantity respects the lot size constraint
      if (lotSizePerLeg > 1) {
        const qtyUnits = lots * lotSizePerLeg;
        if (!Number.isInteger(qtyUnits) || qtyUnits <= 0) {
          throw new Error(`Quantity multiplied by lot size must be a positive integer. Lot size: ${lotSizePerLeg}`);
        }
      }

      const qtyUnitsSingle = Math.max(0, Math.trunc(lots * lotSizePerLeg));

      const primarySecurityId = isMultiLeg ? String(orderData?.legs?.[0]?.security_id || orderData?.legs?.[0]?.token || '') : String(orderData?.security_id || orderData?.token || '');
      const primaryExchange = isMultiLeg ? (orderData?.legs?.[0]?.exchange_segment || orderData?.legs?.[0]?.exchange) : (orderData?.exchange_segment || orderData?.exchange);
      const marketPriceHint = Number(orderData?.ltp || orderData?.legs?.[0]?.ltp || 0);
      const basePayload = {
        symbol: orderData?.symbol,
        security_id: primarySecurityId,
        exchange_segment: primaryExchange,
        transaction_type: side,
        quantity: qtyUnitsSingle,
        order_type: priceType,
        product_type: productType,
        price: priceType === "LIMIT" ? Number(limitPrice) : (marketPriceHint > 0 ? marketPriceHint : 0),
      };

      const legsPayload = isMultiLeg
        ? (orderData.legs || []).map((leg) => {
            const legMarketHint = Number(leg?.ltp || 0);
            const legLotSize = Number(leg?.lotSize || leg?.lot_size || lotSizePerLeg || 1);
            // Validate lot size constraint for each leg
            if (legLotSize > 1) {
              const legQtyUnits = lots * legLotSize;
              if (!Number.isInteger(legQtyUnits)) {
                throw new Error(`Quantity for ${leg.symbol} must respect lot size (${legLotSize})`);
              }
            }
            const qtyUnits = Math.max(0, Math.trunc(lots * legLotSize));
            return {
              symbol: leg.symbol,
              security_id: String(leg.security_id || leg.token || ''),
              exchange_segment: leg.exchange_segment || leg.exchange,
              transaction_type: side,
              quantity: qtyUnits,
              order_type: priceType,
              product_type: productType,
              price: priceType === "LIMIT" ? Number(limitPrice) : (legMarketHint > 0 ? legMarketHint : 0),
            };
          })
        : [];

      if (isBasketOrder) {
        // Add to basket
        const legsToSave = isMultiLeg ? legsPayload.map((leg) => ({
          ...leg,
          side,
          qty: leg.quantity,
          exchange: leg.exchange_segment,
          productType,
        })) : [{
          symbol: orderData?.symbol,
          security_id: orderData?.security_id,
          exchange: orderData?.exchange_segment || orderData?.exchange,
          side,
          qty: qtyUnitsSingle,
          productType,
          price: priceType === "LIMIT" ? Number(limitPrice) : (marketPriceHint > 0 ? marketPriceHint : 0),
          order_type: priceType,
        }];

        if (selectedBasketId) {
          for (const leg of legsToSave) {
            await apiService.post(`/trading/basket-orders/${selectedBasketId}/legs`, leg);
          }
        } else {
          const name = newBasketName.trim() || `Basket ${Date.now()}`;
          await apiService.post('/trading/basket-orders', {
            name,
            legs: legsToSave,
          });
        }
        window.dispatchEvent(new CustomEvent('baskets:updated'));
        setSuccess("Added to basket successfully!");
      } else if (isSuperOrder) {
        if (!targetPrice || !stopLossPrice) throw new Error("Target price and stop-loss are required for Super Order");
        await apiService.post('/trading/orders', {
          ...basePayload,
          is_super: true,
          target_price: Number(targetPrice),
          stop_loss_price: Number(stopLossPrice),
          trailing_jump: trailingJump ? Number(trailingJump) : undefined,
        });
        apiService.clearCacheEntry('/trading/orders');
        window.dispatchEvent(new CustomEvent('orders:updated'));
        window.dispatchEvent(new CustomEvent('positions:updated'));
        setSuccess("Super order placed!");
      } else {
        if (isMultiLeg && legsPayload.length) {
          const execBody = { orders: legsPayload.map((leg) => ({
            symbol: leg.symbol,
            security_id: leg.security_id,
            exchange: leg.exchange_segment,
            side: leg.transaction_type,
            qty: leg.quantity,
            productType: leg.product_type,
            price: leg.price,
            order_type: leg.order_type,
          })) };
          await apiService.post('/trading/basket-orders/execute', execBody);
        } else {
          await apiService.post('/trading/orders', basePayload);
        }
        apiService.clearCacheEntry('/trading/orders');
        window.dispatchEvent(new CustomEvent('orders:updated'));
        window.dispatchEvent(new CustomEvent('positions:updated'));
        const quantityLabel = isEquityInstrument ? `${qtyUnitsSingle} stock(s)` : `${lots} lot(s)`;
        const displayName = orderData?.displaySymbol || orderData?.symbol || '';
        setSuccess(isMultiLeg ? `Straddle order placed — ${side} ${lots} lot(s) ×2` : `Order placed — ${side} ${quantityLabel} × ${displayName}`);
      }
      setTimeout(() => { setSuccess(""); onClose?.(); }, 1500);
    } catch (err) {
      // Extract actual error message from API response or use fallback
      let errorMsg = "Order failed";
      
      // Try to get detailed error from API response
      if (err?.response?.data?.detail) {
        errorMsg = err.response.data.detail;
      } else if (err?.response?.data?.message) {
        errorMsg = err.response.data.message;
      } else if (err?.message) {
        errorMsg = err.message;
      }
      
      // Show error message in red box, don't hide after timeout
      setError(errorMsg);
    }
    setIsSubmitting(false);
  };

  if (!isOpen) return null;

  // ── styles ───────────────────────────────────────────────────────────────────
  const isBuy = side === "BUY";
  const legs = isMultiLeg ? (orderData?.legs || []) : [orderData];
  const overlay = { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 999, display: 'flex', alignItems: 'center', justifyContent: 'center' };
  const modal = { position: 'relative', transform: `translate(${position.x}px, ${position.y}px)`, width: '380px', maxHeight: '90vh', overflowY: 'auto', background: 'var(--surface)', borderRadius: '14px', boxShadow: '0 20px 60px rgba(0,0,0,0.4)', zIndex: 1000, userSelect: 'none' };
  const header = { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', cursor: 'grab', borderBottom: '1px solid var(--border)', background: isBuy ? 'linear-gradient(135deg,rgba(59,130,246,0.15),rgba(37,99,235,0.2))' : 'linear-gradient(135deg,rgba(249,115,22,0.15),rgba(194,65,12,0.2))' };
  const title = { fontSize: '15px', fontWeight: 700, color: isBuy ? '#60a5fa' : '#fb923c' };
  const closeBtn = { border: 'none', background: 'none', cursor: 'pointer', fontSize: '18px', color: 'var(--muted)', lineHeight: 1 };
  const body = { padding: '18px' };
  const label = { fontSize: '12px', fontWeight: 600, color: 'var(--muted)', marginBottom: '4px', display: 'block' };
  const toggleRow = { display: 'flex', gap: '6px', marginBottom: '14px' };
  const toggleBtn = (active, color) => ({ flex: 1, padding: '8px', borderRadius: '8px', border: `2px solid ${active ? color : 'var(--border)'}`, background: active ? color : 'var(--surface2)', color: active ? '#ffffff' : 'var(--muted)', fontSize: '13px', fontWeight: active ? 700 : 500, cursor: 'pointer', transition: 'all 0.15s' });
  const input = { width: '100%', padding: '8px 10px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--control-bg)', color: 'var(--text)', fontSize: '13px', outline: 'none', boxSizing: 'border-box', transition: 'border 0.15s' };
  const inputGroup = { marginBottom: '12px' };
  const checkRow = { display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', cursor: 'pointer', fontSize: '13px', color: 'var(--text)' };
  const submitBtn = { width: '100%', padding: '11px', borderRadius: '10px', border: 'none', background: isBuy ? 'linear-gradient(135deg,#3b82f6,#1d4ed8)' : 'linear-gradient(135deg,#f97316,#c2410c)', color: '#fff', fontSize: '15px', fontWeight: 700, cursor: isSubmitting ? 'not-allowed' : 'pointer', opacity: isSubmitting ? 0.7 : 1, marginTop: '6px' };
  const errorBox = { padding: '10px', borderRadius: '8px', background: 'rgba(220,38,38,0.12)', color: 'var(--negative-text)', fontSize: '13px', marginBottom: '10px', border: '1px solid rgba(220,38,38,0.3)' };
  const successBox = { padding: '10px', borderRadius: '8px', background: 'rgba(22,163,74,0.12)', color: 'var(--positive-text)', fontSize: '13px', marginBottom: '10px', border: '1px solid rgba(22,163,74,0.3)' };
  const marginInfo = { fontSize: '12px', color: 'var(--muted)', marginTop: '8px', padding: '8px', background: 'var(--surface2)', borderRadius: '8px', border: '1px solid var(--border)' };
  const selectStyle = { ...input, appearance: 'none', backgroundImage: 'none' };

  return (
    <div style={overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div style={modal} ref={modalRef}>
        <div style={header} onMouseDown={onMouseDown}>
          <div>
            <div style={title}>{isBuy ? '▲ BUY' : '▼ SELL'} — {isMultiLeg ? 'Straddle (2 legs)' : (orderData?.displaySymbol || orderData?.symbol || 'Order')}</div>
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px' }}>{orderData?.exchange_segment || orderData?.exchange || ''}</div>
          </div>
          <button style={closeBtn} onClick={onClose}>✕</button>
        </div>
        <div style={body}>
          {error && <div style={errorBox}>{error}</div>}
          {success && success !== "order_rejected" && <div style={successBox}>{success}</div>}
          {success === "order_rejected" && (
            <div style={{ padding: '10px', borderRadius: '8px', background: 'rgba(217,119,6,0.12)', color: '#fbbf24', fontSize: '13px', marginBottom: '10px', border: '1px solid rgba(217,119,6,0.3)', fontWeight: 600, textAlign: 'center' }}>
              Order Rejected — see Orders tab for details
            </div>
          )}

          {isMultiLeg && (
            <div style={{ ...marginInfo, marginBottom: '10px' }}>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>Legs</div>
              {legs.map((leg, idx) => (
                <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: 4 }}>
                  <span>{leg.displaySymbol || leg.symbol}</span>
                  <span>{(leg.action || side)} • {Number(quantity || 0)} lot(s) × {Number(leg?.lotSize || lotSizePerLeg || 1)}</span>
                </div>
              ))}
            </div>
          )}

          {/* BUY / SELL toggle */}
          <div style={toggleRow}>
            <button style={toggleBtn(isBuy, '#3b82f6')} onClick={() => setSide('BUY')}>BUY</button>
            <button style={toggleBtn(!isBuy, '#f97316')} onClick={() => setSide('SELL')}>SELL</button>
          </div>

          {/* Product type */}
          <div style={inputGroup}>
            <span style={label}>Product</span>
            <div style={toggleRow}>
              {['MIS', 'NORMAL'].map(pt => (
                <button key={pt} style={toggleBtn(productType === pt, '#8b5cf6')} onClick={() => setProductType(pt)}>{pt}</button>
              ))}
            </div>
          </div>

          {/* Price type */}
          <div style={inputGroup}>
            <span style={label}>Order Type</span>
            <div style={toggleRow}>
              {['MARKET', 'LIMIT'].map(pt => (
                <button key={pt} style={toggleBtn(priceType === pt, '#6366f1')} onClick={() => setPriceType(pt)}>{pt}</button>
              ))}
            </div>
          </div>

          {/* Quantity */}
          <div style={inputGroup}>
            <label style={label}>
              {isEquityInstrument ? 'Quantity (Stocks)' : 'Quantity (Lots per leg)'}
            </label>
            <input type="number" min="1" value={quantity} onChange={e => setQuantity(e.target.value)} style={input} />
          </div>

          {isEquityInstrument ? (
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '-6px', marginBottom: '10px' }}>
              For equity: 1 quantity = 1 stock
            </div>
          ) : (
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '-6px', marginBottom: '10px' }}>
              Lot size: {Number(lotSizePerLeg || 1)} (Qty = lots × lot size)
            </div>
          )}

          {/* Limit price */}
          {priceType === "LIMIT" && (
            <div style={inputGroup}>
              <label style={label}>Limit Price</label>
              <input type="number" min="0" step="0.05" value={limitPrice} onChange={e => setLimitPrice(e.target.value)} placeholder="0.00" style={input} />
            </div>
          )}

          {/* Super Order toggle */}
          <label style={checkRow}>
            <input type="checkbox" checked={isSuperOrder} onChange={e => { setIsSuperOrder(e.target.checked); if (e.target.checked) setIsBasketOrder(false); }} style={{ width: 14, height: 14 }} />
            Super Order (Target + SL + Trailing)
          </label>

          {isSuperOrder && (
            <>
              <div style={inputGroup}>
                <label style={label}>Target Price</label>
                <input type="number" min="0" step="0.05" value={targetPrice} onChange={e => setTargetPrice(e.target.value)} placeholder="0.00" style={input} />
              </div>
              <div style={inputGroup}>
                <label style={label}>Stop-Loss Price</label>
                <input type="number" min="0" step="0.05" value={stopLossPrice} onChange={e => setStopLossPrice(e.target.value)} placeholder="0.00" style={input} />
              </div>
              <div style={inputGroup}>
                <label style={label}>Trailing Jump (optional)</label>
                <input type="number" min="0" step="0.5" value={trailingJump} onChange={e => setTrailingJump(e.target.value)} placeholder="0.00" style={input} />
              </div>
            </>
          )}

          {/* Basket toggle */}
          <label style={checkRow}>
            <input type="checkbox" checked={isBasketOrder} onChange={e => { setIsBasketOrder(e.target.checked); if (e.target.checked) setIsSuperOrder(false); }} style={{ width: 14, height: 14 }} />
            Add to Basket
          </label>

          {isBasketOrder && (
            <div style={inputGroup}>
              <label style={label}>Select Basket</label>
              {baskets.length > 0 ? (
                <select value={selectedBasketId} onChange={e => setSelectedBasketId(e.target.value)} style={selectStyle}>
                  {baskets.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
                  <option value="">+ Create new basket</option>
                </select>
              ) : (
                <div>
                  <input type="text" value={newBasketName} onChange={e => setNewBasketName(e.target.value)} placeholder="New basket name" style={input} />
                </div>
              )}
              {selectedBasketId === "" && baskets.length > 0 && (
                <input type="text" value={newBasketName} onChange={e => setNewBasketName(e.target.value)} placeholder="New basket name" style={{ ...input, marginTop: '6px' }} />
              )}
            </div>
          )}

          {/* Margin info */}
          {(margin !== null || availableMargin !== null) && (
            <div style={marginInfo}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span>Required Margin</span>
                <span style={{ fontWeight: 600 }}>{margin !== null ? '₹' + Number(margin).toLocaleString("en-IN", { maximumFractionDigits: 0 }) : '—'}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: '4px' }}>
                <span>Available Margin</span>
                <span style={{ fontWeight: 600 }}>{availableMargin !== null ? '₹' + Number(availableMargin).toLocaleString("en-IN", { maximumFractionDigits: 0 }) : '—'}</span>
              </div>
            </div>
          )}

          <button style={submitBtn} onClick={handleSubmit} disabled={isSubmitting}>
            {isSubmitting ? 'Placing...' : (isBasketOrder ? 'Add to Basket' : `Place ${side} Order`)}
          </button>
        </div>
      </div>
    </div>
  );
};

export default OrderModal;
