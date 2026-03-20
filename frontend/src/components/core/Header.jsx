import React from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';
import { THEMES, getInitialTheme, setTheme } from '../../utils/theme';
import { usePortalLogo } from '../../hooks/usePortalLogo';

const NAV_ITEMS = [
  { label: 'Trade',       path: '/trade',                          roles: null },
  { label: 'Portfolio',   path: '/portfolio',                      roles: null },
  { label: 'Chart',       path: '/chart',                          roles: null },
  { label: 'Admin Dashboard', path: '/admin-dashboard',            roles: ['ADMIN', 'SUPER_ADMIN'] },
  { label: 'P.MIS',       path: '/trade/all-positions',            roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_positions_mis' },
  { label: 'P.Normal',    path: '/all-positions-normal',           roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_positions_normal' },
  { label: 'P.Userwise',  path: '/all-positions-userwise',         roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_positions_userwise' },
  { label: 'Users',       path: '/users',                          roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_users' },
  { label: 'Payouts',     path: '/payouts',                        roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_payouts' },
  { label: 'Ledger',      path: '/ledger',                         roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_ledger' },
  { label: 'Trade History', path: '/trade-history',                roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_trade_history' },
  { label: 'P&L',         path: '/pandl',                          roles: ['ADMIN', 'SUPER_ADMIN'], permission: 'admin_tab_pnl' },
  { label: 'Dashboard',   path: '/dashboard',                      roles: ['SUPER_ADMIN'] },
];

const Header = () => {
  const { isAuthenticated, user, logout, hasRole, hasPermission } = useAuth();
  const location = useLocation();
  const navigate  = useNavigate();
  const displayFirstName = user?.first_name || (user?.name ? String(user.name).trim().split(/\s+/)[0] : '') || user?.mobile || '';
  const [themeMode, setThemeMode] = React.useState(getInitialTheme());
  const logo = usePortalLogo();

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const handleThemeChange = (mode) => {
    const next = setTheme(mode);
    setThemeMode(next);
  };

  const visibleItems = NAV_ITEMS.filter(item => {
    if (!item.roles) return true;
    const roleAllowed = item.roles.some(r => hasRole(r));
    if (!roleAllowed) return false;
    if (!item.permission) return true;
    return hasPermission(item.permission);
  });

  if (!isAuthenticated) return null;

  return (
    <header className="tn-header" style={{
      background:   'var(--surface)',
      borderBottom: '1px solid var(--border)',
      position:     'sticky',
      top:           0,
      zIndex:        100,
    }}>
      <div className="tn-header-inner">
        {/* Logo */}
        <Link to="/" className="tn-header-logo" style={{ color: 'var(--header-text)' }}>
          {logo
            ? <img src={logo} alt="Logo" style={{ height: '32px', maxWidth: '120px', objectFit: 'contain' }} />
            : 'TN'
          }
        </Link>

        {/* Nav links */}
        <nav className="tn-header-nav">
          {visibleItems.map(item => (
            <Link
              key={item.path}
              to={item.path}
              className={`tn-header-link${location.pathname === item.path ? ' active' : ''}`}
              style={{
                fontWeight:    location.pathname === item.path ? 600 : 400,
                color:         location.pathname === item.path ? 'var(--header-active-text)' : 'var(--header-muted)',
                background:    location.pathname === item.path ? 'var(--header-active-bg, color-mix(in srgb, var(--accent) 12%, transparent))' : 'transparent',
                transition:    'all 0.15s',
              }}
            >
              {item.label}
            </Link>
          ))}
        </nav>

        {/* User + logout */}
        <div className="tn-header-actions">
          <div className="theme-switch" role="group" aria-label="Theme selector">
            {THEMES.map((mode) => (
              <button
                key={mode}
                type="button"
                className={`theme-btn${themeMode === mode ? ' active' : ''}`}
                onClick={() => handleThemeChange(mode)}
              >
                {mode.charAt(0).toUpperCase() + mode.slice(1)}
              </button>
            ))}
          </div>
          <span className="tn-header-user-name" style={{ fontSize: '0.75rem', color: 'var(--header-muted)', whiteSpace: 'nowrap' }}>
            {displayFirstName}
          </span>
          <button
            onClick={() => navigate('/profile')}
            title="Profile"
            style={{
              padding:      '4px 8px',
              background:   'transparent',
              border:       '1px solid var(--border)',
              borderRadius: '5px',
              color:        'var(--header-muted)',
              fontSize:     '0.9rem',
              cursor:       'pointer',
              display:      'flex',
              alignItems:   'center',
              justifyContent: 'center',
              minWidth:     '30px',
              minHeight:    '24px',
            }}
          >
            👤
          </button>
          <button
            onClick={handleLogout}
            style={{
              padding:      '4px 10px',
              background:   'transparent',
              border:       '1px solid var(--border)',
              borderRadius: '5px',
              color:        'var(--header-muted)',
              fontSize:     '0.75rem',
              cursor:       'pointer',
            }}
          >
            Logout
          </button>
        </div>
      </div>
    </header>
  );
};

export default Header;
