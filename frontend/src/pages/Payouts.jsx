import React, { useState, useEffect } from "react";
import { apiService } from '../services/apiService';

const PayoutsPage = () => {
  const [payouts, setPayouts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [updatingId, setUpdatingId] = useState(null);
  const [updateError, setUpdateError] = useState('');
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);

  useEffect(() => {
    apiService.get('/payouts').then(res => setPayouts(res?.data || [])).catch(() => setPayouts([])).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const s = { page: { padding: isMobile ? '12px' : '24px', fontFamily: 'system-ui,sans-serif', color: 'var(--text)' }, title: { fontSize: '20px', fontWeight: 700, marginBottom: '20px', color: 'var(--text)' }, card: { background: 'var(--surface)', borderRadius: '8px', border: '1px solid var(--border)', padding: isMobile ? '12px' : '20px' }, th: { padding: '10px 14px', textAlign: 'left', background: 'var(--surface2)', borderBottom: '1px solid var(--border)', fontWeight: 600, color: 'var(--muted)', fontSize: '12px' }, td: { padding: '10px 14px', borderBottom: '1px solid var(--border)', fontSize: '13px', color: 'var(--text)' } };
  const statusBadge = (st) => {
    const v = (st || '').toUpperCase();
    const c = (v === 'PAID' || v === 'COMPLETED' || v === 'APPROVED') ? '#4ade80' : (v === 'PENDING' || v === 'HOLD') ? '#fbbf24' : '#a1a1aa';
    return <span style={{ padding: '2px 10px', borderRadius: '999px', background: c + '20', color: c, fontSize: '11px', fontWeight: 700 }}>{v || '—'}</span>;
  };

  const STATUS_OPTIONS = ['APPROVED', 'REJECTED', 'HOLD', 'PENDING', 'PAID'];

  const updateStatus = async (payoutId, nextStatus) => {
    setUpdateError('');
    const prev = payouts;
    setPayouts(list => list.map(p => (p.id === payoutId ? { ...p, status: nextStatus } : p)));
    setUpdatingId(payoutId);
    try {
      await apiService.patch(`/payouts/${payoutId}`, { status: nextStatus });
    } catch (e) {
      setPayouts(prev);
      setUpdateError(e?.message || 'Failed to update payout status');
    } finally {
      setUpdatingId(null);
    }
  };

  return (
    <div style={s.page}>
      <div style={s.title}>Payouts</div>
      <div style={s.card}>
        {updateError ? <div style={{ color: '#fca5a5', fontSize: '12px', marginBottom: '10px' }}>{updateError}</div> : null}
        {loading ? <div>Loading...</div> : payouts.length === 0 ? <div style={{ color: '#a1a1aa', fontSize: '13px' }}>No payout records found.</div> : (
          <div style={{ overflowX: 'auto', overflowY: 'hidden' }}>
            <table style={{ width: '100%', minWidth: '760px', borderCollapse: 'collapse' }}>
              <thead><tr>{['Date', 'User', 'Amount', 'Mode', 'Status'].map(h => <th key={h} style={s.th}>{h}</th>)}</tr></thead>
              <tbody>
                {payouts.map((p, i) => (
                  <tr key={p.id || i}>
                    <td style={s.td}>{p.date}</td>
                    <td style={s.td}>{p.user_name || p.user_id}</td>
                    <td style={s.td}>{'₹' + Number(p.amount).toLocaleString('en-IN')}</td>
                    <td style={s.td}>{p.mode || '—'}</td>
                    <td style={s.td}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                        {statusBadge(p.status)}
                        <select
                          value={(p.status || '').toUpperCase()}
                          onChange={(e) => updateStatus(p.id, e.target.value)}
                          disabled={!p.id || updatingId === p.id}
                          style={{
                            padding: '6px 10px',
                            borderRadius: '8px',
                            border: '1px solid var(--border)',
                            background: 'var(--surface2)',
                            color: 'var(--text)',
                            fontSize: '12px',
                          }}
                        >
                          {STATUS_OPTIONS.map(opt => (
                            <option key={opt} value={opt}>{opt}</option>
                          ))}
                        </select>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
};

export default PayoutsPage;
