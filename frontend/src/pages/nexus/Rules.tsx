import React, { useMemo, useState } from 'react';
import { ShieldCheck, AlertTriangle, CalendarClock, HandCoins, LineChart, TrendingUp, Wallet, Gauge } from 'lucide-react';
import '../../styles/nexus/NeoTheme.css';
import { usePortalLogo } from '../../hooks/usePortalLogo';

const FUNDED_URL = '/funded';

const Rules: React.FC = () => {
  const logo = usePortalLogo();
  const [walletPayInInput, setWalletPayInInput] = useState('');

  const walletPayIn = useMemo(() => {
    const numeric = Number(walletPayInInput.replace(/[^\d]/g, ''));
    return Number.isFinite(numeric) ? numeric : 0;
  }, [walletPayInInput]);

  const straddlyCapital = walletPayIn * 4;
  const marginAllotedForTrading = walletPayIn + straddlyCapital;

  const formatInr = (value: number) => `₹${value.toLocaleString('en-IN')}`;

  const handleWalletPayInChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const raw = event.target.value.replace(/[^\d]/g, '');
    setWalletPayInInput(raw);
  };

  return (
    <div className="rules-page">
      <div className="rules-container">

        <nav className="rules-nav">
          <div className="rules-logo-wrap">
            {logo ? <img src={logo} alt="straddly" className="rules-logo-img" /> : null}
            <div className="rules-logo">straddly</div>
          </div>
          <div className="rules-nav-links">
            <a href="/">Home</a>
            <a href="/funded">Funded Program</a>
            <a href="/rules">Trading Rules</a>
          </div>
        </nav>

        <section className="rules-hero">
          <div className="rules-hero-badge">
            <span className="rules-badge-dot" aria-hidden="true" />
            Live Capital Allocation Preview
          </div>
          <h1>Trading Rules &amp; <span className="rules-gradient">Risk Framework</span></h1>
          <p>
            straddly provides additional trading capital to traders while maintaining
            strict risk controls to ensure stability of the system. The following framework
            governs how funded trading accounts operate.
          </p>
        </section>

        <div className="rules-sections">

          <section className="rules-card">
            <h2><Wallet className="rules-section-icon" /> Capital Allocation Model</h2>
            <p className="rules-muted">
              straddly enables traders to trade with higher capital by allocating
              additional funds to their account.
            </p>
            <div className="rules-allocation-grid">
              <div className="rules-alloc-box">
                <div className="rules-alloc-title-wrap">
                  <span className="rules-icon rules-icon-wallet" aria-hidden="true">₹</span>
                  <h4>Trader Wallet Pay-In</h4>
                </div>
                <label htmlFor="wallet-pay-in" className="rules-input-label">Enter Amount</label>
                <input
                  id="wallet-pay-in"
                  className="rules-payin-input"
                  type="text"
                  inputMode="numeric"
                  placeholder="Enter amount"
                  value={walletPayInInput}
                  onChange={handleWalletPayInChange}
                />
                <p className="rules-amount">{formatInr(walletPayIn)}</p>
              </div>
              <div className="rules-alloc-box">
                <div className="rules-alloc-title-wrap">
                  <span className="rules-icon rules-icon-boost" aria-hidden="true">⚡</span>
                  <h4>straddly Capital</h4>
                </div>
                <p className="rules-ratio">1:5 Capital Structure</p>
                <p className="rules-amount">{formatInr(straddlyCapital)}</p>
              </div>
              <div className="rules-alloc-box rules-alloc-highlight">
                <div className="rules-alloc-title-wrap">
                  <span className="rules-icon rules-icon-margin" aria-hidden="true">◎</span>
                  <h4>Margin Alloted for Trading</h4>
                </div>
                <p className="rules-amount rules-gradient">{formatInr(marginAllotedForTrading)}</p>
              </div>
            </div>
          </section>

          <section className="rules-card rules-marketing-card">
            <div className="rules-marketing-glow" aria-hidden="true" />
            <div className="rules-marketing-head">
              <span className="rules-marketing-icon" aria-hidden="true">◆</span>
              <h2>Capital Acceleration for High-Conviction Traders</h2>
            </div>
            <p className="rules-muted rules-marketing-copy">
              At straddly, we identify disciplined and high-potential participants through
              structured evaluation. Eligible selected traders can unlock up to <strong>400% additional capital</strong>,
              creating the opportunity to scale strategy execution with stronger position size,
              better compounding potential, and institutional-grade growth support.
            </p>
            <p className="rules-marketing-hook">
              Trade with precision. Qualify with consistency. Scale with straddly capital.
            </p>
          </section>

          <section className="rules-card">
            <h2><ShieldCheck className="rules-section-icon" /> Risk Monitoring (RMS)</h2>
            <p className="rules-muted">
              straddly maintains a dedicated Risk Management System (RMS) team that
              continuously monitors trading activity. The objective of RMS monitoring is
              to maintain trading discipline and ensure that risk exposure remains within
              acceptable limits.
            </p>
          </section>

          <section className="rules-card">
            <h2><AlertTriangle className="rules-section-icon" /> Maximum Loss Protection</h2>
            <p className="rules-muted">
              A strict system-level stop loss is enforced to protect trading capital. If
              the mark-to-market (MTM) loss on live positions reaches{' '}
              <strong>50% of the trader's base capital</strong>, positions may be
              automatically squared off by the system.
            </p>
          </section>

          <section className="rules-card">
            <h2><Gauge className="rules-section-icon" /> Capital Stability During the Month</h2>
            <p className="rules-muted">
              Once capital allocation is provided at the beginning of the month, it will
              not be reduced during the same month even if losses occur. Adjustments, if
              any, are applied only during the settlement cycle.
            </p>
          </section>

          <section className="rules-card">
            <h2><CalendarClock className="rules-section-icon" /> Monthly Settlement</h2>
            <p className="rules-muted">
              Final settlement of profits and losses for funded trading accounts is
              calculated on the <strong>last Thursday of every month.</strong>{' '}
              Withdrawals between settlement cycles are not permitted.
            </p>
          </section>

          <section className="rules-card">
            <h2><TrendingUp className="rules-section-icon" /> Profit Policy</h2>
            <p className="rules-muted">
              straddly does not operate on a profit sharing model. Traders retain
              the profits generated through their trading activities.
            </p>
          </section>

          <section className="rules-card">
            <h2><HandCoins className="rules-section-icon" /> Platform Charges</h2>
            <p className="rules-muted">
              straddly charges a platform fee of{' '}
              <strong>0.005 × turnover</strong> for facilitating trading on the system.
            </p>
            <p className="rules-muted" style={{ marginTop: '16px' }}>
              If no trading activity occurs during a month, no platform charges are applied.
            </p>
          </section>

        </div>

        <section className="rules-cta-section">
          <h2>Start Trading With Higher Capital</h2>
          <a className="rules-cta" href={FUNDED_URL}>
            <LineChart size={16} />
            View Funded Program
          </a>
        </section>

        <footer className="rules-footer">straddly © {new Date().getFullYear()}</footer>
      </div>
    </div>
  );
};

export default Rules;
