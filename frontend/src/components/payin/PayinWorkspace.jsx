import React, { useEffect, useMemo, useState } from 'react';
import { apiService } from '../../services/apiService';

const methods = [
  {
    id: 'link',
    title: 'Link Payment',
    subtitle: 'Direct payment link placeholder',
    badge: 'Fast',
    gradient: 'linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%)',
    icon: 'LINK',
  },
  {
    id: 'upi',
    title: 'UPI Payment',
    subtitle: 'QR placeholder and UPI ID placeholder',
    badge: 'Instant',
    gradient: 'linear-gradient(135deg, #f59e0b 0%, #ef4444 100%)',
    icon: 'UPI',
  },
  {
    id: 'bank',
    title: 'Bank Transfer',
    subtitle: 'Bank account details placeholders',
    badge: 'Direct',
    gradient: 'linear-gradient(135deg, #10b981 0%, #14b8a6 100%)',
    icon: 'BANK',
  },
];

const cardStyle = {
  background: 'var(--surface)',
  border: '1px solid var(--border)',
  borderRadius: 14,
};

const inputStyle = {
  width: '100%',
  padding: '10px 12px',
  borderRadius: 10,
  border: '1px solid var(--border)',
  background: 'var(--surface2)',
  color: 'var(--text)',
  fontSize: 13,
  fontWeight: 600,
};

const defaults = {
  payment_link_url: '',
  upi_qr_url: '',
  upi_id: 'stockmarket.payments@upi',
  upi_merchant_name: 'Stock Market Finance Ltd',
  bank_name: 'Bank Limited',
  bank_holder_name: 'Bank Limited',
  bank_account_holder: 'StockMarket Finance Ltd',
  bank_account_number: '5001234567890',
  bank_ifsc_code: 'HDFC0000123',
  bank_branch: 'HDFCINBB',
};

function PlaceholderRow({ label, value }) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 12,
        padding: '10px 12px',
        borderRadius: 10,
        border: '1px solid var(--border)',
        background: 'var(--surface2)',
      }}
    >
      <div>
        <div style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 700 }}>{label}</div>
        <div style={{ fontSize: 13, color: 'var(--text)', fontWeight: 700 }}>{value}</div>
      </div>
      <button
        type="button"
        style={{
          border: '1px solid var(--border)',
          background: 'var(--surface)',
          color: 'var(--text)',
          borderRadius: 8,
          padding: '6px 10px',
          fontSize: 11,
          fontWeight: 700,
          cursor: 'pointer',
        }}
      >
        Copy
      </button>
    </div>
  );
}

export default function PayinWorkspace({ showHeading = true, mode = 'viewer' }) {
  const [activeMethod, setActiveMethod] = useState('link');
  const [settings, setSettings] = useState(defaults);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const isAdminMode = mode === 'admin';

  useEffect(() => {
    let ignore = false;

    const loadSettings = async () => {
      setLoading(true);
      setError('');
      try {
        const data = await apiService.get('/admin/payin-settings');
        if (!ignore) {
          setSettings({ ...defaults, ...(data || {}) });
        }
      } catch (err) {
        if (!ignore) {
          setError(err?.message || 'Failed to load payin settings');
        }
      } finally {
        if (!ignore) {
          setLoading(false);
        }
      }
    };

    loadSettings();
    return () => {
      ignore = true;
    };
  }, []);

  const paymentUrl = useMemo(() => {
    if (settings.payment_link_url) return settings.payment_link_url;
    if (typeof window === 'undefined') return 'https://your-domain.com/payment-link';
    return `${window.location.origin}/payment-link-placeholder`;
  }, [settings.payment_link_url]);

  const updateField = (field, value) => {
    setSettings((current) => ({ ...current, [field]: value }));
    setMessage('');
    setError('');
  };

  const handleSave = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const response = await apiService.post('/admin/payin-settings', settings);
      setMessage(response?.message || 'Payin settings saved');
    } catch (err) {
      setError(err?.message || 'Failed to save payin settings');
    } finally {
      setSaving(false);
    }
  };

  const handleOpenLink = () => {
    if (typeof window !== 'undefined') {
      window.open(paymentUrl, '_blank', 'noopener,noreferrer');
    }
  };

  const handleCopy = async (value) => {
    if (!value || typeof navigator === 'undefined' || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(value);
      setMessage('Copied to clipboard');
      setError('');
    } catch {
      setError('Copy failed');
    }
  };

  if (loading) {
    return (
      <div style={{ ...cardStyle, padding: 16, color: 'var(--text)', fontWeight: 700 }}>
        Loading payin settings...
      </div>
    );
  }

  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {showHeading && (
        <div style={{ ...cardStyle, padding: '16px 18px' }}>
          <h2 style={{ margin: 0, fontSize: 18, color: 'var(--text)', fontWeight: 800 }}>Payin</h2>
          <p style={{ margin: '6px 0 0 0', fontSize: 12, color: 'var(--muted)' }}>
            {isAdminMode
              ? 'Manage placeholder content for Link Payment, UPI (QR + UPI ID), and Bank Transfer.'
              : 'Pay in using Link Payment, UPI (QR + UPI ID), or Bank Transfer.'}
          </p>
        </div>
      )}

      <div style={{ ...cardStyle, padding: 14 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(220px,1fr))', gap: 10 }}>
          {methods.map((m) => {
            const isActive = activeMethod === m.id;
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => setActiveMethod(m.id)}
                style={{
                  textAlign: 'left',
                  borderRadius: 12,
                  border: isActive ? '1px solid #3b82f6' : '1px solid var(--border)',
                  background: isActive ? 'color-mix(in srgb, #2563eb 16%, var(--surface))' : 'var(--surface)',
                  padding: 12,
                  cursor: 'pointer',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', gap: 8 }}>
                  <div
                    style={{
                      borderRadius: 10,
                      background: m.gradient,
                      color: '#fff',
                      fontWeight: 800,
                      fontSize: 11,
                      padding: '8px 10px',
                      minWidth: 54,
                      textAlign: 'center',
                    }}
                  >
                    {m.icon}
                  </div>
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 800,
                      color: '#dbeafe',
                      background: '#1d4ed8',
                      borderRadius: 999,
                      padding: '4px 8px',
                    }}
                  >
                    {m.badge}
                  </span>
                </div>
                <div style={{ marginTop: 10 }}>
                  <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--text)' }}>{m.title}</div>
                  <div style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>{m.subtitle}</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {activeMethod === 'link' && (
        <div style={{ ...cardStyle, padding: 16 }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 800, color: 'var(--text)' }}>
            {isAdminMode ? 'Inset Payment Link Placeholder' : 'Payment Link'}
          </h3>
          {isAdminMode && (
            <p style={{ margin: '6px 0 12px 0', color: 'var(--muted)', fontSize: 12 }}>
              This maps the reference link-payment block. Keep this URL updated from admin-managed data.
            </p>
          )}
          <div
            style={{
              borderRadius: 12,
              border: '1px dashed #3b82f6',
              background: 'color-mix(in srgb, #3b82f6 8%, var(--surface))',
              padding: 14,
              display: 'grid',
              gap: 10,
            }}
          >
            <label style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 700 }}>Payment Link URL</label>
            <input
              type="text"
              readOnly={!isAdminMode}
              value={paymentUrl}
              onChange={(e) => updateField('payment_link_url', e.target.value)}
              placeholder="https://example.com/pay"
              style={inputStyle}
            />
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                type="button"
                onClick={handleOpenLink}
                style={{
                  borderRadius: 9,
                  border: '1px solid #1d4ed8',
                  background: '#1d4ed8',
                  color: '#fff',
                  padding: '8px 12px',
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                Open Link
              </button>
              <button
                type="button"
                onClick={() => handleCopy(paymentUrl)}
                style={{
                  borderRadius: 9,
                  border: '1px solid var(--border)',
                  background: 'var(--surface)',
                  color: 'var(--text)',
                  padding: '8px 12px',
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: 'pointer',
                }}
              >
                Copy Link
              </button>
            </div>
            {isAdminMode && (
              <div style={{ fontSize: 11, color: 'var(--muted)' }}>
                Save after changing this value to update the Profile Payin view.
              </div>
            )}
          </div>
        </div>
      )}

      {activeMethod === 'upi' && (
        <div style={{ ...cardStyle, padding: 16, display: 'grid', gap: 12 }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 800, color: 'var(--text)' }}>
            {isAdminMode ? 'UPI Placeholder (QR + UPI ID)' : 'UPI Details'}
          </h3>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: 12 }}>
            <div
              style={{
                borderRadius: 12,
                border: '1px solid #fdba74',
                background: 'linear-gradient(135deg, #ffedd5 0%, #fef3c7 100%)',
                padding: 14,
                minHeight: 220,
                display: 'grid',
                placeItems: 'center',
              }}
            >
              {settings.upi_qr_url ? (
                <img
                  src={settings.upi_qr_url}
                  alt="UPI QR"
                  style={{ width: 180, height: 180, borderRadius: 12, objectFit: 'cover', boxShadow: '0 8px 20px rgba(0,0,0,0.12)' }}
                />
              ) : (
                <div
                  style={{
                    width: 180,
                    height: 180,
                    borderRadius: 12,
                    background: 'repeating-linear-gradient(45deg,#111 0,#111 8px,#fff 8px,#fff 16px)',
                    display: 'grid',
                    placeItems: 'center',
                    boxShadow: '0 8px 20px rgba(0,0,0,0.12)',
                  }}
                >
                  <div
                    style={{
                      background: '#111',
                      color: '#fff',
                      fontSize: 10,
                      fontWeight: 800,
                      borderRadius: 999,
                      padding: '5px 9px',
                    }}
                  >
                    QR PLACEHOLDER
                  </div>
                </div>
              )}
            </div>
            <div style={{ display: 'grid', gap: 10, alignContent: 'start' }}>
              <PlaceholderRow label="Merchant UPI ID" value={settings.upi_id} />
              <PlaceholderRow label="Merchant Name" value={settings.upi_merchant_name} />
              {isAdminMode && (
                <>
                  <div style={{ ...cardStyle, padding: 12 }}>
                    <div style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 700 }}>UPI QR Image URL</div>
                    <input
                      type="text"
                      value={settings.upi_qr_url}
                      onChange={(e) => updateField('upi_qr_url', e.target.value)}
                      placeholder="https://example.com/upi-qr.png"
                      style={{ ...inputStyle, marginTop: 8 }}
                    />
                  </div>
                  <div style={{ ...cardStyle, padding: 12 }}>
                    <div style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 700 }}>Merchant UPI ID</div>
                    <input
                      type="text"
                      value={settings.upi_id}
                      onChange={(e) => updateField('upi_id', e.target.value)}
                      placeholder="merchant@upi"
                      style={{ ...inputStyle, marginTop: 8 }}
                    />
                  </div>
                  <div style={{ ...cardStyle, padding: 12 }}>
                    <div style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 700 }}>Merchant Name</div>
                    <input
                      type="text"
                      value={settings.upi_merchant_name}
                      onChange={(e) => updateField('upi_merchant_name', e.target.value)}
                      placeholder="Merchant name"
                      style={{ ...inputStyle, marginTop: 8 }}
                    />
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}

      {activeMethod === 'bank' && (
        <div style={{ ...cardStyle, padding: 16, display: 'grid', gap: 12 }}>
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 800, color: 'var(--text)' }}>
            {isAdminMode ? 'Bank Account Placeholders' : 'Bank Account Details'}
          </h3>
          <div style={{ display: 'grid', gap: 8 }}>
            <PlaceholderRow label="Bank Name" value={settings.bank_name} />
            <PlaceholderRow label="Holder Name" value={settings.bank_holder_name} />
            <PlaceholderRow label="Account Holder" value={settings.bank_account_holder} />
            <PlaceholderRow label="Account Number" value={settings.bank_account_number} />
            <PlaceholderRow label="IFSC Code" value={settings.bank_ifsc_code} />
            <PlaceholderRow label="Branch" value={settings.bank_branch} />
          </div>
          {isAdminMode && (
            <div style={{ ...cardStyle, padding: 12 }}>
              <div style={{ fontSize: 11, color: 'var(--muted)', fontWeight: 700 }}>Bank Details</div>
              <div style={{ display: 'grid', gap: 8, marginTop: 8 }}>
                <input type="text" value={settings.bank_name} onChange={(e) => updateField('bank_name', e.target.value)} placeholder="Bank name" style={inputStyle} />
                <input type="text" value={settings.bank_holder_name} onChange={(e) => updateField('bank_holder_name', e.target.value)} placeholder="Holder name" style={inputStyle} />
                <input type="text" value={settings.bank_account_holder} onChange={(e) => updateField('bank_account_holder', e.target.value)} placeholder="Account holder" style={inputStyle} />
                <input type="text" value={settings.bank_account_number} onChange={(e) => updateField('bank_account_number', e.target.value)} placeholder="Account number" style={inputStyle} />
                <input type="text" value={settings.bank_ifsc_code} onChange={(e) => updateField('bank_ifsc_code', e.target.value)} placeholder="IFSC code" style={inputStyle} />
                <input type="text" value={settings.bank_branch} onChange={(e) => updateField('bank_branch', e.target.value)} placeholder="Branch" style={inputStyle} />
              </div>
            </div>
          )}
        </div>
      )}

      {error && (
        <div style={{ ...cardStyle, padding: '12px 14px', color: '#fca5a5', fontSize: 12, fontWeight: 700 }}>
          {error}
        </div>
      )}

      {message && (
        <div style={{ ...cardStyle, padding: '12px 14px', color: '#86efac', fontSize: 12, fontWeight: 700 }}>
          {message}
        </div>
      )}

      {isAdminMode && (
        <div style={{ ...cardStyle, padding: 16, display: 'flex', justifyContent: 'flex-end' }}>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            style={{
              borderRadius: 10,
              border: '1px solid #1d4ed8',
              background: saving ? '#1e3a8a' : '#1d4ed8',
              color: '#fff',
              padding: '10px 16px',
              fontSize: 13,
              fontWeight: 800,
              cursor: saving ? 'wait' : 'pointer',
              opacity: saving ? 0.8 : 1,
            }}
          >
            {saving ? 'Saving...' : 'Save Payin Settings'}
          </button>
        </div>
      )}
    </div>
  );
}
