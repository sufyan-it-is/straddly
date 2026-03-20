import React, { useEffect, useState, useCallback } from "react";
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { apiService } from '../services/apiService';
import PayinWorkspace from '../components/payin/PayinWorkspace';

import LedgerPage from './Ledger';
import PandLPage from './PandL';
import TradeHistoryPage from './TradeHistory';

const ProfilePage = () => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [active, setActive] = useState('profile');
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const s = {
    page:  { padding: '24px', fontFamily: 'system-ui,sans-serif', color: 'var(--text)', minHeight: '100vh' },
    top:   { background: 'var(--surface)', borderRadius: '10px', border: '1px solid var(--border)', padding: '22px 24px', marginBottom: '16px' },
    title: { fontSize: '20px', fontWeight: 800, margin: 0, color: 'var(--text)' },
    sub:   { fontSize: '13px', color: 'var(--muted)', marginTop: '6px' },
    shell: { background: 'var(--surface)', borderRadius: '10px', border: '1px solid var(--border)', overflow: 'hidden' },
    tabs:  { display: 'flex', gap: '10px', padding: '14px 16px', borderBottom: '1px solid var(--border)', flexWrap: 'wrap' },
    tab:   (on) => ({
      padding: '7px 16px', borderRadius: '999px',
      border: '1px solid ' + (on ? '#9ca3af' : 'var(--border)'),
      background: on ? 'var(--surface2)' : 'var(--surface)',
      color: 'var(--text)',
      fontSize: '13px', fontWeight: 700,
      cursor: 'pointer',
    }),
    body:  { padding: '18px 18px 22px 18px' },
    card:  { background: 'var(--surface)', borderRadius: '10px', border: '1px solid var(--border)', padding: '18px 20px' },
    row:   { display: 'flex', justifyContent: 'space-between', padding: '10px 0', borderBottom: '1px solid var(--border)', fontSize: '14px' },
    label: { color: 'var(--muted)', fontWeight: 500 },
    value: { fontWeight: 700, color: 'var(--text)' },
    btn:   { marginTop: '18px', padding: '10px 18px', borderRadius: '8px', border: '1px solid #7f1d1d', background: '#7f1d1d', color: '#fff', fontSize: '14px', fontWeight: 700, cursor: 'pointer' },
  };

  const tabs = [
    { id: 'profile', label: 'Profile' },
    { id: 'payin', label: 'Payin' },
    { id: 'ledger', label: 'Ledger' },
    { id: 'pnl', label: 'P&L Reports' },
    { id: 'trades', label: 'Trade History' },
    { id: 'margin', label: 'Margin' },
  ];

  const payinUrl = `${window.location.origin}/payin`;

  return (
    <div style={{ ...s.page, padding: isMobile ? '12px' : '24px' }}>
      <div style={s.top}>
        <h1 style={s.title}>Profile</h1>
        <div style={s.sub}>Manage your account and reports</div>
      </div>

      <div style={s.shell}>
        <div style={{ ...s.tabs, overflowX: 'auto' }}>
          {tabs.map(t => (
            <button key={t.id} style={s.tab(active === t.id)} onClick={() => setActive(t.id)}>
              {t.label}
            </button>
          ))}
        </div>

        <div style={{ ...s.body, padding: isMobile ? '12px' : '18px 18px 22px 18px' }}>
          {active === 'profile' && (
            <div style={{ ...s.card, maxWidth: isMobile ? '100%' : '560px' }}>
              {user ? (
                <>
                  <div style={s.row}><span style={s.label}>Name</span><span style={s.value}>{user.name || user.username || '—'}</span></div>
                  <div style={s.row}><span style={s.label}>Mobile</span><span style={s.value}>{user.mobile || '—'}</span></div>
                  <div style={s.row}><span style={s.label}>Role</span><span style={s.value}>{user.role || '—'}</span></div>
                  <div style={{ ...s.row, borderBottom: 'none' }}><span style={s.label}>Client ID</span><span style={s.value}>{user.client_id || user.id || '—'}</span></div>
                  <button style={s.btn} onClick={logout}>Logout</button>
                </>
              ) : <div>Not logged in.</div>}
            </div>
          )}

          {active === 'payin' && (
            <div style={{ display: 'grid', gap: 12 }}>
              <div style={{ ...s.card, maxWidth: isMobile ? '100%' : '720px' }}>
                <div style={{ ...s.row }}>
                  <span style={s.label}>Payin URL</span>
                  <span style={s.value}>{payinUrl}</span>
                </div>
                <div style={{ display: 'flex', gap: 8, marginTop: 14 }}>
                  <button
                    onClick={() => navigate('/payin')}
                    style={{
                      padding: '9px 14px',
                      borderRadius: '8px',
                      border: '1px solid #1d4ed8',
                      background: '#1d4ed8',
                      color: '#fff',
                      fontSize: '13px',
                      fontWeight: 700,
                      cursor: 'pointer',
                    }}
                  >
                    Open Payin Page
                  </button>
                </div>
              </div>
              <PayinWorkspace showHeading={false} mode="viewer" />
            </div>
          )}

          {active === 'ledger' && (
            <div style={{ margin: isMobile ? '-12px' : '-18px' }}>
              <LedgerPage />
            </div>
          )}

          {active === 'pnl' && (
            <div style={{ margin: isMobile ? '-12px' : '-18px' }}>
              <PandLPage hideUserSelect={true} />
            </div>
          )}

          {active === 'trades' && (
            <div style={{ margin: isMobile ? '-12px' : '-18px' }}>
              <TradeHistoryPage />
            </div>
          )}

          {active === 'margin' && (
            <MarginTab />
          )}
        </div>
      </div>
    </div>
  );
};


function MoneyCard({ label, value, color }) {
  return (
    <div style={{ flex: '1 1 240px', background: 'var(--surface)', borderRadius: '8px', padding: '16px 18px', border: '1px solid var(--border)' }}>
      <div style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '6px', fontWeight: 700 }}>{label}</div>
      <div style={{ fontSize: '16px', fontWeight: 900, color: color || 'var(--text)' }}>{value}</div>
    </div>
  );
}


function MarginTab() {
  const [loading, setLoading] = useState(true);
  const [m, setM] = useState({ allotted_margin: 0, used_margin: 0, available_margin: 0 });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.get('/margin/account');
      const d = res?.data?.data || res?.data || {};
      setM({
        allotted_margin: Number(d.allotted_margin ?? d.total_balance ?? 0),
        used_margin: Number(d.used_margin ?? 0),
        available_margin: Number(d.available_margin ?? 0),
      });
    } catch {
      setM({ allotted_margin: 0, used_margin: 0, available_margin: 0 });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const fmt = (n) => '₹' + Number(n || 0).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  return (
    <div>
      <div style={{ fontSize: '20px', fontWeight: 800, color: 'var(--text)', marginBottom: '14px' }}>Margin</div>
      {loading ? (
        <div style={{ color: 'var(--text)' }}>Loading…</div>
      ) : (
        <>
          <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', marginBottom: '14px' }}>
            <MoneyCard label="Allotted Margin" value={fmt(m.allotted_margin)} />
            <MoneyCard label="Used Margin" value={fmt(m.used_margin)} />
            <MoneyCard label="Available Margin" value={fmt(m.available_margin)} />
          </div>
          <div style={{ color: 'var(--muted)', fontSize: '12px' }}>
            Available Margin = Allotted Margin - Used Margin
          </div>
        </>
      )}
    </div>
  );
}

export default ProfilePage;
