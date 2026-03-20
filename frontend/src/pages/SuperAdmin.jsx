import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { apiService } from '../services/apiService';
import { useAuthSettings } from '../hooks/useAuthSettings';
import { useAuth } from '../contexts/AuthContext';
import SystemMonitoring from '../components/SystemMonitoring';
import PayinWorkspace from '../components/payin/PayinWorkspace';
import { ADMIN_DASHBOARD_TABS } from '../constants/adminDashboardTabs';

// ── helpers ──────────────────────────────────────────────────────────────────
const API = '/api/v2';
const req = (path, opts = {}) => {
  const token = localStorage.getItem('authToken');
  const headers = {
    'Content-Type': 'application/json',
    ...(token && { 'X-AUTH': token }),
    ...opts.headers,
  };
  return fetch(`${API}${path}`, { ...opts, headers });
};

const DAYS_OF_WEEK = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
const EXCHANGES    = ['NSE', 'BSE', 'MCX'];

const defaultMarketConfig = () => ({
  NSE: { open: '09:15', close: '15:30', days: [0, 1, 2, 3, 4] },
  BSE: { open: '09:15', close: '15:30', days: [0, 1, 2, 3, 4] },
  MCX: { open: '09:00', close: '23:55', days: [0, 1, 2, 3, 4] },
});

const defaultSmsOtpSettings = () => ({
  message_central_customer_id: 'C-44071166CC38423',
  message_central_password: 'Allalone@01',
  otp_expiry_seconds: 180,
  otp_resend_cooldown_seconds: 300,
  otp_max_attempts: 5,
});

const TABS = [
  { id: 'settings',  label: 'Settings & Monitoring' },
  { id: 'adminAccess', label: 'Admin Access' },
  { id: 'payin', label: 'Payin' },
  { id: 'marketData', label: 'Market Data Connection' },
  { id: 'smsOtp', label: 'SMS OTP Settings' },
  { id: 'authCheck', label: 'User Auth Check' },
  { id: 'historic',  label: 'Historic Position' },
  { id: 'courseEnrollments', label: 'Course Enrollments' },
  { id: 'userSignups', label: 'User Signups' },
  { id: 'schedulers', label: 'Schedulers' },
  { id: 'detailedLogs', label: 'Detailed Logs' },
];

const TAB_PERMISSION_MAP = {
  payin: 'admin_tab_payin',
  detailedLogs: 'admin_tab_detailed_logs',
  smsOtp: 'admin_tab_sms_otp_settings',
  courseEnrollments: 'admin_tab_course_enrollments',
  userSignups: 'admin_tab_user_signups',
};

// ── Row components ────────────────────────────────────────────────────────────
const FormField = ({ label, children }) => (
  <div className="flex flex-col gap-1">
    <label className="text-xs font-medium text-gray-400">{label}</label>
    {children}
  </div>
);

const ConfirmModal = ({ open, title, message, onConfirm, onCancel }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-xl border border-zinc-700 bg-zinc-900 p-5 shadow-2xl">
        <h3 className="text-sm font-semibold text-zinc-100">{title}</h3>
        <p className="mt-2 whitespace-pre-line text-xs text-zinc-300">{message}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg border border-zinc-600 bg-zinc-800 px-3 py-2 text-xs font-medium text-zinc-200 hover:bg-zinc-700"
          >
            No
          </button>
          <button
            onClick={onConfirm}
            className="rounded-lg bg-red-600 px-3 py-2 text-xs font-medium text-white hover:bg-red-500"
          >
            Yes
          </button>
        </div>
      </div>
    </div>
  );
};

const inputCls = 'sa-input w-full px-3 py-2 text-sm border rounded-lg outline-none focus:border-blue-500';
const btnCls   = (color = 'blue') => `px-4 py-2 rounded-lg font-medium transition-colors text-zinc-100 text-sm ${
  color === 'blue'   ? 'bg-blue-600   hover:bg-blue-500   disabled:bg-blue-900'   :
  color === 'red'    ? 'bg-red-600    hover:bg-red-500    disabled:bg-red-900'    :
  color === 'green'  ? 'bg-green-600  hover:bg-green-500  disabled:bg-green-900'  :
  color === 'yellow' ? 'bg-yellow-600 hover:bg-yellow-500 disabled:bg-yellow-900' :
  color === 'indigo' ? 'bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-900' :
  color === 'purple' ? 'bg-purple-600 hover:bg-purple-500 disabled:bg-purple-900' :
  'bg-gray-600 hover:bg-gray-500 disabled:bg-gray-900'
}`;

// ── User Detail Modal ─────────────────────────────────────────────────────────
const UserDetailModal = ({ user, type, onClose }) => {
  if (!user) return null;

  const parseIp = (ipDetails) => {
    if (!ipDetails) return null;
    const s = String(ipDetails).trim();
    if (s.startsWith('{') || s.startsWith('[')) {
      try { return JSON.parse(s); } catch {}
    }
    return { ip: s };
  };

  const ipInfo = parseIp(user.ip_details);

  const Field = ({ label, value }) => {
    if (value === null || value === undefined || value === '') return null;
    return (
      <div>
        <div className="text-xs text-zinc-400">{label}</div>
        <div className="text-sm text-zinc-100 mt-0.5 break-all">{String(value)}</div>
      </div>
    );
  };

  const BoolField = ({ label, value }) => (
    <div>
      <div className="text-xs text-zinc-400">{label}</div>
      <div className={`text-sm mt-0.5 font-semibold ${value ? 'text-green-400' : 'text-zinc-500'}`}>{value ? 'Yes' : 'No'}</div>
    </div>
  );

  const statusColors = {
    PENDING:  'bg-yellow-900/30 text-yellow-300 border-yellow-700/40',
    APPROVED: 'bg-green-900/30  text-green-300  border-green-700/40',
    REJECTED: 'bg-red-900/30    text-red-300    border-red-700/40',
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-5 border-b border-zinc-700 sticky top-0 bg-zinc-900">
          <div>
            <h3 className="text-base font-semibold text-zinc-100">{user.name || '—'}</h3>
            <p className="text-xs text-zinc-400 mt-0.5">{type === 'signup' ? 'User Signup Application' : 'Course Enrollment'}</p>
          </div>
          <div className="flex items-center gap-3">
            {type === 'signup' && (
              <span className={`px-2 py-1 rounded-full text-xs font-semibold border ${statusColors[user.status] || 'bg-zinc-800 text-zinc-300 border-zinc-600'}`}>
                {user.status || 'PENDING'}
              </span>
            )}
            <button
              onClick={onClose}
              className="text-zinc-400 hover:text-zinc-100 p-1.5 rounded-lg hover:bg-zinc-800 text-lg leading-none"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="p-5 space-y-5">
          {/* Personal Information */}
          <div>
            <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">Personal Information</h4>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Full Name" value={user.name} />
              {type === 'signup' && <>
                <Field label="First Name" value={user.first_name} />
                <Field label="Middle Name" value={user.middle_name} />
                <Field label="Last Name" value={user.last_name} />
              </>}
              <Field label="Email" value={user.email} />
              <Field label="Mobile" value={user.mobile} />
              <Field label="City" value={user.city} />
              {type === 'signup' && <>
                <Field label="Address" value={user.address} />
                <Field label="State" value={user.state} />
                <Field label="Country" value={user.country} />
              </>}
              {type === 'enrollment' && <>
                <Field label="Experience Level" value={user.experience_level} />
                <Field label="Interest" value={user.interest} />
              </>}
            </div>
            {type === 'enrollment' && user.learning_goal && (
              <div className="mt-3">
                <div className="text-xs text-zinc-400">Learning Goal</div>
                <div className="text-sm text-zinc-100 mt-0.5">{user.learning_goal}</div>
              </div>
            )}
          </div>

          {/* Verification */}
          <div>
            <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">Verification Status</h4>
            <div className="grid grid-cols-2 gap-3">
              <BoolField label="SMS Verified" value={user.sms_verified} />
              <BoolField label="Email Verified" value={user.email_verified} />
            </div>
          </div>

          {/* KYC / Financial (signups only) */}
          {type === 'signup' && (
            <div>
              <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">KYC & Financial Details</h4>
              <div className="grid grid-cols-2 gap-3">
                <Field label="PAN Number" value={user.pan_number} />
                <Field label="Aadhar Number" value={user.aadhar_number} />
                <Field label="Bank Account Number" value={user.bank_account_number} />
                <Field label="IFSC Code" value={user.ifsc} />
                <Field label="UPI ID" value={user.upi_id} />
              </div>
            </div>
          )}

          {/* IP & Network Details */}
          <div>
            <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">IP & Network Details</h4>
            {user.ip_details ? (
              <div className="bg-zinc-800 rounded-lg p-3 border border-zinc-700">
                {ipInfo && typeof ipInfo === 'object' ? (
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    {Object.entries(ipInfo).map(([key, val]) =>
                      val !== null && val !== undefined && val !== '' ? (
                        <div key={key}>
                          <span className="text-zinc-400 capitalize">{key.replace(/_/g, ' ')}: </span>
                          <span className="text-zinc-100 break-all">{String(val)}</span>
                        </div>
                      ) : null
                    )}
                  </div>
                ) : (
                  <div className="text-sm text-zinc-100 break-all">{user.ip_details}</div>
                )}
              </div>
            ) : (
              <div className="text-sm text-zinc-500">No IP information recorded</div>
            )}
          </div>

          {/* Application Status (signups only) */}
          {type === 'signup' && (
            <div>
              <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">Application Status</h4>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <div className="text-xs text-zinc-400">Status</div>
                  <div className="mt-1">
                    <span className={`px-2 py-1 rounded-full text-xs font-semibold border ${statusColors[user.status] || 'bg-zinc-800 text-zinc-300 border-zinc-600'}`}>
                      {user.status || 'PENDING'}
                    </span>
                  </div>
                </div>
                <Field label="Rejection Reason" value={user.rejection_reason} />
                <Field label="Reviewed At" value={user.reviewed_at ? new Date(user.reviewed_at).toLocaleString() : null} />
              </div>
            </div>
          )}

          {/* Timestamps */}
          <div>
            <h4 className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-3">Timestamps</h4>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Registered At" value={user.created_at ? new Date(user.created_at).toLocaleString() : null} />
              <Field label="Last Updated" value={user.updated_at ? new Date(user.updated_at).toLocaleString() : null} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

// ── Main component ─────────────────────────────────────────────────────────────
const SuperAdminDashboard = () => {
  const { hasRole, hasPermission } = useAuth();
  const isSuperAdmin = hasRole('SUPER_ADMIN');
  const [activeTab, setActiveTab] = useState('settings');

  const availableTabs = useMemo(() => {
    if (isSuperAdmin) return TABS;
    return TABS.filter((tab) => {
      const permission = TAB_PERMISSION_MAP[tab.id];
      return permission ? hasPermission(permission) : false;
    });
  }, [isSuperAdmin, hasPermission]);

  const defaultTabId = availableTabs[0]?.id || '';

  const getRequestedTabFromUrl = useCallback(() => {
    try {
      const params = new URLSearchParams(window.location.search || '');
      return (params.get('tab') || '').trim();
    } catch {
      return '';
    }
  }, []);

  const handleTabChange = useCallback((tabId) => {
    setActiveTab(tabId);
    try {
      const url = new URL(window.location.href);
      url.searchParams.set('tab', tabId);
      window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
    } catch {
      // no-op: query sync is best-effort only
    }
  }, []);

  useEffect(() => {
    if (availableTabs.length === 0) {
      if (activeTab !== '') setActiveTab('');
      return;
    }

    const requestedTab = getRequestedTabFromUrl();
    const allowedTabIds = new Set(availableTabs.map((tab) => tab.id));
    const nextTab = requestedTab && allowedTabIds.has(requestedTab)
      ? requestedTab
      : (allowedTabIds.has(activeTab) ? activeTab : defaultTabId);

    if (nextTab && nextTab !== activeTab) {
      setActiveTab(nextTab);
    }
  }, [availableTabs, activeTab, defaultTabId, getRequestedTabFromUrl]);

  // ── Admin access controls ──
  const [adminAccessUsers, setAdminAccessUsers] = useState([]);
  const [adminAccessUsersLoading, setAdminAccessUsersLoading] = useState(false);
  const [selectedAdminAccessUserId, setSelectedAdminAccessUserId] = useState('');
  const [selectedAdminPermissions, setSelectedAdminPermissions] = useState(new Set());
  const [adminAccessLoading, setAdminAccessLoading] = useState(false);
  const [adminAccessSaving, setAdminAccessSaving] = useState(false);
  const [adminAccessError, setAdminAccessError] = useState('');
  const [adminAccessMsg, setAdminAccessMsg] = useState('');

  // ── User detail modal ──
  const [selectedUser, setSelectedUser]     = useState(null);
  const [selectedUserType, setSelectedUserType] = useState(null); // 'signup' | 'enrollment'

  // ── Auth settings ──
  const { localSettings, setLocalSettings, saved, loading: authLoading, isSaving, saveSettings, switchMode } = useAuthSettings();

  // ── Master loading ──
  const [masterLoading, setMasterLoading] = useState(false);
  const [masterMsg, setMasterMsg]         = useState('');

  // ── Market config ──
  const [marketConfig, setMarketConfig] = useState(defaultMarketConfig());
  const [mcError, setMcError]           = useState('');

  // ── SMS OTP settings ──
  const [smsOtpSettings, setSmsOtpSettings] = useState(defaultSmsOtpSettings());
  const [smsOtpLoading, setSmsOtpLoading] = useState(false);
  const [smsOtpSaving, setSmsOtpSaving] = useState(false);
  const [smsOtpMessage, setSmsOtpMessage] = useState('');
  const [smsOtpError, setSmsOtpError] = useState('');

  // ── User auth check ──
  const [authCheckIdentifier, setAuthCheckIdentifier] = useState('');
  const [authCheckPassword, setAuthCheckPassword]     = useState('');
  const [authCheckLoading, setAuthCheckLoading]       = useState(false);
  const [authCheckResult, setAuthCheckResult]         = useState(null);
  const [authCheckError, setAuthCheckError]           = useState('');

  // ── Soft delete user ──
  const [deleteUserSelection, setDeleteUserSelection] = useState('');
  const [deleteUsersLoading, setDeleteUsersLoading]   = useState(false);
  const [deleteUsersError, setDeleteUsersError]       = useState('');
  const [deleteUsersMsg, setDeleteUsersMsg]           = useState('');
  const [archivedUsers, setArchivedUsers]             = useState([]);
  const [archivedUsersLoading, setArchivedUsersLoading] = useState(false);

  // ── Delete user positions ──
  const [deletePositionsUserSelection, setDeletePositionsUserSelection] = useState('');
  const [deletePositionsLoading, setDeletePositionsLoading] = useState(false);
  const [deletePositionsError, setDeletePositionsError] = useState('');
  const [deletePositionsMsg, setDeletePositionsMsg]   = useState('');
  const [userPositionsList, setUserPositionsList] = useState(null);
  const [selectedPositionIds, setSelectedPositionIds] = useState(new Set());
  const [loadingUserPositions, setLoadingUserPositions] = useState(false);

  // ── Course enrollments ──
  const [courseEnrollments, setCourseEnrollments] = useState([]);
  const [courseEnrollmentsLoading, setCourseEnrollmentsLoading] = useState(false);
  const [courseEnrollmentsError, setCourseEnrollmentsError] = useState('');
  const [courseEnrollmentsTotal, setCourseEnrollmentsTotal] = useState(0);

  // ── User signups ──
  const [portalUsers, setPortalUsers]             = useState([]);
  const [portalUsersLoading, setPortalUsersLoading] = useState(false);
  const [portalUsersError, setPortalUsersError]   = useState('');
  const [portalUsersTotal, setPortalUsersTotal]   = useState(0);
  const [selectedPortalUserIds, setSelectedPortalUserIds] = useState(new Set());
  const [portalUsersDeleteLoading, setPortalUsersDeleteLoading] = useState(false);
  const [portalUsersDeleteMsg, setPortalUsersDeleteMsg] = useState('');
  const [portalUsersStatus, setPortalUsersStatus] = useState('PENDING');
  const [portalActionBusyId, setPortalActionBusyId] = useState(null);
  const [portalSignupActivity, setPortalSignupActivity] = useState([]);
  const [portalSignupActivityLoading, setPortalSignupActivityLoading] = useState(false);

  // ── Activity / Detailed Logs ──
  const [activityLogs, setActivityLogs]           = useState([]);
  const [activityLogsLoading, setActivityLogsLoading] = useState(false);
  const [activityLogsError, setActivityLogsError] = useState('');
  const [activityLogsTotal, setActivityLogsTotal] = useState(0);
  const [activityLogsPage, setActivityLogsPage]   = useState(0);
  const [activityLogsFilters, setActivityLogsFilters] = useState(() => {
    const now = new Date();
    const threeDaysAgo = new Date(now.getTime() - 3 * 24 * 60 * 60 * 1000);
    return {
      from_date: threeDaysAgo.toISOString().slice(0, 16),
      to_date: now.toISOString().slice(0, 16),
      action_type: '', role: '', search: '', ip: '',
    };
  });
  const LOGS_PAGE_SIZE = 50;

  // ── Backdate position ──
  const [backdateForm, setBackdateForm]     = useState({ user_id: '', symbol: '', qty: '', price: '', trade_date: '', trade_time: '09:15', instrument_type: 'EQ', exchange: 'NSE', product_type: 'MIS' });
  const [backdateLoading, setBackdateLoading] = useState(false);
  const [backdateError, setBackdateError]   = useState('');
  const [backdateMsg, setBackdateMsg]       = useState('');
  const [backdateResult, setBackdateResult] = useState(null);
  const [symbolInputBlur, setSymbolInputBlur] = useState(false);
  const [instrumentSelectedFromDropdown, setInstrumentSelectedFromDropdown] = useState(false);

  // ── Force exit ──
  const [forceExitForm, setForceExitForm]     = useState({ user_id: '', position_id: '', exit_price: '', exit_time: '15:30', exit_date: '' });
  const [forceExitLoading, setForceExitLoading] = useState(false);
  const [forceExitError, setForceExitError]   = useState('');
  const [forceExitMsg, setForceExitMsg]       = useState('');
  const [forceExitResult, setForceExitResult] = useState(null);

  // ── Instrument autocomplete ──
  const [instrumentSuggestions, setInstrumentSuggestions] = useState([]);

  // ── Dhan connection ──
  const [dhanStatus,    setDhanStatus]    = useState(null);
  const [isConnecting,  setIsConnecting]  = useState(false);
  const [connectMsg,    setConnectMsg]    = useState({ text: '', type: '' });
  const [marketAuthStatus, setMarketAuthStatus] = useState(null);
  const [authSwitchLoading, setAuthSwitchLoading] = useState(false);
  const [authSwitchMsg, setAuthSwitchMsg] = useState({ text: '', type: '' });
  const [authSelfTestLoading, setAuthSelfTestLoading] = useState(false);
  const [authSelfTestResult, setAuthSelfTestResult] = useState(null);
  const [authSelfTestError, setAuthSelfTestError] = useState('');

  // ── Scheduler dashboard ──
  const [schedSnapshot, setSchedSnapshot] = useState(null);
  const [schedLoading, setSchedLoading]   = useState(false);
  const [schedError, setSchedError]       = useState('');
  const [schedWorking, setSchedWorking]   = useState(null);

  // ── Logo upload ──
  const [logoFile, setLogoFile]         = useState(null);
  const [logoPreview, setLogoPreview]   = useState(null);
  const [logoUploading, setLogoUploading] = useState(false);
  const [logoMsg, setLogoMsg]           = useState('');
  const [currentLogo, setCurrentLogo]   = useState(null);

  // ── Option Chain Controls ──
  const [ocAtmLoading,     setOcAtmLoading]     = useState(false);
  const [ocAtmResult,      setOcAtmResult]      = useState(null);
  const [ocRebuildLoading, setOcRebuildLoading] = useState(false);
  const [ocRebuildResult,  setOcRebuildResult]  = useState(null);

  // ── Expiry Rollover ──
  const [expiryRolloverLoading, setExpiryRolloverLoading] = useState(false);
  const [expiryRolloverResult, setExpiryRolloverResult] = useState(null);

  // ── Save error ──
  const [saveError, setSaveError] = useState('');
  const [confirmDialog, setConfirmDialog] = useState({
    open: false,
    title: '',
    message: '',
    onConfirm: null,
  });

  const askConfirm = (title, message, onConfirm) => {
    setConfirmDialog({ open: true, title, message, onConfirm });
  };

  const closeConfirm = () => {
    setConfirmDialog({ open: false, title: '', message: '', onConfirm: null });
  };

  const runConfirmedAction = async () => {
    const fn = confirmDialog.onConfirm;
    closeConfirm();
    if (typeof fn === 'function') {
      await fn();
    }
  };

  // ── Fetch Dhan connection status ──
  const fetchDhanStatus = useCallback(async () => {
    try {
      const res = await req('/admin/dhan/status');
      if (res.ok) setDhanStatus(await res.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchDhanStatus();
    const id = setInterval(fetchDhanStatus, 5000);
    return () => clearInterval(id);
  }, [fetchDhanStatus]);

  const fetchMarketAuthStatus = useCallback(async () => {
    try {
      const data = await apiService.get('/admin/auth-status');
      setMarketAuthStatus(data || null);
    } catch {
      // ignore noisy polling errors
    }
  }, []);

  useEffect(() => {
    if (activeTab !== 'marketData') return;
    fetchMarketAuthStatus();
    const id = setInterval(fetchMarketAuthStatus, 5000);
    return () => clearInterval(id);
  }, [activeTab, fetchMarketAuthStatus]);

  // ── Load market config on mount ──
  const fetchMarketConfig = useCallback(async () => {
    try {
      const res = await req('/admin/market-config');
      if (res.ok) { const data = await res.json(); setMarketConfig(data); }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchMarketConfig(); }, [fetchMarketConfig]);

  const fetchSmsOtpSettings = useCallback(async () => {
    setSmsOtpLoading(true);
    setSmsOtpError('');
    try {
      const res = await req('/admin/sms-otp-settings');
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setSmsOtpError(data.detail || 'Failed to load SMS OTP settings');
        return;
      }
      const data = await res.json();
      setSmsOtpSettings({
        message_central_customer_id: data.message_central_customer_id || 'C-44071166CC38423',
        message_central_password: data.message_central_password || 'Allalone@01',
        otp_expiry_seconds: Number(data.otp_expiry_seconds || 180),
        otp_resend_cooldown_seconds: Number(data.otp_resend_cooldown_seconds || 300),
        otp_max_attempts: Number(data.otp_max_attempts || 5),
      });
    } catch (e) {
      setSmsOtpError(e?.message || 'Failed to load SMS OTP settings');
    } finally {
      setSmsOtpLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== 'smsOtp') return;
    fetchSmsOtpSettings();
  }, [activeTab, fetchSmsOtpSettings]);

  const handleOcRecalibrateAtm = async () => {
    setOcAtmLoading(true);
    setOcAtmResult(null);
    try {
      const res = await req('/admin/option-chain/recalibrate-atm', { method: 'POST' });
      const data = await res.json();
      setOcAtmResult(data);
    } catch (e) {
      setOcAtmResult({ success: false, message: e?.message || 'Request failed' });
    } finally {
      setOcAtmLoading(false);
    }
  };

  const handleOcRebuildSkeleton = async () => {
    setOcRebuildLoading(true);
    setOcRebuildResult(null);
    try {
      const res = await req('/admin/option-chain/rebuild-skeleton', { method: 'POST' });
      const data = await res.json();
      setOcRebuildResult(data);
    } catch (e) {
      setOcRebuildResult({ success: false, message: e?.message || 'Request failed' });
    } finally {
      setOcRebuildLoading(false);
    }
  };

  const handleExpiryRollover = async () => {
    setExpiryRolloverLoading(true);
    setExpiryRolloverResult(null);
    try {
      const res = await req('/admin/subscriptions/rollover', { method: 'POST' });
      const data = await res.json();
      setExpiryRolloverResult(data);
    } catch (e) {
      setExpiryRolloverResult({ status: 'error', message: e?.message || 'Request failed' });
    } finally {
      setExpiryRolloverLoading(false);
    }
  };

  const fetchSchedulers = useCallback(async () => {
    setSchedLoading(true);
    setSchedError('');
    try {
      const res = await apiService.get('/admin/schedulers');
      setSchedSnapshot(res);
    } catch (e) {
      setSchedSnapshot(null);
      setSchedError(e?.message || 'Failed to load schedulers');
    } finally {
      setSchedLoading(false);
    }
  }, []);

  useEffect(() => {
    if (activeTab !== 'schedulers') return;
    fetchSchedulers();
    const id = setInterval(fetchSchedulers, 10000);
    return () => clearInterval(id);
  }, [activeTab, fetchSchedulers]);

  const schedulerAction = async (name, action) => {
    setSchedWorking(`${name}:${action}`);
    setSchedError('');
    try {
      await apiService.post(`/admin/schedulers/${encodeURIComponent(name)}/${encodeURIComponent(action)}`, {});
      await fetchSchedulers();
    } catch (e) {
      setSchedError(e?.message || 'Action failed');
    } finally {
      setSchedWorking(null);
    }
  };

  const fetchCourseEnrollments = useCallback(async () => {
    setCourseEnrollmentsLoading(true);
    setCourseEnrollmentsError('');
    try {
      const res = await req('/auth/portal/users');
      if (res.ok) {
        const data = await res.json();
        setCourseEnrollments(data.users || []);
        setCourseEnrollmentsTotal(data.total || 0);
      } else {
        const errData = await res.json().catch(() => ({}));
        setCourseEnrollmentsError(errData.detail || 'Failed to load course enrollments');
      }
    } catch (e) {
      setCourseEnrollmentsError(e?.message || 'Error fetching course enrollments');
    } finally {
      setCourseEnrollmentsLoading(false);
    }
  }, []);

  const fetchActivityLogs = useCallback(async (page = 0, filters = activityLogsFilters) => {
    setActivityLogsLoading(true);
    setActivityLogsError('');
    try {
      const params = new URLSearchParams({ limit: LOGS_PAGE_SIZE, offset: page * LOGS_PAGE_SIZE });
      if (filters.from_date)   params.set('from_date',   filters.from_date);
      if (filters.to_date)     params.set('to_date',     filters.to_date);
      if (filters.action_type) params.set('action_type', filters.action_type);
      if (filters.role)        params.set('role',        filters.role);
      if (filters.search)      params.set('search',      filters.search);
      if (filters.ip)          params.set('ip',          filters.ip);
      const res = await req(`/admin/activity-logs?${params}`);
      if (res.ok) {
        const data = await res.json();
        setActivityLogs(data.items || []);
        setActivityLogsTotal(data.total || 0);
        setActivityLogsPage(page);
      } else {
        const err = await res.json().catch(() => ({}));
        setActivityLogsError(err.detail || 'Failed to load activity logs');
      }
    } catch (e) {
      setActivityLogsError(e?.message || 'Error loading activity logs');
    } finally {
      setActivityLogsLoading(false);
    }
  }, [activityLogsFilters]);

  const fetchPortalUsers = useCallback(async () => {
    setPortalUsersLoading(true);
    setPortalUsersError('');
    setPortalUsersDeleteMsg('');
    try {
      const res = await req(`/auth/portal/user-signups?status=${encodeURIComponent(portalUsersStatus)}`);
      if (res.ok) {
        const data = await res.json();
        setPortalUsers(data.users || []);
        setPortalUsersTotal(data.total || 0);
      } else {
        const errData = await res.json().catch(() => ({}));
        setPortalUsersError(errData.detail || 'Failed to load user signups');
      }
    } catch (e) {
      setPortalUsersError(e?.message || 'Error fetching user signups');
    } finally {
      setPortalUsersLoading(false);
    }
  }, [portalUsersStatus]);

  const fetchPortalSignupActivity = useCallback(async () => {
    setPortalSignupActivityLoading(true);
    try {
      const res = await req('/auth/portal/user-signups/activity?limit=20');
      if (res.ok) {
        const data = await res.json();
        setPortalSignupActivity(data.items || []);
      }
    } catch {
      // Keep the review table usable even if activity fetch fails.
    } finally {
      setPortalSignupActivityLoading(false);
    }
  }, []);

  const refreshPortalSignupPanel = useCallback(async () => {
    await Promise.all([fetchPortalUsers(), fetchPortalSignupActivity()]);
  }, [fetchPortalUsers, fetchPortalSignupActivity]);

  const handlePortalSignupReview = async (signupId, action) => {
    if (!signupId) return;

    let reason = '';
    if (action === 'REJECT') {
      reason = window.prompt('Optional rejection reason:', '') || '';
    } else if (action === 'RESTORE') {
      const shouldRestore = window.confirm('Restore this rejected application back to pending?');
      if (!shouldRestore) return;
    }

    setPortalActionBusyId(signupId);
    setPortalUsersError('');
    setPortalUsersDeleteMsg('');
    try {
      const res = await req(`/auth/portal/user-signups/${signupId}/review`, {
        method: 'POST',
        body: JSON.stringify({ action, reason }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPortalUsersError(data.detail || `Failed to ${action.toLowerCase()} signup`);
      } else {
        setPortalUsersDeleteMsg(data.message || `Signup ${action.toLowerCase()}d successfully`);
        await refreshPortalSignupPanel();
      }
    } catch (e) {
      setPortalUsersError(e?.message || `Failed to ${action.toLowerCase()} signup`);
    } finally {
      setPortalActionBusyId(null);
    }
  };

  useEffect(() => {
    setSelectedPortalUserIds((prev) => {
      if (!prev.size) return prev;
      const validIds = new Set(portalUsers.map((u) => u.id));
      const filtered = new Set([...prev].filter((id) => validIds.has(id)));
      return filtered.size === prev.size ? prev : filtered;
    });
  }, [portalUsers]);

  const togglePortalUserSelection = (userId) => {
    setSelectedPortalUserIds((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  };

  const toggleSelectAllPortalUsers = () => {
    if (!portalUsers.length) return;
    setSelectedPortalUserIds((prev) => {
      if (prev.size === portalUsers.length) {
        return new Set();
      }
      return new Set(portalUsers.map((u) => u.id));
    });
  };

  const handleDeleteSelectedPortalUsers = async () => {
    const ids = Array.from(selectedPortalUserIds);
    if (!ids.length) return;

    if (!window.confirm(`Delete ${ids.length} selected portal signup(s)? This cannot be undone.`)) return;

    setPortalUsersDeleteLoading(true);
    setPortalUsersError('');
    setPortalUsersDeleteMsg('');
    try {
      const res = await req('/auth/portal/users/delete', {
        method: 'POST',
        body: JSON.stringify({ user_ids: ids }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setPortalUsersError(data.detail || 'Failed to delete selected signups');
      } else {
        setPortalUsersDeleteMsg(data.message || `Deleted ${data.deleted || 0} signup(s)`);
        setSelectedPortalUserIds(new Set());
        await fetchPortalUsers();
      }
    } catch (e) {
      setPortalUsersError(e?.message || 'Failed to delete selected signups');
    } finally {
      setPortalUsersDeleteLoading(false);
    }
  };

  const escapeCsv = (value) => {
    const raw = value == null ? '' : String(value);
    return `"${raw.replace(/"/g, '""')}"`;
  };

  const handleExportCourseEnrollmentsCsv = () => {
    if (!courseEnrollments.length) return;
    const headers = [
      'name',
      'email',
      'mobile',
      'city',
      'experience_level',
      'interest',
      'learning_goal',
      'ip_details',
      'sms_verified',
      'email_verified',
      'created_at',
    ];
    const rows = courseEnrollments.map((u) => [
      u.name,
      u.email,
      u.mobile,
      u.city,
      u.experience_level,
      u.interest,
      u.learning_goal,
      u.ip_details,
      u.sms_verified,
      u.email_verified,
      u.created_at,
    ]);
    const csv = [headers, ...rows]
      .map((row) => row.map(escapeCsv).join(','))
      .join('\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    anchor.href = url;
    anchor.download = `course-enrollments-${stamp}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };

  const handleExportPortalUsersCsv = () => {
    if (!portalUsers.length) return;
    const headers = [
      'name',
      'email',
      'mobile',
      'pan_number',
      'aadhar_number',
      'bank_account_number',
      'ifsc',
      'upi_id',
      'city',
      'ip_details',
      'sms_verified',
      'email_verified',
      'status',
      'rejection_reason',
      'created_at',
    ];
    const rows = portalUsers.map((u) => [
      u.name,
      u.email,
      u.mobile,
      u.pan_number,
      u.aadhar_number,
      u.bank_account_number,
      u.ifsc,
      u.upi_id,
      u.city,
      u.ip_details,
      u.sms_verified,
      u.email_verified,
      u.status,
      u.rejection_reason,
      u.created_at,
    ]);
    const csv = [headers, ...rows]
      .map((row) => row.map(escapeCsv).join(','))
      .join('\n');

    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    anchor.href = url;
    anchor.download = `user-signups-${stamp}.csv`;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
    URL.revokeObjectURL(url);
  };

  const loadAdminAccessUsers = useCallback(async () => {
    setAdminAccessUsersLoading(true);
    setAdminAccessError('');
    try {
      const response = await apiService.get('/admin/users');
      const allUsers = Array.isArray(response) ? response : (response?.data || response?.users || []);
      const admins = allUsers.filter((u) => u?.role === 'ADMIN');
      setAdminAccessUsers(admins);

      if (!selectedAdminAccessUserId && admins.length > 0) {
        setSelectedAdminAccessUserId(String(admins[0].id));
      }
    } catch (e) {
      setAdminAccessError(e?.message || 'Failed to load admins');
      setAdminAccessUsers([]);
    } finally {
      setAdminAccessUsersLoading(false);
    }
  }, [selectedAdminAccessUserId]);

  const loadAdminAccessForUser = useCallback(async (adminUserId) => {
    if (!adminUserId) {
      setSelectedAdminPermissions(new Set());
      return;
    }
    setAdminAccessLoading(true);
    setAdminAccessError('');
    setAdminAccessMsg('');
    try {
      const data = await apiService.get(`/admin/admin-access/${adminUserId}`);
      const permissions = Array.isArray(data?.permissions) ? data.permissions : [];
      setSelectedAdminPermissions(new Set(permissions));
    } catch (e) {
      setAdminAccessError(e?.message || 'Failed to load admin access');
      setSelectedAdminPermissions(new Set());
    } finally {
      setAdminAccessLoading(false);
    }
  }, []);

  const toggleAdminPermission = (permission) => {
    setSelectedAdminPermissions((prev) => {
      const next = new Set(prev);
      if (next.has(permission)) next.delete(permission);
      else next.add(permission);
      return next;
    });
  };

  const handleSaveAdminAccess = async () => {
    if (!selectedAdminAccessUserId) {
      setAdminAccessError('Select an admin first.');
      return;
    }
    setAdminAccessSaving(true);
    setAdminAccessError('');
    setAdminAccessMsg('');
    try {
      const permissions = Array.from(selectedAdminPermissions);
      const res = await apiService.post(`/admin/admin-access/${selectedAdminAccessUserId}`, { permissions });
      setAdminAccessMsg(res?.message || 'Admin access saved successfully.');
    } catch (e) {
      setAdminAccessError(e?.message || 'Failed to save admin access');
    } finally {
      setAdminAccessSaving(false);
    }
  };

  // Load admin signup tabs when active
  useEffect(() => {
    if (activeTab === 'courseEnrollments') {
      fetchCourseEnrollments();
    }
  }, [activeTab, fetchCourseEnrollments]);

  useEffect(() => {
    if (activeTab === 'adminAccess') {
      loadAdminAccessUsers();
    }
  }, [activeTab, loadAdminAccessUsers]);

  useEffect(() => {
    if (activeTab === 'adminAccess' && selectedAdminAccessUserId) {
      loadAdminAccessForUser(selectedAdminAccessUserId);
    }
  }, [activeTab, selectedAdminAccessUserId, loadAdminAccessForUser]);

  useEffect(() => {
    if (activeTab === 'userSignups') {
      refreshPortalSignupPanel();
    }
  }, [activeTab, refreshPortalSignupPanel]);

  useEffect(() => {
    if (activeTab === 'detailedLogs') {
      fetchActivityLogs(0, activityLogsFilters);
    }
  }, [activeTab]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Handlers ──
  const handleSave = async () => {
    setSaveError('');
    try { await saveSettings(); } catch (e) { setSaveError(e?.message || 'Save failed'); }
    fetchMarketAuthStatus();
  };

  const handleForceSwitchMode = async () => {
    setAuthSwitchLoading(true);
    setAuthSwitchMsg({ text: '', type: '' });
    try {
      await switchMode(localSettings.authMode, { force: true, dailyToken: localSettings.accessToken });
      setAuthSwitchMsg({ text: `Switched mode to ${localSettings.authMode}.`, type: 'success' });
    } catch (e) {
      setAuthSwitchMsg({ text: e?.message || 'Force switch failed.', type: 'error' });
    } finally {
      setAuthSwitchLoading(false);
      fetchMarketAuthStatus();
      fetchDhanStatus();
    }
  };

  const handleAuthSelfTest = async () => {
    setAuthSelfTestLoading(true);
    setAuthSelfTestError('');
    try {
      const data = await apiService.get('/admin/auth-mode/self-test');
      setAuthSelfTestResult(data || null);
    } catch (e) {
      setAuthSelfTestResult(null);
      setAuthSelfTestError(e?.message || 'Auto test failed.');
    } finally {
      setAuthSelfTestLoading(false);
      fetchMarketAuthStatus();
    }
  };

  const handleConnect = async () => {
    setIsConnecting(true);
    setConnectMsg({ text: '', type: '' });
    try {
      const res = await req('/admin/dhan/connect', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setConnectMsg({ text: data.message || 'Connect initiated.', type: 'success' });
      } else {
        setConnectMsg({ text: data.detail || data.message || 'Connect failed.', type: 'error' });
      }
    } catch (e) {
      setConnectMsg({ text: e?.message || 'Connect failed.', type: 'error' });
    } finally {
      setIsConnecting(false);
      fetchDhanStatus();
    }
  };

  const handleDisconnect = async () => {
    setIsConnecting(true);
    setConnectMsg({ text: '', type: '' });
    try {
      const res = await req('/admin/dhan/disconnect', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setConnectMsg({ text: data.message || 'Disconnected.', type: 'success' });
      } else {
        setConnectMsg({ text: data.detail || data.message || 'Disconnect failed.', type: 'error' });
      }
    } catch (e) {
      setConnectMsg({ text: e?.message || 'Disconnect failed.', type: 'error' });
    } finally {
      setIsConnecting(false);
      fetchDhanStatus();
    }
  };

  const handleLoadInstrumentMaster = async () => {
    setMasterLoading(true); setMasterMsg('');
    try {
      const res = await req('/admin/scrip-master/refresh', { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      setMasterMsg(res.ok ? (data.message || 'Instrument master reloaded.') : (data.detail || 'Failed.'));
    } catch (e) { setMasterMsg(e?.message || 'Error'); } finally { setMasterLoading(false); }
  };

  const saveMarketConfig = async () => {
    setMcError('');
    try {
      const res = await req('/admin/market-config', { method: 'POST', body: JSON.stringify(marketConfig) });
      if (!res.ok) { const d = await res.json().catch(() => ({})); setMcError(d.detail || 'Save failed'); }
    } catch (e) { setMcError(e?.message || 'Error'); }
  };

  const handleSaveSmsOtpSettings = async () => {
    setSmsOtpSaving(true);
    setSmsOtpError('');
    setSmsOtpMessage('');
    try {
      const payload = {
        message_central_customer_id: (smsOtpSettings.message_central_customer_id || '').trim(),
        message_central_password: (smsOtpSettings.message_central_password || '').trim(),
        otp_expiry_seconds: Number(smsOtpSettings.otp_expiry_seconds || 180),
        otp_resend_cooldown_seconds: Number(smsOtpSettings.otp_resend_cooldown_seconds || 300),
        otp_max_attempts: Number(smsOtpSettings.otp_max_attempts || 5),
      };

      if (!payload.message_central_customer_id) {
        setSmsOtpError('Customer ID is required');
        return;
      }
      if (!payload.message_central_password) {
        setSmsOtpError('Password is required');
        return;
      }

      const res = await req('/admin/sms-otp-settings', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setSmsOtpError(data.detail || 'Failed to save SMS OTP settings');
        return;
      }
      setSmsOtpMessage(data.message || 'SMS OTP settings saved');
      await fetchSmsOtpSettings();
    } catch (e) {
      setSmsOtpError(e?.message || 'Failed to save SMS OTP settings');
    } finally {
      setSmsOtpSaving(false);
    }
  };

  const handleUserAuthCheck = async () => {
    if (!authCheckIdentifier) { setAuthCheckError('Enter identifier.'); return; }
    setAuthCheckLoading(true); setAuthCheckResult(null); setAuthCheckError('');
    try {
      const res = await req('/admin/diagnose-login', { method: 'POST', body: JSON.stringify({ identifier: authCheckIdentifier, password: authCheckPassword }) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) setAuthCheckResult(data); else setAuthCheckError(data.detail || 'Check failed');
    } catch (e) { setAuthCheckError(e?.message || 'Error'); } finally { setAuthCheckLoading(false); }
  };

  const handleSoftDeleteUser = async () => {
    if (!deleteUserSelection) { setDeleteUsersError('Select a user.'); return; }
    if (!window.confirm(`⚠️ This will ARCHIVE the user. They cannot login again.\n\nUser: ${deleteUserSelection}\n\nContinue?`)) return;
    
    setDeleteUsersLoading(true); setDeleteUsersError(''); setDeleteUsersMsg('');
    try {
      const res = await req(`/admin/users/${deleteUserSelection}/soft-delete`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setDeleteUsersMsg(data.message || 'User archived successfully');
        setDeleteUserSelection('');
        // Refresh archived list
        fetchArchivedUsers();
      } else {
        setDeleteUsersError(data.detail || 'Soft delete failed');
      }
    } catch (e) { setDeleteUsersError(e?.message || 'Error'); } finally { setDeleteUsersLoading(false); }
  };

  const fetchArchivedUsers = async () => {
    setArchivedUsersLoading(true);
    try {
      const res = await req('/admin/users/archived');
      const data = await res.json().catch(() => ({}));
      if (res.ok) setArchivedUsers(data.archived_users || []);
    } catch (e) { /* ignore */ } finally { setArchivedUsersLoading(false); }
  };

  useEffect(() => {
    if (activeTab === 'authCheck') { fetchArchivedUsers(); }
  }, [activeTab]);

  const handleDeleteUserPositions = async () => {
    if (!deletePositionsUserSelection) { setDeletePositionsError('Select a user.'); return; }
    if (!window.confirm(`⚠️ PERMANENT! All positions, orders, and ledger entries will be DELETED.\n\nUser: ${deletePositionsUserSelection}\n\nThis cannot be undone!\n\nContinue?`)) return;
    
    setDeletePositionsLoading(true); setDeletePositionsError(''); setDeletePositionsMsg('');
    try {
      const res = await req(`/admin/users/${deletePositionsUserSelection}/positions/delete-all`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setDeletePositionsMsg(data.message || 'Positions deleted successfully');
        setDeletePositionsUserSelection('');
        setUserPositionsList(null);
        setSelectedPositionIds(new Set());
      } else {
        setDeletePositionsError(data.detail || 'Position deletion failed');
      }
    } catch (e) { setDeletePositionsError(e?.message || 'Error'); } finally { setDeletePositionsLoading(false); }
  };

  const handleLoadUserPositions = async () => {
    if (!deletePositionsUserSelection) { setDeletePositionsError('Select a user.'); return; }
    setLoadingUserPositions(true); setDeletePositionsError('');
    try {
      const res = await req(`/admin/users/${deletePositionsUserSelection}/positions`);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setUserPositionsList(data);
        setSelectedPositionIds(new Set());
      } else {
        setDeletePositionsError(data.detail || 'Failed to load positions');
      }
    } catch (e) { setDeletePositionsError(e?.message || 'Error'); } finally { setLoadingUserPositions(false); }
  };

  const handleDeleteSpecificPositions = async () => {
    if (selectedPositionIds.size === 0) { setDeletePositionsError('Select at least one position.'); return; }
    if (!window.confirm(`⚠️ PERMANENT! Delete ${selectedPositionIds.size} selected position(s)?\n\nThis cannot be undone!\n\nContinue?`)) return;
    
    setDeletePositionsLoading(true); setDeletePositionsError(''); setDeletePositionsMsg('');
    try {
      const res = await req(`/admin/users/${deletePositionsUserSelection}/positions/delete-specific`, {
        method: 'POST',
        body: JSON.stringify({ position_ids: Array.from(selectedPositionIds) })
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setDeletePositionsMsg(data.message || 'Positions deleted successfully');
        setSelectedPositionIds(new Set());
        // Reload positions
        await handleLoadUserPositions();
      } else {
        setDeletePositionsError(data.detail || 'Position deletion failed');
      }
    } catch (e) { setDeletePositionsError(e?.message || 'Error'); } finally { setDeletePositionsLoading(false); }
  };

  const togglePositionSelection = (positionId) => {
    const newSet = new Set(selectedPositionIds);
    if (newSet.has(positionId)) {
      newSet.delete(positionId);
    } else {
      newSet.add(positionId);
    }
    setSelectedPositionIds(newSet);
  };

  const handleBackdatePosition = async () => {
    setBackdateLoading(true); 
    setBackdateError(''); 
    setBackdateMsg(''); 
    setBackdateResult(null);
    
    try {
      // Validate required fields
      if (!backdateForm.user_id.trim()) {
        setBackdateError('User ID is required');
        setBackdateLoading(false);
        return;
      }
      if (!backdateForm.symbol.trim()) {
        setBackdateError('Symbol is required - use the dropdown to search');
        setBackdateLoading(false);
        return;
      }
      if (!backdateForm.qty) {
        setBackdateError('Quantity is required');
        setBackdateLoading(false);
        return;
      }
      if (!backdateForm.price) {
        setBackdateError('Price is required');
        setBackdateLoading(false);
        return;
      }
      if (!backdateForm.trade_date) {
        setBackdateError('Trade Date is required');
        setBackdateLoading(false);
        return;
      }
      if (!backdateForm.trade_time) {
        setBackdateError('Trade Time is required and must be within market hours');
        setBackdateLoading(false);
        return;
      }
      
      // Convert date from YYYY-MM-DD to DD-MM-YYYY for backend
      const formData = { ...backdateForm };
      formData.symbol = formData.symbol.toUpperCase().trim();
      formData.exchange = formData.exchange.toUpperCase().trim();
      formData.product_type = formData.product_type.toUpperCase().trim();
      
      if (formData.trade_date) {
        const [year, month, day] = formData.trade_date.split('-');
        formData.trade_date = `${day}-${month}-${year}`;
      }
      
      const res = await req('/admin/backdate-position', { 
        method: 'POST', 
        body: JSON.stringify(formData) 
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) { 
        setBackdateMsg(data.message || 'Position created.'); 
        setBackdateResult(data); 
        // Clear form on success
        setBackdateForm({ user_id: '', symbol: '', qty: '', price: '', trade_date: '', trade_time: '09:15', instrument_type: 'EQ', exchange: 'NSE', product_type: 'MIS' });
        setInstrumentSelectedFromDropdown(false); // Reset selection flag
      }
      else setBackdateError(data.detail || 'Failed');
    } catch (e) { setBackdateError(e?.message || 'Error'); } 
    finally { setBackdateLoading(false); }
  };

  const handleForceExit = async () => {
    if (!forceExitForm.user_id.trim()) { setForceExitError('User ID required.'); return; }
    if (!forceExitForm.position_id) { setForceExitError('Position ID required.'); return; }
    if (!forceExitForm.exit_price) { setForceExitError('Exit Price required.'); return; }
    if (!forceExitForm.exit_date) { setForceExitError('Exit Date required.'); return; }
    if (!forceExitForm.exit_time) { setForceExitError('Exit Time required and must be within market hours.'); return; }
    
    setForceExitLoading(true); setForceExitError(''); setForceExitMsg(''); setForceExitResult(null);
    try {
      const formData = { ...forceExitForm };
      // Convert date from YYYY-MM-DD to DD-MM-YYYY for backend
      if (formData.exit_date) {
        const [year, month, day] = formData.exit_date.split('-');
        formData.exit_date = `${day}-${month}-${year}`;
      }
      const res = await req('/admin/force-exit', { method: 'POST', body: JSON.stringify(formData) });
      const data = await res.json().catch(() => ({}));
      if (res.ok) { 
        setForceExitMsg(data.message || 'Position closed.'); 
        setForceExitResult(data); 
        // Clear form on success
        setForceExitForm({ user_id: '', position_id: '', exit_price: '', exit_time: '15:30', exit_date: '' });
      }
      else setForceExitError(data.detail || 'Failed');
    } catch (e) { setForceExitError(e?.message || 'Error'); } 
    finally { setForceExitLoading(false); }
  };

  const searchInstrument = async (q) => {
    if (!q || q.length < 2) { setInstrumentSuggestions([]); return; }
    try {
      const res = await req(`/instruments/search?q=${encodeURIComponent(q)}&limit=8`);
      if (res.ok) {
        const data = await res.json();
        const results = Array.isArray(data) ? data : data.data || [];
        setInstrumentSuggestions(results);
      }
    } catch { setInstrumentSuggestions([]); }
  };

  // ── Logo handlers ──
  const fetchCurrentLogo = useCallback(async () => {
    try {
      const res = await req('/admin/logo');
      if (res.ok) {
        const data = await res.json();
        setCurrentLogo(data.logo);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchCurrentLogo(); }, [fetchCurrentLogo]);

  const handleLogoFileChange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setLogoFile(file);
    const reader = new FileReader();
    reader.onload = (ev) => setLogoPreview(ev.target?.result);
    reader.readAsDataURL(file);
  };

  const handleLogoUpload = async () => {
    if (!logoFile) { setLogoMsg('Select a file first.'); return; }
    setLogoUploading(true); setLogoMsg('');
    const form = new FormData();
    form.append('file', logoFile);
    try {
      const res = await fetch(`${API}/admin/logo/upload`, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${apiService._token}` },
        body: form,
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setLogoMsg('Logo uploaded successfully!');
        await fetchCurrentLogo();
        setLogoFile(null);
        setLogoPreview(null);
      } else {
        setLogoMsg(data.detail || 'Upload failed');
      }
    } catch (e) { setLogoMsg(e?.message || 'Error'); } finally { setLogoUploading(false); }
  };

  const handleLogoDelete = async () => {
    if (!confirm('Delete the current logo?')) return;
    setLogoUploading(true); setLogoMsg('');
    try {
      const res = await req('/admin/logo', { method: 'DELETE' });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setLogoMsg('Logo deleted successfully.');
        await fetchCurrentLogo();
      } else {
        setLogoMsg(data.detail || 'Delete failed');
      }
    } catch (e) { setLogoMsg(e?.message || 'Error'); } finally { setLogoUploading(false); }
  };

  return (
    <div className="space-y-6 sa-scope">
      {/* Header with Mode Badge */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">{isSuperAdmin ? 'Super Admin Dashboard' : 'Admin Modules'}</h2>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 rounded-lg p-1 overflow-x-auto sa-tabs-bar border">
        {availableTabs.map(t => (
          <button key={t.id} onClick={() => handleTabChange(t.id)}
            className={`flex-shrink-0 px-4 py-2 rounded text-sm font-medium transition-all ${
              activeTab === t.id 
                ? 'bg-blue-600 text-white font-semibold shadow-lg' 
                : 'sa-tab-btn border hover:bg-gray-700'
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {!isSuperAdmin && availableTabs.length === 0 && (
        <div className="rounded-xl border border-zinc-700 bg-zinc-900 p-5">
          <p className="text-sm text-zinc-300">No modules are assigned to your account. Contact Super Admin.</p>
        </div>
      )}

      {/* ── Settings & Monitoring ── */}
      {activeTab === 'settings' && (
        <div className="space-y-5">
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">Zone 1: Live Telemetry</h2>
              <span className="text-[11px] text-zinc-400">Real-time monitoring and alerts</span>
            </div>
            <div className="rounded-xl border sa-card p-4">
              <SystemMonitoring />
            </div>
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">Zone 2: Operations</h2>
              <span className="text-[11px] text-zinc-400">High-impact runtime controls</span>
            </div>
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
              <div className="rounded-xl p-4 space-y-4 sa-card border">
                <h3 className="text-sm font-semibold">Option Chain Controls</h3>
                <div className="space-y-2">
                  <p className="text-xs text-gray-400">
                    <span className="font-semibold text-blue-400">Reset ATM Cache</span> refreshes in-memory ATM from current DB prices.
                  </p>
                  <button
                    onClick={() => askConfirm('Confirm ATM Reset', 'Are you sure to reset ATM cache from DB LTP?', handleOcRecalibrateAtm)}
                    disabled={ocAtmLoading}
                    className={btnCls('blue')}
                  >
                    {ocAtmLoading ? 'Resetting…' : 'Reset ATM Cache'}
                  </button>
                  {ocAtmResult && (
                    <div className={`text-xs rounded p-2 mt-1 ${
                      ocAtmResult.success ? 'bg-green-900/40 text-green-300 border border-green-700' : 'bg-red-900/40 text-red-300 border border-red-700'
                    }`}>
                      <div className="font-semibold mb-1">{ocAtmResult.message}</div>
                      {(ocAtmResult.results || []).map(r => (
                        <div key={r.underlying} className="font-mono">
                          {r.underlying}: {r.status === 'updated'
                            ? `LTP=${r.ltp} | ATM ${r.old_atm} -> ${r.new_atm}`
                            : r.status}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                <hr className="border-zinc-700" />
                <div className="space-y-2">
                  <p className="text-xs text-gray-400">
                    <span className="font-semibold text-orange-400">Rebuild Skeleton from Dhan</span> pulls fresh spot from Dhan and rebuilds the options skeleton.
                  </p>
                  <button
                    onClick={() => askConfirm('Confirm Skeleton Rebuild', 'Are you sure to rebuild option chain skeleton from Dhan REST?', handleOcRebuildSkeleton)}
                    disabled={ocRebuildLoading}
                    className={btnCls('red')}
                  >
                    {ocRebuildLoading ? 'Rebuilding…' : 'Rebuild Skeleton'}
                  </button>
                  {ocRebuildResult && (
                    <div className={`text-xs rounded p-2 mt-1 ${
                      ocRebuildResult.success ? 'bg-green-900/40 text-green-300 border border-green-700' : 'bg-red-900/40 text-red-300 border border-red-700'
                    }`}>
                      <div className="font-semibold mb-1">{ocRebuildResult.message}</div>
                      {(ocRebuildResult.atm_updates || []).map(r => (
                        <div key={r.underlying} className="font-mono">
                          {r.underlying}: {(r.new_atm !== undefined && r.new_atm !== null)
                            ? `Spot=${r.dhan_ltp ?? 'n/a'} | ATM ${r.old_atm ?? 'n/a'} -> ${r.new_atm}`
                            : r.status}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <div className="rounded-xl p-4 space-y-3 sa-card border">
                <h3 className="text-sm font-semibold">Instrument Master</h3>
                <p className="text-xs text-gray-400">Reload the latest instrument master and refresh tick metadata cache.</p>
                {masterMsg && <p className="text-xs text-blue-300">{masterMsg}</p>}
                <button
                  onClick={() => askConfirm('Confirm Instrument Reload', 'Are you sure to reload instrument master now?', handleLoadInstrumentMaster)}
                  disabled={masterLoading}
                  className={btnCls('purple')}
                >
                  {masterLoading ? 'Reloading…' : 'Reload Instrument Master'}
                </button>
              </div>

              <div className="rounded-xl p-4 space-y-3 sa-card border">
                <h3 className="text-sm font-semibold">Subscription Management</h3>
                <p className="text-xs text-gray-400">
                  Force expiry rollover immediately unsubscribes expired contracts not needed by watchlists/open positions.
                </p>
                <button
                  onClick={() => askConfirm('Confirm Expiry Rollover', 'Are you sure to force expiry rollover now?', handleExpiryRollover)}
                  disabled={expiryRolloverLoading}
                  className={btnCls('red')}
                >
                  {expiryRolloverLoading ? 'Processing…' : 'Force Expiry Rollover'}
                </button>
                {expiryRolloverResult && (
                  <div className={`text-xs rounded p-3 mt-2 ${
                    expiryRolloverResult.status === 'completed'
                      ? 'bg-green-900/40 text-green-300 border border-green-700'
                      : 'bg-red-900/40 text-red-300 border border-red-700'
                  }`}>
                    <div className="font-semibold mb-2">
                      {expiryRolloverResult.status === 'completed' ? 'Rollover Completed' : 'Failed'}
                    </div>
                    <div className="space-y-1 font-mono">
                      <div>Tokens Before: <span className="text-white">{expiryRolloverResult.tokens_before || 'N/A'}</span></div>
                      <div>Tokens After: <span className="text-white">{expiryRolloverResult.tokens_after || 'N/A'}</span></div>
                      <div className="font-bold">Evicted: <span className="text-white">{expiryRolloverResult.evicted || 0}</span></div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </section>

          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">Zone 3: Configuration</h2>
              <span className="text-[11px] text-zinc-400">Persistent platform settings</span>
            </div>
            <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
              <div className="rounded-xl p-4 space-y-3 sa-card border">
                <h3 className="text-sm font-semibold">DhanHQ Authentication</h3>
                <p className="text-xs text-gray-400">
                  Authentication controls moved to the <span className="text-blue-400 font-semibold">Market Data Connection</span> tab.
                  Configure AUTO_TOTP (primary), STATIC_IP (secondary), and DAILY_MANUAL fallback there.
                </p>
                <div className="flex items-center gap-2 text-xs">
                  <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                    dhanStatus === null              ? 'bg-gray-500' :
                    dhanStatus.connected             ? 'bg-green-500 animate-pulse' :
                    dhanStatus.tick_processor        ? 'bg-yellow-400 animate-pulse' :
                                                       'bg-red-500'
                  }`} />
                  <span className="text-zinc-400">
                    {dhanStatus === null ? 'Checking status…'
                      : dhanStatus.connected     ? `Connected - ${dhanStatus.slots?.filter(s => s.connected).length ?? 0}/5 WS slots active`
                      : dhanStatus.tick_processor ? 'Services started - waiting for WS connection…'
                      : dhanStatus.has_credentials ? 'Credentials saved - not connected'
                      : 'No credentials saved'
                    }
                  </span>
                </div>
              </div>

              <div className="rounded-xl p-4 space-y-3 sa-card border">
                <h3 className="text-sm font-semibold">Market Hours</h3>
                {EXCHANGES.map(ex => (
                  <div key={ex} className="space-y-2 border-b border-zinc-700 pb-3 last:border-0 last:pb-0">
                    <div className="text-xs font-semibold text-blue-400">{ex}</div>
                    <div className="grid grid-cols-2 gap-3">
                      <FormField label="Open">
                        <input type="time" className={inputCls}
                          value={marketConfig[ex]?.open || ''}
                          onChange={e => setMarketConfig(c => ({ ...c, [ex]: { ...c[ex], open: e.target.value } }))} />
                      </FormField>
                      <FormField label="Close">
                        <input type="time" className={inputCls}
                          value={marketConfig[ex]?.close || ''}
                          onChange={e => setMarketConfig(c => ({ ...c, [ex]: { ...c[ex], close: e.target.value } }))} />
                      </FormField>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {DAYS_OF_WEEK.map((day, idx) => (
                        <label key={day} className="flex items-center gap-1 text-xs cursor-pointer">
                          <input type="checkbox"
                            checked={(marketConfig[ex]?.days || []).includes(idx)}
                            onChange={e => {
                              const days = [...(marketConfig[ex]?.days || [])];
                              if (e.target.checked) days.push(idx); else days.splice(days.indexOf(idx), 1);
                              setMarketConfig(c => ({ ...c, [ex]: { ...c[ex], days } }));
                            }} />
                          {day.slice(0, 3)}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
                {mcError && <p className="text-xs text-red-400">{mcError}</p>}
                <button onClick={saveMarketConfig} className={btnCls('green')}>Save Market Hours</button>
              </div>

              <div className="rounded-xl p-4 space-y-3 sa-card border">
                <h3 className="text-sm font-semibold">Brand Logo</h3>
                <p className="text-xs text-gray-400">Upload a custom logo to replace the TN text in the header.</p>
                {currentLogo && (
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-gray-400">Current Logo</label>
                    <div className="flex items-center gap-3 p-3 bg-zinc-900 rounded-lg border border-zinc-700">
                      <img src={currentLogo} alt="Current logo" className="h-8 max-w-[120px] object-contain" />
                      <button onClick={handleLogoDelete} disabled={logoUploading} className="ml-auto px-3 py-1 text-xs bg-red-600 hover:bg-red-500 text-white rounded transition-colors">
                        Delete
                      </button>
                    </div>
                  </div>
                )}
                <FormField label="Upload New Logo (PNG, JPG, SVG - Max 2MB)">
                  <input
                    type="file"
                    accept="image/*"
                    onChange={handleLogoFileChange}
                    className="text-xs text-gray-300"
                  />
                </FormField>
                {logoPreview && (
                  <div className="p-3 bg-zinc-900 rounded-lg border border-zinc-700">
                    <img src={logoPreview} alt="Preview" className="h-8 max-w-[120px] object-contain" />
                  </div>
                )}
                {logoMsg && <p className="text-xs text-blue-300">{logoMsg}</p>}
                <button onClick={handleLogoUpload} disabled={logoUploading || !logoFile} className={btnCls('indigo')}>
                  {logoUploading ? 'Uploading…' : 'Upload Logo'}
                </button>
              </div>
            </div>
          </section>
        </div>
      )}

      {activeTab === 'adminAccess' && (
        <div className="space-y-5">
          <section className="rounded-xl p-5 sa-card border space-y-4">
            <div>
              <h2 className="text-base font-semibold">Admin Access Control</h2>
              <p className="text-xs text-gray-400 mt-1">
                Select an admin and allow or restrict access to Admin Dashboard tabs.
              </p>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <FormField label="Select Admin">
                <select
                  className={inputCls}
                  value={selectedAdminAccessUserId}
                  onChange={(e) => setSelectedAdminAccessUserId(e.target.value)}
                  disabled={adminAccessUsersLoading || adminAccessLoading}
                >
                  <option value="">Select admin</option>
                  {adminAccessUsers.map((admin) => {
                    const label = `${admin.first_name || ''} ${admin.last_name || ''}`.trim() || admin.name || admin.mobile;
                    return (
                      <option key={admin.id} value={admin.id}>
                        {label} ({admin.mobile || 'no mobile'})
                      </option>
                    );
                  })}
                </select>
              </FormField>
            </div>

            {adminAccessUsersLoading && <p className="text-xs text-zinc-400">Loading admins...</p>}
            {adminAccessLoading && <p className="text-xs text-zinc-400">Loading selected admin access...</p>}
            {adminAccessError && <p className="text-xs text-red-400">{adminAccessError}</p>}
            {adminAccessMsg && <p className="text-xs text-green-400">{adminAccessMsg}</p>}

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {ADMIN_DASHBOARD_TABS.map((tab) => (
                <label key={tab.id} className="flex items-start gap-3 rounded-lg border border-zinc-700 bg-zinc-900 p-3 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={selectedAdminPermissions.has(tab.permission)}
                    onChange={() => toggleAdminPermission(tab.permission)}
                    disabled={!selectedAdminAccessUserId || adminAccessLoading || adminAccessSaving}
                    className="mt-0.5"
                  />
                  <div>
                    <div className="text-sm font-medium text-zinc-100">{tab.label}</div>
                    <div className="text-xs text-zinc-400">{tab.description}</div>
                  </div>
                </label>
              ))}
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handleSaveAdminAccess}
                disabled={!selectedAdminAccessUserId || adminAccessSaving || adminAccessLoading}
                className={btnCls('green')}
              >
                {adminAccessSaving ? 'Saving...' : 'Save Access'}
              </button>
            </div>
          </section>
        </div>
      )}

      {activeTab === 'payin' && (
        <div className="rounded-xl p-5 sa-card border">
          <PayinWorkspace showHeading={false} mode="admin" />
        </div>
      )}

      {/* ── Market Data Connection ── */}
      {activeTab === 'marketData' && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <div className="rounded-xl p-5 space-y-4 bg-zinc-800 border border-zinc-700">
            <h2 className="text-base font-semibold">DhanHQ Market Data Authentication</h2>
            <p className="text-xs text-gray-400">
              Primary: <span className="text-green-400 font-semibold">AUTO_TOTP</span> | Secondary: <span className="text-blue-400 font-semibold">STATIC_IP</span> | Fallback: <span className="text-yellow-400 font-semibold">DAILY_MANUAL</span>
            </p>

            <div className="flex flex-wrap gap-2">
              {['AUTO_TOTP', 'STATIC_IP', 'DAILY_MANUAL'].map(mode => (
                <button
                  key={mode}
                  onClick={() => setLocalSettings(s => ({ ...s, authMode: mode }))}
                  className={`px-3 py-2 rounded-lg text-xs font-semibold transition-all ${
                    localSettings.authMode === mode
                      ? 'bg-blue-600 text-zinc-100'
                      : 'bg-zinc-800 text-zinc-400 border border-zinc-700'
                  }`}
                >
                  {mode}
                </button>
              ))}
            </div>

            <FormField label="Client ID">
              <input
                className={inputCls}
                value={localSettings.clientId || ''}
                onChange={e => setLocalSettings(s => ({ ...s, clientId: e.target.value }))}
                placeholder="Required for all 3 modes"
              />
            </FormField>

            <FormField label="DHAN PIN (Used by AUTO_TOTP)">
              <input
                className={inputCls}
                value={localSettings.dhanPin || ''}
                onChange={e => setLocalSettings(s => ({ ...s, dhanPin: e.target.value }))}
                placeholder={localSettings.hasSavedTotp ? 'Already saved. Enter both PIN and TOTP secret together only if replacing.' : 'Enter DHAN PIN'}
                type="password"
              />
            </FormField>

            <FormField label="DHAN TOTP Secret (Used by AUTO_TOTP)">
              <input
                className={inputCls}
                value={localSettings.dhanTotpSecret || ''}
                onChange={e => setLocalSettings(s => ({ ...s, dhanTotpSecret: e.target.value }))}
                placeholder={localSettings.hasSavedTotp ? 'Already saved. Enter both PIN and TOTP secret together only if replacing.' : 'Enter DHAN TOTP Secret'}
                type="password"
              />
            </FormField>

            <FormField label={localSettings.authMode === 'DAILY_MANUAL' ? 'Daily Access Token (Required)' : 'Daily Access Token (Optional fallback)'}>
              <input
                className={inputCls}
                value={localSettings.accessToken || ''}
                onChange={e => setLocalSettings(s => ({ ...s, accessToken: e.target.value }))}
                placeholder="Paste daily token for manual fallback"
                type="password"
              />
            </FormField>

            <FormField label="Static API Key (Used by STATIC_IP)">
              <input
                className={inputCls}
                value={localSettings.apiKey || ''}
                onChange={e => setLocalSettings(s => ({ ...s, apiKey: e.target.value }))}
                placeholder={localSettings.hasSavedStatic ? 'Already saved. Enter key only if replacing static credentials.' : 'API key for static mode'}
              />
            </FormField>
            <FormField label="Static API Secret (Used by STATIC_IP)">
              <input
                className={inputCls}
                value={localSettings.clientSecret || ''}
                onChange={e => setLocalSettings(s => ({ ...s, clientSecret: e.target.value }))}
                placeholder={localSettings.hasSavedStatic ? 'Already saved. Enter secret only if replacing static credentials.' : 'API secret for static mode'}
                type="password"
              />
            </FormField>

            {saveError && <p className="text-xs text-red-400">{saveError}</p>}
            {saved && <p className="text-xs text-green-400">Credentials saved successfully.</p>}

            <div className="flex flex-wrap gap-2">
              <button onClick={handleSave} disabled={isSaving || authLoading} className={btnCls('blue')}>
                {isSaving ? 'Saving…' : 'Save Credentials'}
              </button>
              <button onClick={handleForceSwitchMode} disabled={authSwitchLoading || authLoading} className={btnCls('yellow')}>
                {authSwitchLoading ? 'Switching…' : 'Force Switch Mode'}
              </button>
              <button onClick={handleAuthSelfTest} disabled={authSelfTestLoading || authLoading} className={btnCls('indigo')}>
                {authSelfTestLoading ? 'Testing…' : 'Auto Test All Modes'}
              </button>
            </div>

            {authSwitchMsg.text && (
              <p className={`text-xs ${authSwitchMsg.type === 'success' ? 'text-green-400' : 'text-red-400'}`}>
                {authSwitchMsg.text}
              </p>
            )}

            {authSelfTestError && <p className="text-xs text-red-400">{authSelfTestError}</p>}

            {authSelfTestResult && (
              <div className="rounded-lg border border-zinc-700 bg-zinc-900 p-3 text-xs space-y-2">
                <div className="flex items-center justify-between">
                  <div className="text-zinc-300 font-semibold">Auto Test Outcome (Safe Mode)</div>
                  <div className="text-zinc-500">{new Date(authSelfTestResult.checked_at).toLocaleString()}</div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {(authSelfTestResult.tests || []).map((t) => (
                    <div
                      key={t.mode}
                      className={`rounded border p-2 ${
                        t.status === 'ready'
                          ? 'border-green-700 bg-green-900/20 text-green-300'
                          : 'border-red-700 bg-red-900/20 text-red-300'
                      }`}
                    >
                      <div className="font-semibold uppercase">{t.mode}</div>
                      <div className="mt-1">{t.summary}</div>
                      {Array.isArray(t.missing_credentials) && t.missing_credentials.length > 0 && (
                        <div className="mt-1 text-[11px] text-zinc-200">
                          Missing: {t.missing_credentials.join(', ')}
                        </div>
                      )}
                    </div>
                  ))}
                </div>

                {Array.isArray(authSelfTestResult.reenter_recommended) && authSelfTestResult.reenter_recommended.length > 0 ? (
                  <div className="text-[11px] text-yellow-300">
                    Re-enter needed: {authSelfTestResult.reenter_recommended.join(', ')}
                  </div>
                ) : (
                  <div className="text-[11px] text-green-300">No re-entry needed based on current saved credentials.</div>
                )}
              </div>
            )}
          </div>

          <div className="rounded-xl p-5 space-y-4 bg-zinc-800 border border-zinc-700">
            <h3 className="text-sm font-semibold">Connection Runtime Status</h3>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <div className="rounded-lg border border-zinc-700 p-3 bg-zinc-900">
                <div className="text-zinc-400">Active Auth Mode</div>
                <div className="text-zinc-100 font-semibold mt-1">{marketAuthStatus?.mode || 'unknown'}</div>
              </div>
              <div className="rounded-lg border border-zinc-700 p-3 bg-zinc-900">
                <div className="text-zinc-400">Daily Token</div>
                <div className="text-zinc-100 font-semibold mt-1">{marketAuthStatus?.daily_token?.has_token ? 'Available' : 'Missing'}</div>
              </div>
              <div className="rounded-lg border border-zinc-700 p-3 bg-zinc-900">
                <div className="text-zinc-400">Static Configured</div>
                <div className="text-zinc-100 font-semibold mt-1">{marketAuthStatus?.static_configured ? 'Yes' : 'No'}</div>
              </div>
              <div className="rounded-lg border border-zinc-700 p-3 bg-zinc-900">
                <div className="text-zinc-400">Static Failure Counter</div>
                <div className="text-zinc-100 font-semibold mt-1">{marketAuthStatus?.monitor?.failure_count ?? 0}</div>
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs">
              <span className={`w-2 h-2 rounded-full flex-shrink-0 ${
                dhanStatus === null              ? 'bg-gray-500' :
                dhanStatus.connected             ? 'bg-green-500 animate-pulse' :
                dhanStatus.tick_processor        ? 'bg-yellow-400 animate-pulse' :
                                                   'bg-red-500'
              }`} />
              <span className="text-zinc-400">
                {dhanStatus === null ? 'Checking status…'
                  : dhanStatus.connected     ? `Connected - ${dhanStatus.slots?.filter(s => s.connected).length ?? 0}/5 WS slots active`
                  : dhanStatus.tick_processor ? 'Services started - waiting for WS connection…'
                  : dhanStatus.has_credentials ? 'Credentials saved - not connected'
                  : 'No credentials saved'
                }
              </span>
            </div>

            <div className="flex flex-wrap gap-2">
              {(dhanStatus?.connected || dhanStatus?.tick_processor)
                ? <button
                    onClick={() => askConfirm('Confirm Disconnect', 'Are you sure to disconnect all Dhan services?', handleDisconnect)}
                    disabled={isConnecting}
                    className={btnCls('red')}
                  >
                    {isConnecting ? 'Working…' : 'Disconnect'}
                  </button>
                : <button
                    onClick={() => askConfirm('Confirm Connect', 'Are you sure to start Dhan services and reconnect streams?', handleConnect)}
                    disabled={isConnecting}
                    className={btnCls('green')}
                  >
                    {isConnecting ? 'Connecting…' : 'Connect to Dhan'}
                  </button>
              }
            </div>

            {connectMsg.text && (
              <p className={`text-xs ${
                connectMsg.type === 'success' ? 'text-green-400' :
                connectMsg.type === 'warn'    ? 'text-yellow-400' : 'text-red-400'
              }`}>
                {connectMsg.text}
              </p>
            )}
          </div>
        </div>
      )}

      {/* ── SMS OTP Settings ── */}
      {activeTab === 'smsOtp' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="rounded-xl p-5 space-y-4 bg-zinc-800 border border-zinc-700">
            <h2 className="text-base font-semibold">SMS OTP Provider Settings</h2>
            <p className="text-xs text-gray-400">
              Configure Message Central credentials and OTP controls used in phone OTP flows.
            </p>

            <FormField label="Customer ID">
              <input
                className={inputCls}
                value={smsOtpSettings.message_central_customer_id}
                onChange={e => setSmsOtpSettings(s => ({ ...s, message_central_customer_id: e.target.value }))}
                placeholder="C-44071166CC38423"
              />
            </FormField>

            <FormField label="Password">
              <input
                className={inputCls}
                type="password"
                value={smsOtpSettings.message_central_password}
                onChange={e => setSmsOtpSettings(s => ({ ...s, message_central_password: e.target.value }))}
                placeholder="Allalone@01"
              />
            </FormField>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <FormField label="OTP Expiry (seconds)">
                <input
                  className={inputCls}
                  type="number"
                  min={1}
                  value={smsOtpSettings.otp_expiry_seconds}
                  onChange={e => setSmsOtpSettings(s => ({ ...s, otp_expiry_seconds: Number(e.target.value || 0) }))}
                />
              </FormField>

              <FormField label="Resend Cooldown (seconds)">
                <input
                  className={inputCls}
                  type="number"
                  min={1}
                  value={smsOtpSettings.otp_resend_cooldown_seconds}
                  onChange={e => setSmsOtpSettings(s => ({ ...s, otp_resend_cooldown_seconds: Number(e.target.value || 0) }))}
                />
              </FormField>

              <FormField label="Max Attempts">
                <input
                  className={inputCls}
                  type="number"
                  min={1}
                  value={smsOtpSettings.otp_max_attempts}
                  onChange={e => setSmsOtpSettings(s => ({ ...s, otp_max_attempts: Number(e.target.value || 0) }))}
                />
              </FormField>
            </div>

            {smsOtpLoading && <p className="text-xs text-zinc-400">Loading settings...</p>}
            {smsOtpError && <p className="text-xs text-red-400">{smsOtpError}</p>}
            {smsOtpMessage && <p className="text-xs text-green-400">{smsOtpMessage}</p>}

            <div className="flex gap-2">
              <button onClick={handleSaveSmsOtpSettings} disabled={smsOtpSaving || smsOtpLoading} className={btnCls('blue')}>
                {smsOtpSaving ? 'Saving...' : 'Save SMS OTP Settings'}
              </button>
              <button onClick={fetchSmsOtpSettings} disabled={smsOtpLoading || smsOtpSaving} className={btnCls('gray')}>
                {smsOtpLoading ? 'Refreshing...' : 'Reload'}
              </button>
            </div>
          </div>

          <div className="rounded-xl p-5 bg-zinc-800 border border-zinc-700 space-y-3">
            <h3 className="text-sm font-semibold">Configured Defaults</h3>
            <ul className="text-xs text-zinc-300 space-y-2">
              <li>Customer ID: C-44071166CC38423</li>
              <li>Password: Allalone@01</li>
              <li>OTP expiry: 180 seconds (3 minutes)</li>
              <li>Resend cooldown: 300 seconds (5 minutes)</li>
              <li>Maximum attempts: 5</li>
            </ul>
          </div>
        </div>
      )}

      {/* ── User Auth Check ── */}
      {activeTab === 'authCheck' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left column — Diagnose & Delete */}
          <div className="space-y-6">
            {/* Diagnose User Login */}
            <div className="rounded-xl p-5 space-y-4 bg-zinc-800 border border-zinc-700">
              <h2 className="text-base font-semibold">Diagnose User Login</h2>
              <p className="text-xs text-gray-400">Check why a user cannot log in. Enter their mobile or username.</p>
              <FormField label="Mobile / Username">
                <input className={inputCls} value={authCheckIdentifier}
                  onChange={e => setAuthCheckIdentifier(e.target.value)} placeholder="9876543210" />
              </FormField>
              <FormField label="Password (optional — verifies hash)">
                <input className={inputCls} type="password" value={authCheckPassword}
                  onChange={e => setAuthCheckPassword(e.target.value)} placeholder="Leave blank to skip" />
              </FormField>
              {authCheckError && <p className="text-xs text-red-400">{authCheckError}</p>}
              <button onClick={handleUserAuthCheck} disabled={authCheckLoading} className={btnCls('blue')}>
                {authCheckLoading ? 'Checking…' : 'Run Diagnosis'}
              </button>
              {authCheckResult && (
                <pre className="rounded-lg p-3 text-xs overflow-auto max-h-72 bg-zinc-950 text-zinc-100">
                  {JSON.stringify(authCheckResult, null, 2)}
                </pre>
              )}
            </div>

            {/* Soft Delete User */}
            <div className="rounded-xl p-5 space-y-4 bg-red-950/30 border border-red-700/50">
              <h2 className="text-base font-semibold text-red-300">Soft Delete User (Archive)</h2>
              <p className="text-xs text-gray-400">Archive a user — they cannot login but data is preserved. Recoverable.</p>
              <FormField label="User to Archive">
                <input className={inputCls} value={deleteUserSelection}
                  onChange={e => setDeleteUserSelection(e.target.value)} placeholder="Mobile or User ID" />
              </FormField>
              {deleteUsersError && <p className="text-xs text-red-400">❌ {deleteUsersError}</p>}
              {deleteUsersMsg && <p className="text-xs text-green-400">✓ {deleteUsersMsg}</p>}
              <button onClick={handleSoftDeleteUser} disabled={deleteUsersLoading} className={btnCls('red')}>
                {deleteUsersLoading ? 'Archiving…' : '🗑️ Archive User'}
              </button>
            </div>

            {/* Delete User Positions */}
            <div className="rounded-xl p-5 space-y-4 bg-orange-950/30 border border-orange-700/50">
              <h2 className="text-base font-semibold text-orange-300">Delete Positions</h2>
              <p className="text-xs text-gray-400">⚠️ Delete specific or all positions, orders, and ledger entries.</p>
              
              <FormField label="User ID">
                <input className={inputCls} value={deletePositionsUserSelection}
                  onChange={e => setDeletePositionsUserSelection(e.target.value)} placeholder="Mobile or User ID" />
              </FormField>
              
              <div className="flex gap-2">
                <button onClick={handleLoadUserPositions} disabled={loadingUserPositions || !deletePositionsUserSelection} className={btnCls('blue')}>
                  {loadingUserPositions ? 'Loading…' : 'View Positions'}
                </button>
                <button onClick={handleDeleteUserPositions} disabled={deletePositionsLoading || !deletePositionsUserSelection} className={btnCls('red')}>
                  {deletePositionsLoading ? 'Deleting…' : '🔥 Delete ALL'}
                </button>
              </div>

              {/* Position List */}
              {userPositionsList && (
                <div className="space-y-2 max-h-64 overflow-y-auto rounded-lg bg-zinc-900/50 p-3 border border-zinc-700">
                  <div className="text-xs font-semibold text-gray-300 mb-2">
                    Positions ({userPositionsList.positions.length})
                    {selectedPositionIds.size > 0 && (
                      <span className="ml-2 text-orange-400">
                        {selectedPositionIds.size} selected
                      </span>
                    )}
                  </div>
                  
                  {userPositionsList.positions.length === 0 ? (
                    <p className="text-xs text-gray-500">No positions</p>
                  ) : (
                    userPositionsList.positions.map((pos) => (
                      <label key={pos.position_id} className="flex items-start gap-2 p-2 hover:bg-zinc-800 rounded cursor-pointer text-xs">
                        <input
                          type="checkbox"
                          checked={selectedPositionIds.has(pos.position_id)}
                          onChange={() => togglePositionSelection(pos.position_id)}
                          className="mt-0.5"
                        />
                        <span className="flex-1">
                          <span className="font-semibold">{pos.symbol}</span>
                          <span className="text-gray-400 ml-1">
                            {pos.quantity} @ {pos.avg_price.toFixed(2)}
                          </span>
                          <span className="text-gray-500 ml-1">({pos.status})</span>
                        </span>
                      </label>
                    ))
                  )}
                  
                  {selectedPositionIds.size > 0 && (
                    <button
                      onClick={handleDeleteSpecificPositions}
                      disabled={deletePositionsLoading}
                      className={btnCls('red')}
                      style={{ width: '100%' }}
                    >
                      {deletePositionsLoading ? 'Deleting…' : `Delete ${selectedPositionIds.size} Selected`}
                    </button>
                  )}
                </div>
              )}

              {deletePositionsError && <p className="text-xs text-orange-400">❌ {deletePositionsError}</p>}
              {deletePositionsMsg && <p className="text-xs text-green-400">✓ {deletePositionsMsg}</p>}
            </div>
          </div>

          {/* Right column — Archived Users */}
          <div className="rounded-xl p-5 space-y-4 bg-zinc-800 border border-zinc-700 h-fit">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold">Archived Users</h2>
              <button onClick={fetchArchivedUsers} disabled={archivedUsersLoading} 
                className={`text-xs ${archivedUsersLoading ? 'text-gray-500' : 'text-blue-400 hover:text-blue-300'}`}>
                {archivedUsersLoading ? '⟳ Loading…' : '🔄 Refresh'}
              </button>
            </div>
            <p className="text-xs text-gray-400">Users who have been archived. They cannot login.</p>
            {archivedUsers && archivedUsers.length > 0 ? (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {archivedUsers.map((u, idx) => (
                  <div key={idx} className="p-3 bg-zinc-900 rounded-lg border border-zinc-700 text-xs">
                    <div className="font-semibold text-zinc-100">{u.mobile || u.name || u.email}</div>
                    <div className="text-gray-400 text-xs mt-1">
                      Archived: {new Date(u.archived_at).toLocaleDateString()} 
                      {u.last_login && ` | Last login: ${new Date(u.last_login).toLocaleDateString()}`}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-gray-500">No archived users yet.</p>
            )}
          </div>
        </div>
      )}

      {/* ── Historic Position ── */}
      {activeTab === 'historic' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Backdate */}
          <div className="rounded-xl p-5 space-y-4 sa-card border">
            <h2 className="text-base font-semibold">Backdate Position</h2>
            <p className="text-xs text-gray-400">Manually add a historic trade position for any user.</p>
            
            <FormField label="User ID (Mobile or UUID)">
              <input
                className={inputCls}
                type="text"
                value={backdateForm.user_id}
                onChange={e => setBackdateForm(f => ({ ...f, user_id: e.target.value }))}
                placeholder="e.g., 9999999999 or UUID"
              />
            </FormField>
            
            <FormField label="Symbol">
              <div className="relative">
                <input
                  className={`${inputCls} ${backdateForm.symbol && !instrumentSuggestions.length && symbolInputBlur && !instrumentSelectedFromDropdown ? 'border-red-500 border-2' : ''}`}
                  type="text"
                  value={backdateForm.symbol}
                  onChange={e => {
                    const val = e.target.value;
                    searchInstrument(val);
                    setBackdateForm(f => ({ ...f, symbol: val }));
                    setInstrumentSelectedFromDropdown(false); // User is typing, not selecting
                  }}
                  onBlur={() => setTimeout(() => setSymbolInputBlur(true), 150)}
                  onFocus={() => setSymbolInputBlur(false)}
                  placeholder="Search stocks... (e.g., RELIANCE, INFY)"
                  autoComplete="off"
                  maxLength="20"
                />
                
                {backdateForm.symbol && symbolInputBlur && !instrumentSuggestions.length && !instrumentSelectedFromDropdown && (
                  <p className="text-xs text-red-400 mt-1">⚠️ Please search and select from dropdown</p>
                )}
                
                {instrumentSuggestions.length > 0 && !symbolInputBlur && (
                  <div className="absolute top-full left-0 right-0 mt-1 bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-64 overflow-y-auto z-10">
                    {instrumentSuggestions.map((suggestion, idx) => {
                      const symbol = suggestion.trading_symbol || suggestion.symbol;
                      const exchangeSegment = suggestion.exchange_segment || suggestion.exchange || '';
                      const instType = suggestion.instrument_type || '';
                      
                      // Extract base exchange from exchange_segment (NSE_EQ -> NSE, BSE_FO -> BSE, etc.)
                      const baseExchange = (exchangeSegment.split('_')[0] || 'NSE').toUpperCase();
                      
                      return (
                        <div
                          key={idx}
                          onClick={() => {
                            setBackdateForm(f => ({ 
                              ...f, 
                              symbol: symbol,
                              exchange: baseExchange || f.exchange,
                              instrument_type: instType.startsWith('OPT') ? (instType.includes('IDX') ? 'OPTIDX' : 'OPTSTK') :
                                              instType.startsWith('FUT') ? (instType.includes('IDX') ? 'FUTIDX' : 'FUTSTK') :
                                              'EQ'
                            }));
                            setInstrumentSuggestions([]);
                            setSymbolInputBlur(true);
                            setInstrumentSelectedFromDropdown(true); // Mark as selected from dropdown
                          }}
                          className="px-4 py-3 hover:bg-blue-600 cursor-pointer border-b border-gray-700 last:border-b-0 transition-colors"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div>
                              <div className="font-semibold text-zinc-100">{symbol}</div>
                              <div className="text-xs text-gray-400">{instType}</div>
                            </div>
                            <div className="text-xs px-2 py-1 bg-gray-700 rounded text-gray-300">
                              {exchangeSegment}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </FormField>
            
            <FormField label="Quantity">
              <input
                className={inputCls}
                type="number"
                value={backdateForm.qty}
                onChange={e => setBackdateForm(f => ({ ...f, qty: e.target.value }))}
                placeholder="e.g., 380"
                min="1"
              />
            </FormField>
            
            <FormField label="Price">
              <input
                className={inputCls}
                type="number"
                step="0.05"
                value={backdateForm.price}
                onChange={e => setBackdateForm(f => ({ ...f, price: e.target.value }))}
                placeholder="e.g., 514.70"
                min="0"
              />
            </FormField>
            
            <div className="grid grid-cols-2 gap-3">
              <FormField label="Trade Date">
                <input
                  className={inputCls}
                  type="date"
                  value={backdateForm.trade_date}
                  onChange={e => setBackdateForm(f => ({ ...f, trade_date: e.target.value }))}
                />
              </FormField>
              <FormField label="Trade Time (HH:MM)">
                <input
                  className={inputCls}
                  type="time"
                  value={backdateForm.trade_time}
                  onChange={e => setBackdateForm(f => ({ ...f, trade_time: e.target.value }))}
                />
              </FormField>
            </div>
            
            <div className="grid grid-cols-2 gap-3">
              <FormField label="Instrument Type">
                <select className={inputCls} value={backdateForm.instrument_type}
                  onChange={e => setBackdateForm(f => ({ ...f, instrument_type: e.target.value }))}>
                  <optgroup label="Equity">
                    <option value="EQ">Equity (EQ)</option>
                  </optgroup>
                  <optgroup label="Index">
                    <option value="FUTIDX">Index Future (FUTIDX)</option>
                    <option value="OPTIDX">Index Option (OPTIDX)</option>
                  </optgroup>
                  <optgroup label="Stock Derivatives">
                    <option value="FUTSTK">Stock Future (FUTSTK)</option>
                    <option value="OPTSTK">Stock Option (OPTSTK)</option>
                  </optgroup>
                  <optgroup label="Commodity Derivatives">
                    <option value="FUTCOMM">Commodity Future (FUTCOMM)</option>
                    <option value="OPTCOMM">Commodity Option (OPTCOMM)</option>
                  </optgroup>
                </select>
              </FormField>
              <FormField label="Exchange">
                <select className={inputCls} value={backdateForm.exchange}
                  onChange={e => setBackdateForm(f => ({ ...f, exchange: e.target.value }))}>
                  {EXCHANGES.map(ex => <option key={ex}>{ex}</option>)}
                </select>
              </FormField>
            </div>
            
            <div className="grid grid-cols-1 gap-3">
              <FormField label="Product Type">
                <select className={inputCls} value={backdateForm.product_type}
                  onChange={e => setBackdateForm(f => ({ ...f, product_type: e.target.value }))}>
                  <option value="MIS">MIS (Intraday)</option>
                  <option value="NORMAL">NORMAL (Delivery)</option>
                </select>
              </FormField>
            </div>
            
            {backdateError && <p className="text-xs text-red-400">❌ {backdateError}</p>}
            {backdateMsg   && <p className="text-xs text-green-400">✅ {backdateMsg}</p>}
            {backdateResult && (
              <pre className="rounded-lg p-3 text-xs overflow-auto max-h-40 bg-zinc-950 text-zinc-100">
                {JSON.stringify(backdateResult, null, 2)}
              </pre>
            )}
            <button onClick={handleBackdatePosition} disabled={backdateLoading} className={btnCls('blue')}>
              {backdateLoading ? 'Adding…' : 'Add Historic Position'}
            </button>
          </div>

          {/* Force Exit */}
          <div className="rounded-xl p-5 space-y-4 sa-card border">
            <h2 className="text-base font-semibold">Force Exit Position</h2>
            <p className="text-xs text-gray-400">Manually close an open position at a specified price.</p>
            <FormField label="User ID">
              <input className={inputCls} value={forceExitForm.user_id}
                onChange={e => setForceExitForm(f => ({ ...f, user_id: e.target.value }))} placeholder="User ID" />
            </FormField>
            <FormField label="Position ID">
              <input className={inputCls} value={forceExitForm.position_id}
                onChange={e => setForceExitForm(f => ({ ...f, position_id: e.target.value }))} placeholder="Position ID" />
            </FormField>
            <FormField label="Exit Date">
              <input className={inputCls} type="date" value={forceExitForm.exit_date}
                onChange={e => setForceExitForm(f => ({ ...f, exit_date: e.target.value }))} />
            </FormField>
            <FormField label="Exit Time (HH:MM)">
              <input className={inputCls} type="time" value={forceExitForm.exit_time}
                onChange={e => setForceExitForm(f => ({ ...f, exit_time: e.target.value }))} />
            </FormField>
            <FormField label="Exit Price">
              <input className={inputCls} type="number" step="0.05" value={forceExitForm.exit_price}
                onChange={e => setForceExitForm(f => ({ ...f, exit_price: e.target.value }))} placeholder="e.g. 450.50" />
            </FormField>
            {forceExitError && <p className="text-xs text-red-400">{forceExitError}</p>}
            {forceExitMsg   && <p className="text-xs text-green-400">{forceExitMsg}</p>}
            {forceExitResult && (
              <pre className="rounded-lg p-3 text-xs overflow-auto max-h-40 bg-zinc-950 text-zinc-100">
                {JSON.stringify(forceExitResult, null, 2)}
              </pre>
            )}
            <button onClick={handleForceExit} disabled={forceExitLoading} className={`${btnCls('red')} mt-2`}>
              {forceExitLoading ? 'Exiting…' : 'Force Exit Position'}
            </button>
          </div>
        </div>
      )}

      {/* ── Course Enrollments ── */}
      {activeTab === 'courseEnrollments' && (
        <div className="space-y-4">
          <div className="rounded-xl p-5 bg-zinc-800 border border-zinc-700">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div>
                <h2 className="text-base font-semibold">Course Enrollments</h2>
                <p className="text-xs text-zinc-400 mt-1">
                  Total enrollments: <span className="font-semibold text-zinc-100">{courseEnrollmentsTotal}</span>
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button onClick={handleExportCourseEnrollmentsCsv} disabled={!courseEnrollments.length || courseEnrollmentsLoading} className={btnCls('green')}>
                  Export CSV
                </button>
                <button onClick={fetchCourseEnrollments} disabled={courseEnrollmentsLoading} className={btnCls('blue')}>
                  {courseEnrollmentsLoading ? 'Loading…' : 'Refresh'}
                </button>
              </div>
            </div>

            {courseEnrollmentsError && (
              <div className="mb-4 p-3 bg-red-900/20 border border-red-700/40 rounded-lg text-xs text-red-300">
                {courseEnrollmentsError}
              </div>
            )}

            {courseEnrollmentsLoading && (
              <div className="text-center py-8 text-zinc-400">
                Loading course enrollments...
              </div>
            )}

            {!courseEnrollmentsLoading && courseEnrollments.length === 0 && (
              <div className="text-center py-8 text-zinc-400">
                No records found
              </div>
            )}

            {!courseEnrollmentsLoading && courseEnrollments.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-xs text-zinc-400 border-b border-zinc-700">
                      <th className="text-left py-3 px-4">Name</th>
                      <th className="text-left py-3 px-4">Email</th>
                      <th className="text-left py-3 px-4">Mobile</th>
                      <th className="text-left py-3 px-4">City</th>
                      <th className="text-left py-3 px-4">IP Details</th>
                      <th className="text-left py-3 px-4">SMS Verified</th>
                      <th className="text-left py-3 px-4">Email Verified</th>
                      <th className="text-left py-3 px-4">Enrollment Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {courseEnrollments.map((user, idx) => (
                      <tr
                        key={user.id}
                        className={`border-b border-zinc-700 hover:bg-zinc-700/30 cursor-pointer ${idx % 2 === 0 ? 'bg-zinc-800/50' : ''}`}
                        onClick={() => { setSelectedUser(user); setSelectedUserType('enrollment'); }}
                        title="Click to view full details"
                      >
                        <td className="py-3 px-4 font-medium text-zinc-100">{user.name}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.email}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.mobile || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.city || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.ip_details || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.sms_verified ? 'Yes' : 'No'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.email_verified ? 'Yes' : 'No'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-400">
                          {user.created_at ? new Date(user.created_at).toLocaleString() : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── User Signups ── */}
      {activeTab === 'userSignups' && (
        <div className="space-y-4">
          <div className="rounded-xl p-5 bg-zinc-800 border border-zinc-700">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div>
                <h2 className="text-base font-semibold">Recent Review Activity</h2>
                <p className="text-xs text-zinc-400 mt-1">
                  Approve, reject, and restore actions are recorded here.
                </p>
              </div>
            </div>

            {portalSignupActivityLoading ? (
              <div className="text-center py-6 text-zinc-400">Loading review activity...</div>
            ) : portalSignupActivity.length === 0 ? (
              <div className="text-center py-6 text-zinc-400">No review activity yet</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-xs text-zinc-400 border-b border-zinc-700">
                      <th className="text-left py-3 px-4">When</th>
                      <th className="text-left py-3 px-4">Action</th>
                      <th className="text-left py-3 px-4">Application</th>
                      <th className="text-left py-3 px-4">Status Change</th>
                      <th className="text-left py-3 px-4">Reviewed By</th>
                      <th className="text-left py-3 px-4">Reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portalSignupActivity.map((item, idx) => (
                      <tr key={item.id} className={`border-b border-zinc-700 hover:bg-zinc-700/30 ${idx % 2 === 0 ? 'bg-zinc-800/50' : ''}`}>
                        <td className="py-3 px-4 text-xs text-zinc-400">
                          {item.created_at ? new Date(item.created_at).toLocaleString() : '—'}
                        </td>
                        <td className="py-3 px-4 text-xs">
                          <span className={`px-2 py-1 rounded-full font-semibold border ${item.action === 'APPROVE' ? 'bg-blue-900/20 text-blue-300 border-blue-700/40' : item.action === 'REJECT' ? 'bg-red-900/20 text-red-300 border-red-700/40' : 'bg-yellow-900/20 text-yellow-300 border-yellow-700/40'}`}>
                            {item.action}
                          </span>
                        </td>
                        <td className="py-3 px-4 text-xs text-zinc-300">
                          <div className="font-medium text-zinc-100">{item.signup_name || '—'}</div>
                          <div>{item.signup_email || item.signup_mobile || '—'}</div>
                        </td>
                        <td className="py-3 px-4 text-xs text-zinc-300">
                          {item.previous_status} → {item.new_status}
                        </td>
                        <td className="py-3 px-4 text-xs text-zinc-300">
                          <div>{item.actor_name || '—'}</div>
                          <div className="text-zinc-400">{item.actor_mobile || '—'}</div>
                        </td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{item.reason || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="rounded-xl p-5 bg-zinc-800 border border-zinc-700">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div>
                <h2 className="text-base font-semibold">User Signups</h2>
                <p className="text-xs text-zinc-400 mt-1">
                  Total {portalUsersStatus.toLowerCase()} items: <span className="font-semibold text-zinc-100">{portalUsersTotal}</span>
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <select
                  className={inputCls}
                  value={portalUsersStatus}
                  onChange={(e) => setPortalUsersStatus(e.target.value)}
                  style={{ minWidth: 140 }}
                >
                  <option value="PENDING">Pending</option>
                  <option value="APPROVED">Approved</option>
                  <option value="REJECTED">Rejected</option>
                  <option value="ALL">All</option>
                </select>
                <button onClick={handleExportPortalUsersCsv} disabled={!portalUsers.length || portalUsersLoading} className={btnCls('green')}>
                  Export CSV
                </button>
                <button onClick={refreshPortalSignupPanel} disabled={portalUsersLoading || portalSignupActivityLoading} className={btnCls('blue')}>
                  {portalUsersLoading ? 'Loading…' : 'Refresh'}
                </button>
              </div>
            </div>

            {portalUsersDeleteMsg && (
              <div className="mb-4 p-3 bg-green-900/20 border border-green-700/40 rounded-lg text-xs text-green-300">
                {portalUsersDeleteMsg}
              </div>
            )}

            {portalUsersError && (
              <div className="mb-4 p-3 bg-red-900/20 border border-red-700/40 rounded-lg text-xs text-red-300">
                {portalUsersError}
              </div>
            )}

            {portalUsersLoading && (
              <div className="text-center py-8 text-zinc-400">
                Loading portal users...
              </div>
            )}

            {!portalUsersLoading && portalUsers.length === 0 && (
              <div className="text-center py-8 text-zinc-400">
                No records found
              </div>
            )}

            {!portalUsersLoading && portalUsers.length > 0 && (
              <div className="overflow-x-auto">
                <table className="min-w-full text-sm">
                  <thead>
                    <tr className="text-xs text-zinc-400 border-b border-zinc-700">
                      <th className="text-left py-3 px-4">Name</th>
                      <th className="text-left py-3 px-4">Email</th>
                      <th className="text-left py-3 px-4">Mobile</th>
                      <th className="text-left py-3 px-4">IP Details</th>
                      <th className="text-left py-3 px-4">SMS Verified</th>
                      <th className="text-left py-3 px-4">Email Verified</th>
                      <th className="text-left py-3 px-4">PAN</th>
                      <th className="text-left py-3 px-4">Aadhar</th>
                      <th className="text-left py-3 px-4">Bank A/C</th>
                      <th className="text-left py-3 px-4">IFSC</th>
                      <th className="text-left py-3 px-4">City</th>
                      <th className="text-left py-3 px-4">Status</th>
                      <th className="text-left py-3 px-4">Signup Date</th>
                      <th className="text-left py-3 px-4">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {portalUsers.map((user, idx) => (
                      <tr
                        key={user.id}
                        className={`border-b border-zinc-700 hover:bg-zinc-700/30 cursor-pointer ${idx % 2 === 0 ? 'bg-zinc-800/50' : ''}`}
                        onClick={(e) => { if (!e.target.closest('button')) { setSelectedUser(user); setSelectedUserType('signup'); } }}
                        title="Click to view full details"
                      >
                        <td className="py-3 px-4 font-medium text-zinc-100">{user.name}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.email}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.mobile || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.ip_details || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.sms_verified ? 'Yes' : 'No'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.email_verified ? 'Yes' : 'No'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.pan_number || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.aadhar_number || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.bank_account_number || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.ifsc || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">{user.city || '—'}</td>
                        <td className="py-3 px-4 text-xs text-zinc-300">
                          <div>{user.status || 'PENDING'}</div>
                          {user.rejection_reason ? <div className="text-zinc-400 mt-1">Reason: {user.rejection_reason}</div> : null}
                        </td>
                        <td className="py-3 px-4 text-xs text-zinc-400">
                          {user.created_at ? new Date(user.created_at).toLocaleString() : '—'}
                        </td>
                        <td className="py-3 px-4 text-xs text-zinc-300">
                          {(user.status || 'PENDING') === 'PENDING' ? (
                            <div className="flex gap-2">
                              <button
                                onClick={() => handlePortalSignupReview(user.id, 'APPROVE')}
                                disabled={portalActionBusyId === user.id}
                                className={btnCls('blue')}
                              >
                                {portalActionBusyId === user.id ? 'Working...' : 'Approve'}
                              </button>
                              <button
                                onClick={() => handlePortalSignupReview(user.id, 'REJECT')}
                                disabled={portalActionBusyId === user.id}
                                className={btnCls('red')}
                              >
                                Reject
                              </button>
                            </div>
                          ) : (user.status || 'PENDING') === 'REJECTED' ? (
                            <button
                              onClick={() => handlePortalSignupReview(user.id, 'RESTORE')}
                              disabled={portalActionBusyId === user.id}
                              className={btnCls('yellow')}
                            >
                              {portalActionBusyId === user.id ? 'Working...' : 'Restore'}
                            </button>
                          ) : (
                            <span className="text-zinc-400">Addressed</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Schedulers ── */}
      {activeTab === 'schedulers' && (
        <div className="space-y-4">
          <div className="rounded-xl p-5 bg-zinc-800 border border-zinc-700">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-base font-semibold">Schedulers</h2>
                <p className="text-xs text-zinc-400 mt-1">
                  Server time (IST): {schedSnapshot?.server_time_ist || '—'}
                </p>
                <p className="text-xs text-zinc-400">
                  Equity window: {schedSnapshot?.equity_window_active ? 'ACTIVE' : 'INACTIVE'} · Commodity window: {schedSnapshot?.commodity_window_active ? 'ACTIVE' : 'INACTIVE'}
                </p>
              </div>
              <button onClick={fetchSchedulers} disabled={schedLoading} className={btnCls('blue')}>
                {schedLoading ? 'Refreshing…' : 'Refresh'}
              </button>
            </div>

            {schedError && <p className="text-xs text-red-400 mt-3">{schedError}</p>}

            <div className="mt-4 overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead>
                  <tr className="text-xs text-zinc-400">
                    <th className="text-left py-2 pr-4">Name</th>
                    <th className="text-left py-2 pr-4">Type</th>
                    <th className="text-left py-2 pr-4">Window</th>
                    <th className="text-left py-2 pr-4">Status</th>
                    <th className="text-left py-2 pr-4">Override</th>
                    <th className="text-left py-2">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(schedSnapshot?.items || []).map((it) => {
                    const working = schedWorking && schedWorking.startsWith(it.id + ':');
                    return (
                      <tr key={it.id} className="border-t border-zinc-700">
                        <td className="py-3 pr-4 font-semibold text-zinc-100">{it.label}</td>
                        <td className="py-3 pr-4 text-zinc-300">{it.kind}</td>
                        <td className="py-3 pr-4 text-zinc-300">{it.window}</td>
                        <td className="py-3 pr-4">
                          <span className={`text-xs font-semibold px-2 py-1 rounded-full ${it.running ? 'bg-green-900/40 text-green-300 border border-green-700/40' : 'bg-zinc-900/40 text-zinc-300 border border-zinc-700/40'}`}>
                            {it.running ? 'RUNNING' : 'STOPPED'}
                          </span>
                        </td>
                        <td className="py-3 pr-4 text-zinc-300">{it.override}</td>
                        <td className="py-3">
                          <div className="flex flex-wrap gap-2">
                            {(it.actions || []).includes('start') && (
                              <button disabled={working || schedLoading} onClick={() => schedulerAction(it.id, 'start')} className={btnCls('green')}>Start</button>
                            )}
                            {(it.actions || []).includes('stop') && (
                              <button disabled={working || schedLoading} onClick={() => schedulerAction(it.id, 'stop')} className={btnCls('red')}>Stop</button>
                            )}
                            {(it.actions || []).includes('refresh') && (
                              <button disabled={working || schedLoading} onClick={() => schedulerAction(it.id, 'refresh')} className={btnCls('indigo')}>Refresh</button>
                            )}
                            {(it.actions || []).includes('auto') && (
                              <button disabled={working || schedLoading} onClick={() => schedulerAction(it.id, 'auto')} className={btnCls('blue')}>Auto</button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="mt-4 text-xs text-zinc-400">
              Holidays loaded: NSE {schedSnapshot?.holidays?.NSE?.count ?? 0}, BSE {schedSnapshot?.holidays?.BSE?.count ?? 0}, MCX {schedSnapshot?.holidays?.MCX?.count ?? 0}
            </div>
          </div>
        </div>
      )}

      <UserDetailModal
        user={selectedUser}
        type={selectedUserType}
        onClose={() => setSelectedUser(null)}
      />

      {/* ── Detailed Logs Tab ────────────────────────────────────────────── */}
      {activeTab === 'detailedLogs' && (
        <div className="space-y-4">
          {/* Filters bar */}
          <div className="rounded-xl border border-zinc-700 bg-zinc-800 p-4">
            <h2 className="mb-3 text-sm font-semibold text-zinc-200">Activity Audit Log</h2>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
              <input
                type="datetime-local"
                className={inputCls}
                value={activityLogsFilters.from_date}
                onChange={e => setActivityLogsFilters(f => ({ ...f, from_date: e.target.value }))}
              />
              <input
                type="datetime-local"
                className={inputCls}
                value={activityLogsFilters.to_date}
                onChange={e => setActivityLogsFilters(f => ({ ...f, to_date: e.target.value }))}
              />
              <select
                className={inputCls}
                value={activityLogsFilters.action_type}
                onChange={e => setActivityLogsFilters(f => ({ ...f, action_type: e.target.value }))}
              >
                <option value="">All actions</option>
                {['LOGIN','LOGIN_FAILED','LOGOUT','OTP_SEND','OTP_VERIFIED','OTP_VERIFY_FAILED',
                  'ENROLLMENT_SUBMIT','ACCOUNT_SIGNUP_SUBMIT','SIGNUP_APPROVED','SIGNUP_REJECTED','SIGNUP_RESTORED'].map(a => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
              <select
                className={inputCls}
                value={activityLogsFilters.role}
                onChange={e => setActivityLogsFilters(f => ({ ...f, role: e.target.value }))}
              >
                <option value="">All roles</option>
                {['USER','TRADER','ADMIN','SUPER_ADMIN'].map(r => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
              <input
                className={inputCls}
                placeholder="IP address"
                value={activityLogsFilters.ip}
                onChange={e => setActivityLogsFilters(f => ({ ...f, ip: e.target.value }))}
              />
              <input
                className={inputCls}
                placeholder="Search name / city..."
                value={activityLogsFilters.search}
                onChange={e => setActivityLogsFilters(f => ({ ...f, search: e.target.value }))}
              />
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={() => fetchActivityLogs(0, activityLogsFilters)}
                disabled={activityLogsLoading}
                className={btnCls('blue')}
              >
                {activityLogsLoading ? 'Loading...' : 'Apply Filters'}
              </button>
              <button
                onClick={() => {
                  const f = { from_date: '', to_date: '', action_type: '', role: '', search: '', ip: '' };
                  setActivityLogsFilters(f);
                  fetchActivityLogs(0, f);
                }}
                className={btnCls('zinc')}
              >
                Clear
              </button>
              <a
                href={`/api/v2/admin/activity-logs/export?${new URLSearchParams(
                  Object.fromEntries(Object.entries(activityLogsFilters).filter(([, v]) => v))
                )}`}
                target="_blank"
                rel="noopener noreferrer"
                className={btnCls('green')}
              >
                Export CSV
              </a>
            </div>
          </div>

          {activityLogsError && (
            <p className="rounded-lg bg-red-900/30 p-3 text-xs text-red-400">{activityLogsError}</p>
          )}

          <p className="text-xs text-zinc-400">
            Showing {activityLogs.length} of {activityLogsTotal} entries
          </p>

          <div className="overflow-x-auto rounded-xl border border-zinc-700">
            <table className="w-full text-xs text-zinc-300">
              <thead>
                <tr className="border-b border-zinc-700 bg-zinc-900 text-left text-zinc-400">
                  <th className="px-3 py-2">Date / Time</th>
                  <th className="px-3 py-2">Action</th>
                  <th className="px-3 py-2">Actor</th>
                  <th className="px-3 py-2">Role</th>
                  <th className="px-3 py-2">IP Address</th>
                  <th className="px-3 py-2">Location</th>
                  <th className="px-3 py-2">Browser</th>
                  <th className="px-3 py-2">Status</th>
                  <th className="px-3 py-2">Resource</th>
                </tr>
              </thead>
              <tbody>
                {activityLogsLoading && (
                  <tr><td colSpan={9} className="py-8 text-center text-zinc-500">Loading...</td></tr>
                )}
                {!activityLogsLoading && activityLogs.length === 0 && (
                  <tr><td colSpan={9} className="py-8 text-center text-zinc-500">No log entries found.</td></tr>
                )}
                {activityLogs.map((row, idx) => (
                  <tr
                    key={row.id}
                    className={`border-b border-zinc-700 hover:bg-zinc-700/20 ${idx % 2 === 0 ? 'bg-zinc-800/40' : ''}`}
                  >
                    <td className="whitespace-nowrap px-3 py-2">
                      {row.created_at ? new Date(row.created_at).toLocaleString('en-IN') : '\u2014'}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${
                        row.action_type?.includes('FAIL') || row.action_type?.includes('REJECT')
                          ? 'bg-red-900/50 text-red-300'
                          : row.action_type === 'LOGIN' || row.action_type === 'SIGNUP_APPROVED'
                          ? 'bg-green-900/50 text-green-300'
                          : 'bg-zinc-700 text-zinc-200'
                      }`}>
                        {row.action_type}
                      </span>
                    </td>
                    <td className="px-3 py-2 font-medium text-zinc-100">{row.actor_name || '\u2014'}</td>
                    <td className="px-3 py-2">{row.actor_role || '\u2014'}</td>
                    <td className="whitespace-nowrap px-3 py-2 font-mono text-[11px]">{row.ip_address || '\u2014'}</td>
                    <td className="px-3 py-2">
                      {[row.geo_city, row.geo_region, row.geo_country].filter(Boolean).join(', ') || '\u2014'}
                    </td>
                    <td className="max-w-[180px] truncate px-3 py-2 text-zinc-400" title={row.user_agent || ''}>
                      {row.user_agent
                        ? row.user_agent.replace(/^Mozilla\/\S+\s/, '').slice(0, 60)
                        : '\u2014'}
                    </td>
                    <td className="px-3 py-2">
                      <span className={`rounded px-1 py-0.5 text-[10px] ${
                        row.status_code >= 400 ? 'bg-red-900/40 text-red-300'
                        : row.status_code >= 200 ? 'bg-green-900/40 text-green-300'
                        : 'text-zinc-400'
                      }`}>
                        {row.status_code ?? '\u2014'}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-zinc-400">
                      {row.resource_type || ''}
                      {row.resource_id ? ` #${row.resource_id.slice(0, 8)}` : ''}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex items-center gap-3">
            <button
              disabled={activityLogsPage === 0 || activityLogsLoading}
              onClick={() => fetchActivityLogs(activityLogsPage - 1, activityLogsFilters)}
              className={btnCls('zinc')}
            >
              Prev
            </button>
            <span className="text-xs text-zinc-400">Page {activityLogsPage + 1}</span>
            <button
              disabled={(activityLogsPage + 1) * LOGS_PAGE_SIZE >= activityLogsTotal || activityLogsLoading}
              onClick={() => fetchActivityLogs(activityLogsPage + 1, activityLogsFilters)}
              className={btnCls('zinc')}
            >
              Next
            </button>
          </div>
        </div>
      )}

      <ConfirmModal
        open={confirmDialog.open}
        title={confirmDialog.title}
        message={confirmDialog.message}
        onCancel={closeConfirm}
        onConfirm={runConfirmedAction}
      />

    </div>
  );
};

export default SuperAdminDashboard;
