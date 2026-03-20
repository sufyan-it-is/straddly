import React, { useState, useEffect, useCallback } from "react";
import { apiService } from '../services/apiService';
import { useAuth } from '../contexts/AuthContext';

// ── Constants ─────────────────────────────────────────────────────────────
const INDIAN_STATES = [
  "Andhra Pradesh","Arunachal Pradesh","Assam","Bihar","Chhattisgarh",
  "Goa","Gujarat","Haryana","Himachal Pradesh","Jharkhand","Karnataka",
  "Kerala","Madhya Pradesh","Maharashtra","Manipur","Meghalaya","Mizoram",
  "Nagaland","Odisha","Punjab","Rajasthan","Sikkim","Tamil Nadu","Telangana",
  "Tripura","Uttar Pradesh","Uttarakhand","West Bengal",
  "Delhi","Jammu & Kashmir","Ladakh","Puducherry","Chandigarh",
];

const BROKERAGE_PLANS = [
  "Plan1 - 0.005×turnover",
  "Plan2 - 0.003×turnover",
  "Plan3 - Nil",
];

const STATUS_CONFIG = {
  ACTIVE:    { color: "#166534", bg: "var(--surface2)" },
  PENDING:   { color: "#f59e0b", bg: "#78350f22" },
  SUSPENDED: { color: "#f97316", bg: "#7c2d1222" },
  BLOCKED:   { color: "#ef4444", bg: "#7f1d1d22" },
};

const ROLE_COLORS = {
  SUPER_ADMIN: "#7c3aed",
  SUPER_USER:  "#d97706",
  ADMIN:       "#2563eb",
  USER:        "#16a34a",
};

const EMPTY_FORM = {
  first_name: "", last_name: "", email: "", mobile: "", password: "",
  role: "USER", status: "PENDING",
  address: "", country: "India", state: "", city: "",
  aadhar_number: "", pan_number: "", upi: "", bank_account: "",
  brokerage_plan: "Plan1 - 0.005×turnover",
  initial_balance: "",
  margin_allotted: "",
  aadhar_doc: null, cancelled_cheque_doc: null, pan_card_doc: null,
};

// ── Helpers ───────────────────────────────────────────────────────────────
const toBase64 = (file) =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload  = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });

const fmt = (n) => "₹" + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 });

// ── Document Viewer Modal ─────────────────────────────────────────────────
function DocumentViewer({ docData, docTitle, onClose }) {
  const isImage = docData?.startsWith('data:image/');
  const isPdf = docData?.startsWith('data:application/pdf');

  const handleDownload = () => {
    if (!docData) return;
    
    try {
      const link = document.createElement('a');
      link.href = docData;
      link.download = `${docTitle.replace(/\s+/g, '_')}_${new Date().toISOString().slice(0, 10)}`;
      if (isPdf) link.download += '.pdf';
      else if (isImage) {
        const match = docData.match(/data:image\/([a-z]+)/);
        const ext = match ? match[1] : 'png';
        link.download += `.${ext}`;
      }
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
    } catch (err) {
      console.error('Download failed:', err);
      alert('Failed to download document');
    }
  };

  return (
    <div style={S.overlay} onMouseDown={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        style={{
          ...S.modal,
          maxWidth: "90vw",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
        }}
        onMouseDown={e => e.stopPropagation()}
      >
        {/* Header */}
        <div style={S.mHeader}>
          <span style={{ fontSize: "16px", fontWeight: 700 }}>{docTitle}</span>
          <button
            onClick={onClose}
            style={{
              background: "none",
              border: "none",
              color: "#a1a1aa",
              fontSize: "20px",
              cursor: "pointer",
            }}
          >
            ✕
          </button>
        </div>

        {/* Content */}
        <div
          style={{
            flex: 1,
            overflow: "auto",
            padding: "20px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            background: "#000",
          }}
        >
          {isImage ? (
            <img
              src={docData}
              alt={docTitle}
              style={{
                maxWidth: "100%",
                maxHeight: "100%",
                objectFit: "contain",
              }}
            />
          ) : isPdf ? (
            <iframe
              src={docData}
              style={{
                width: "100%",
                height: "100%",
                border: "none",
              }}
              title={docTitle}
            />
          ) : (
            <div style={{ color: "#a1a1aa", fontSize: "14px" }}>
              Unable to preview this document type
            </div>
          )}
        </div>

        {/* Footer with Download button */}
        <div style={S.mFooter}>
          <button
            onClick={handleDownload}
            style={{
              ...S.btn("#16a34a"),
              flex: 1,
              padding: "10px",
              fontSize: "14px",
            }}
          >
            ⬇ Download
          </button>
          <button
            onClick={onClose}
            style={{
              ...S.btn("#27272a"),
              flex: 1,
              padding: "10px",
              fontSize: "14px",
              border: "1px solid #3f3f46",
            }}
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────
const S = {
  page:    { padding: "24px", fontFamily: "system-ui,sans-serif", color: "var(--text)", minHeight: "100vh" },
  card:    { background: "var(--surface)", borderRadius: "10px", border: "1px solid var(--border)", padding: "20px" },
  topBar:  { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px", flexWrap: "wrap", gap: "10px" },
  title:   { fontSize: "20px", fontWeight: 700, color: "var(--text)" },
  btn:     (color) => ({ padding: "8px 16px", borderRadius: "6px", border: "none", background: color, color: "#fff", fontSize: "13px", fontWeight: 600, cursor: "pointer" }),
  input:   { width: "100%", padding: "7px 10px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "6px", color: "var(--text)", fontSize: "13px", boxSizing: "border-box" },
  select:  { width: "100%", padding: "7px 10px", background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "6px", color: "var(--text)", fontSize: "13px", boxSizing: "border-box" },
  label:   { fontSize: "11px", color: "var(--muted)", fontWeight: 600, textTransform: "uppercase", marginBottom: "4px" },
  th:      { padding: "10px 12px", textAlign: "left", background: "var(--surface2)", borderBottom: "1px solid var(--border)", fontWeight: 600, color: "var(--muted)", fontSize: "11px", whiteSpace: "nowrap" },
  td:      { padding: "10px 12px", borderBottom: "1px solid #27272a", fontSize: "12px", color: "var(--text)", whiteSpace: "nowrap" },
  overlay: { position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", zIndex: 100, display: "flex", alignItems: "center", justifyContent: "center", padding: "16px" },
  modal:   { background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "12px", width: "100%", maxWidth: "880px", maxHeight: "90vh", overflowY: "auto" },
  mHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "18px 24px", borderBottom: "1px solid var(--border)" },
  mBody:   { padding: "20px 24px" },
  mFooter: { display: "flex", gap: "10px", padding: "16px 24px", borderTop: "1px solid var(--border)" },
  fieldGrp:{ marginBottom: "0" },
};

// ── Doc Upload field ───────────────────────────────────────────────────────
function DocField({ label, fieldKey, value, onChange, onView }) {
  const hasDoc = !!value;
  return (
    <div style={S.fieldGrp}>
      <div style={S.label}>{label}</div>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
        {hasDoc && (
          <span style={{ fontSize: "11px", color: "var(--positive-text)", fontWeight: 600 }}>
            ✓ Saved
          </span>
        )}
        <label style={{
          padding: "6px 12px", borderRadius: "6px", border: "1px solid var(--border)",
          background: "var(--surface2)", color: "var(--text)", fontSize: "12px", cursor: "pointer",
        }}>
          {hasDoc ? "Change / Upload" : "Upload"}
          <input
            type="file" accept="image/*,application/pdf" style={{ display: "none" }}
            onChange={async (e) => {
              const f = e.target.files[0];
              if (f) onChange(fieldKey, await toBase64(f));
            }}
          />
        </label>
        {hasDoc && (
          <>
            <button
              onClick={() => onView(value, label)}
              style={{
                padding: "6px 12px",
                borderRadius: "6px",
                border: "1px solid #2563eb",
                background: "#2563eb",
                color: "#fff",
                fontSize: "12px",
                cursor: "pointer",
                fontWeight: 600,
              }}
            >
              👁 View
            </button>
            <button
              onClick={() => onChange(fieldKey, null)}
              style={{ background: "none", border: "none", color: "#ef4444", fontSize: "12px", cursor: "pointer", padding: 0 }}
            >
              Remove
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ── Status badge component ─────────────────────────────────────────────────
function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || { color: "#a1a1aa", bg: "#a1a1aa11" };
  return (
    <span style={{
      padding: "2px 10px", borderRadius: "999px", fontSize: "11px",
      fontWeight: 700, color: cfg.color, background: cfg.bg,
      border: `1px solid ${cfg.color}44`,
    }}>
      {status}
    </span>
  );
}

// ── Small form helpers (MUST be module-scope so inputs keep focus) ───────────
const Field = ({ label, children }) => (
  <div>
    <div style={S.label}>{label}</div>
    {children}
  </div>
);

const Inp = ({ formData, onField, fkey, type = "text", placeholder = "" }) => (
  <input
    type={type}
    placeholder={placeholder}
    value={formData?.[fkey] || ""}
    onChange={e => onField(fkey, e.target.value)}
    style={S.input}
  />
);

// ── Main component ────────────────────────────────────────────────────────
const UsersPage = () => {
  const { user: self } = useAuth();
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= 900);

  const [users,       setUsers]       = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [search,      setSearch]      = useState("");
  const [showEntries, setShowEntries] = useState(50);
  const [selectedUserTypes, setSelectedUserTypes] = useState([]);
  const [showUserTypeDropdown, setShowUserTypeDropdown] = useState(false);

  // Modal
  const [showModal, setShowModal]   = useState(false);
  const [editingId, setEditingId]   = useState(null); // null = add, uuid = edit
  const [formData,  setFormData]    = useState({ ...EMPTY_FORM });
  const [saving,    setSaving]      = useState(false);
  const [formError, setFormError]   = useState("");

  // Document viewer
  const [showDocViewer, setShowDocViewer] = useState(false);
  const [viewingDocData, setViewingDocData] = useState(null);
  const [viewingDocTitle, setViewingDocTitle] = useState("");

  // Add Funds modal
  const [fundsModal,  setFundsModal]  = useState(false);
  const [fundsUser,   setFundsUser]   = useState(null);
  const [fundsAmount, setFundsAmount] = useState("");
  const [fundsNote,   setFundsNote]   = useState("");
  const [fundsBusy,   setFundsBusy]   = useState(false);

  const isSuperAdmin = self?.role === "SUPER_ADMIN";
  const roleOptions  = isSuperAdmin
    ? ["USER", "ADMIN", "SUPER_USER", "SUPER_ADMIN"]
    : ["USER"];

  useEffect(() => {
    const onResize = () => setIsMobile(window.innerWidth <= 900);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // ── Load users ────────────────────────────────────────────────────────
  const loadUsers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiService.get('/admin/users');
      console.log('Users API response:', res);
      console.log('Response type:', typeof res);
      console.log('Response keys:', Object.keys(res || {}));
      const userData = res?.data || res?.users || res || [];
      console.log('Extracted users:', userData);
      console.log('Users count:', Array.isArray(userData) ? userData.length : 'not an array');
      setUsers(Array.isArray(userData) ? userData : []);
    } catch (err) {
      console.error('Error loading users:', err);
      console.error('Error details:', err.message, err.status, err.data);
      setUsers([]);
    }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  // ── Open Add modal ────────────────────────────────────────────────────
  const openAdd = () => {
    setEditingId(null);
    setFormData({ ...EMPTY_FORM });
    setFormError("");
    setShowModal(true);
  };

  // ── Open Edit modal (fetch full user with docs) ───────────────────────
  const openEdit = async (u) => {
    setFormError("");
    setFormData({
      first_name:           u.first_name   || "",
      last_name:            u.last_name    || "",
      email:                u.email        || "",
      mobile:               u.mobile       || "",
      password:             "",
      role:                 u.role         || "USER",
      status:               u.status       || "ACTIVE",
      address:              u.address      || "",
      country:              u.country      || "India",
      state:                u.state        || "",
      city:                 u.city         || "",
      aadhar_number:        u.aadhar_number   || "",
      pan_number:           u.pan_number      || "",
      upi:                  u.upi             || "",
      bank_account:         u.bank_account    || "",
      brokerage_plan:       u.brokerage_plan  || BROKERAGE_PLANS[0],
      margin_allotted:      u.margin_allotted != null ? String(u.margin_allotted) : "",
      aadhar_doc:           null,
      cancelled_cheque_doc: null,
      pan_card_doc:         null,
      initial_balance:      "",
    });
    setEditingId(u.id);
    setShowModal(true);
    // Fetch full data including docs
    try {
      const full = await apiService.get(`/admin/users/${u.id}`);
      const d = full?.data?.data || full?.data || full;
      if (d) {
        setFormData(prev => ({
          ...prev,
          aadhar_doc:           d.aadhar_doc           || null,
          cancelled_cheque_doc: d.cancelled_cheque_doc || null,
          pan_card_doc:         d.pan_card_doc          || null,
        }));
      }
    } catch { /* docs stay null */ }
  };

  // ── Form field change ─────────────────────────────────────────────────
  const onField = (key, val) => setFormData(prev => ({ ...prev, [key]: val }));

  // ── Open document viewer ──────────────────────────────────────────────
  const handleViewDocument = (docData, docTitle) => {
    setViewingDocData(docData);
    setViewingDocTitle(docTitle);
    setShowDocViewer(true);
  };

  // ── Submit add / edit ─────────────────────────────────────────────────
  const handleSubmit = async () => {
    setFormError("");
    if (!formData.first_name.trim()) { setFormError("First name is required."); return; }
    if (!formData.mobile.trim())     { setFormError("Mobile is required."); return; }
    if (!editingId && !formData.password.trim()) { setFormError("Password is required for new users."); return; }

    setSaving(true);
    try {
      if (!editingId) {
        const payload = { ...formData };
        if (payload.initial_balance !== "") payload.initial_balance = Number(payload.initial_balance);
        if (payload.margin_allotted !== "") payload.margin_allotted = Number(payload.margin_allotted);
        if (!payload.initial_balance) delete payload.initial_balance;
        if (payload.margin_allotted === "") delete payload.margin_allotted;
        await apiService.post('/admin/users', payload);
      } else {
        const payload = { ...formData };
        if (payload.margin_allotted !== "") payload.margin_allotted = Number(payload.margin_allotted);
        if (!payload.password) delete payload.password;
        delete payload.initial_balance;
        if (payload.margin_allotted === "") delete payload.margin_allotted;
        await apiService.patch(`/admin/users/${editingId}`, payload);
      }
      setShowModal(false);
      await loadUsers();
    } catch (err) {
      setFormError(err?.data?.detail || err?.response?.data?.detail || err?.message || "Save failed.");
    } finally { setSaving(false); }
  };

  // ── Add Funds ─────────────────────────────────────────────────────────
  const openFunds = (u) => {
    setFundsUser(u);
    setFundsAmount("");
    setFundsNote("");
    setFundsModal(true);
  };

  const submitFunds = async () => {
    const amt = parseFloat(fundsAmount);
    if (!amt || isNaN(amt)) return;
    setFundsBusy(true);
    try {
      await apiService.post(`/admin/users/${fundsUser.id}/funds`, { amount: amt, note: fundsNote });
      setFundsModal(false);
      await loadUsers();
    } catch (err) {
      alert(err?.data?.detail || err?.response?.data?.detail || "Failed to adjust funds.");
    } finally { setFundsBusy(false); }
  };

  // ── Filter ────────────────────────────────────────────────────────────
  const q = search.toLowerCase();
  const filtered = users.filter(u => {
    // Text search filter
    const matchesSearch = !q ||
      String(u.user_no || "").includes(q) ||
      (u.first_name || "").toLowerCase().includes(q) ||
      (u.last_name  || "").toLowerCase().includes(q) ||
      (u.email      || "").toLowerCase().includes(q) ||
      (u.mobile     || "").toLowerCase().includes(q) ||
      (u.role       || "").toLowerCase().includes(q) ||
      (u.status     || "").toLowerCase().includes(q);
    
    // User Type filter
    const matchesUserType = selectedUserTypes.length === 0 || selectedUserTypes.includes(u.role);
    
    return matchesSearch && matchesUserType;
  });
  const displayed = filtered.slice(0, showEntries);

  // User type filter toggle handler
  const toggleUserType = (type) => {
    setSelectedUserTypes(prev => 
      prev.includes(type) ? prev.filter(t => t !== type) : [...prev, type]
    );
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (e) => {
      if (showUserTypeDropdown && !e.target.closest('.usertype-dropdown-container')) {
        setShowUserTypeDropdown(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [showUserTypeDropdown]);

  const allUserTypes = isSuperAdmin
    ? ["USER", "ADMIN", "SUPER_USER", "SUPER_ADMIN"]
    : ["USER"];

  // ── Render ────────────────────────────────────────────────────────────
  return (
    <div style={{ ...S.page, padding: isMobile ? "12px" : "24px" }}>
      <div style={{ ...S.card, padding: isMobile ? "12px" : "20px" }}>

        {/* Top bar */}
        <div style={S.topBar}>
          <div style={S.title}>Users</div>
          <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
            <label style={{ color: "var(--muted)", fontSize: "13px" }}>
              Show{" "}
              <select
                value={showEntries}
                onChange={e => setShowEntries(Number(e.target.value))}
                style={{ ...S.select, width: "70px", display: "inline-block" }}
              >
                {[10, 25, 50, 100].map(n => <option key={n}>{n}</option>)}
              </select>{" "}
              entries
            </label>
            
            {/* User Type Multi-Select Filter */}
            <div className="usertype-dropdown-container" style={{ position: "relative" }}>
              <button
                onClick={() => setShowUserTypeDropdown(!showUserTypeDropdown)}
                style={{
                  ...S.btn(selectedUserTypes.length > 0 ? "#2563eb" : "#374151"),
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  position: "relative",
                }}
              >
                User Type
                {selectedUserTypes.length > 0 && (
                  <span style={{
                    background: "#fbbf24",
                    color: "#000",
                    borderRadius: "999px",
                    padding: "1px 6px",
                    fontSize: "10px",
                    fontWeight: 700,
                  }}>
                    {selectedUserTypes.length}
                  </span>
                )}
                <svg style={{ width: "12px", height: "12px" }} fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </button>
              
              {showUserTypeDropdown && (
                <div style={{
                  position: "absolute",
                  top: "calc(100% + 4px)",
                  left: 0,
                  background: "var(--surface)",
                  border: "1px solid var(--border)",
                  borderRadius: "8px",
                  boxShadow: "0 4px 12px rgba(0,0,0,0.3)",
                  minWidth: "180px",
                  zIndex: 1000,
                  padding: "8px",
                }}>
                  {allUserTypes.map(type => (
                    <label
                      key={type}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        padding: "8px 10px",
                        cursor: "pointer",
                        borderRadius: "4px",
                        fontSize: "13px",
                        color: "var(--text)",
                        transition: "background 0.15s",
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = "var(--surface2)"}
                      onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                    >
                      <input
                        type="checkbox"
                        checked={selectedUserTypes.includes(type)}
                        onChange={() => toggleUserType(type)}
                        style={{
                          marginRight: "8px",
                          cursor: "pointer",
                          width: "14px",
                          height: "14px",
                        }}
                      />
                      <span style={{
                        padding: "2px 8px",
                        borderRadius: "999px",
                        fontSize: "10px",
                        fontWeight: 700,
                        color: (type === "SUPER_USER" || type === "USER") ? "#111827" : "#fff",
                        background: ROLE_COLORS[type] || "#6b7280",
                      }}>
                        {type}
                      </span>
                    </label>
                  ))}
                  
                  {selectedUserTypes.length > 0 && (
                    <div style={{
                      borderTop: "1px solid var(--border)",
                      marginTop: "4px",
                      paddingTop: "4px",
                    }}>
                      <button
                        onClick={() => setSelectedUserTypes([])}
                        style={{
                          width: "100%",
                          padding: "6px",
                          background: "transparent",
                          border: "1px solid var(--border)",
                          borderRadius: "4px",
                          color: "var(--muted)",
                          fontSize: "11px",
                          cursor: "pointer",
                          fontWeight: 600,
                        }}
                        onMouseEnter={e => e.currentTarget.style.background = "var(--surface2)"}
                        onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                      >
                        Clear All
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
            
            <input
              placeholder="Search…"
              value={search}
              onChange={e => setSearch(e.target.value)}
              style={{ ...S.input, width: isMobile ? "100%" : "200px", minWidth: isMobile ? "220px" : "auto" }}
            />
            <button style={S.btn("#2563eb")} onClick={openAdd}>+ Add User</button>
          </div>
        </div>

        {/* Table */}
        <div style={{ overflowX: "auto", borderRadius: "8px", border: "1px solid #3f3f46" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                {["ID","First Name","Last Name","User Type","Email","Created On","Mobile","Wallet Balance","Status","Actions"].map(h => (
                  <th key={h} style={S.th}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={10} style={{ ...S.td, textAlign: "center", color: "var(--text)", padding: "32px" }}>Loading…</td></tr>
              ) : displayed.length === 0 ? (
                <tr><td colSpan={10} style={{ ...S.td, textAlign: "center", color: "var(--text)", padding: "32px" }}>No matching records found.</td></tr>
              ) : displayed.map(u => (
                <tr key={u.id}>
                  <td style={{ ...S.td, color: "var(--text)" }}>{u.user_no || "—"}</td>
                  <td style={S.td}>{u.first_name || u.name || "—"}</td>
                  <td style={S.td}>{u.last_name || "—"}</td>
                  <td style={S.td}>
                    <span style={{
                      padding: "2px 8px", borderRadius: "999px", fontSize: "10px",
                      fontWeight: 700, color: (u.role === "SUPER_USER" || u.role === "USER") ? "#111827" : "#fff",
                      background: ROLE_COLORS[u.role] || "#6b7280",
                    }}>{u.role}</span>
                  </td>
                  <td style={{ ...S.td, color: "var(--text)" }}>{u.email || "—"}</td>
                  <td style={{ ...S.td, color: "var(--text)" }}>
                    {u.created_at ? new Date(u.created_at).toLocaleDateString("en-IN") : "—"}
                  </td>
                  <td style={S.td}>{u.mobile}</td>
                  <td style={{ ...S.td, fontVariantNumeric: "tabular-nums" }}>
                    <span style={{ color: Number(u.wallet_balance) < 0 ? "#ef4444" : "var(--text)" }}>
                      {fmt(u.wallet_balance)}
                    </span>
                  </td>
                  <td style={S.td}>
                    <StatusBadge status={u.status || (u.is_active !== false ? "ACTIVE" : "BLOCKED")} />
                  </td>
                  <td style={S.td}>
                    <div style={{ display: "flex", gap: "6px" }}>
                      <button style={S.btn("#374151")} onClick={() => openEdit(u)}>Edit</button>
                      <button style={S.btn("#1d4ed8")} onClick={() => openFunds(u)}>Add Funds</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Footer count */}
        <div style={{ marginTop: "12px", fontSize: "12px", color: "var(--muted)" }}>
          Showing 1–{Math.min(displayed.length, filtered.length)} of {filtered.length} entries
          {q && ` (filtered from ${users.length} total)`}
        </div>
      </div>

      {/* ── Add / Edit Modal ─────────────────────────────────────────── */}
      {showModal && (
        <div style={S.overlay} onMouseDown={e => { if (e.target === e.currentTarget) setShowModal(false); }}>
          <div style={{ ...S.modal, maxWidth: isMobile ? "96vw" : "880px" }} onMouseDown={e => e.stopPropagation()}>
            <div style={S.mHeader}>
              <span style={{ fontSize: "16px", fontWeight: 700 }}>
                {editingId ? "Edit User" : "Add New User"}
              </span>
              <button onClick={() => setShowModal(false)}
                style={{ background: "none", border: "none", color: "#a1a1aa", fontSize: "20px", cursor: "pointer" }}>✕</button>
            </div>

            <div style={{ ...S.mBody, padding: isMobile ? "14px" : "20px 24px" }}>
              {formError && (
                <div style={{ marginBottom: "14px", padding: "10px 14px", background: "#7f1d1d33", border: "1px solid #ef4444", borderRadius: "6px", color: "#fca5a5", fontSize: "13px" }}>
                  {formError}
                </div>
              )}

              {/* ── Admin fields: role, status, password */}
              <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: "14px", marginBottom: "16px", paddingBottom: "16px", borderBottom: "1px solid #3f3f46" }}>
                <Field label="Role">
                  <select value={formData.role} onChange={e => onField("role", e.target.value)} style={S.select}>
                    {roleOptions.map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </Field>
                <Field label="Account Status">
                  <select value={formData.status} onChange={e => onField("status", e.target.value)} style={S.select}>
                    {["PENDING","ACTIVE","SUSPENDED","BLOCKED"].map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </Field>
                <Field label={editingId ? "New Password (leave blank to keep)" : "Password *"}>
                  <Inp formData={formData} onField={onField} fkey="password" type="password" placeholder={editingId ? "Leave blank to keep current" : "Set password"} />
                </Field>
                <Field label="Allotted Margin (₹)">
                  <Inp formData={formData} onField={onField} fkey="margin_allotted" type="number" placeholder="0.00" />
                </Field>
                {!editingId && (
                  <Field label="Initial Wallet Balance (₹)">
                    <Inp formData={formData} onField={onField} fkey="initial_balance" type="number" placeholder="0.00" />
                  </Field>
                )}
              </div>

              {/* ── Two-column layout: personal left, KYC right */}
              <div style={{ display: "grid", gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr", gap: "24px" }}>

                {/* LEFT: Personal details */}
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  <div style={{ fontWeight: 700, fontSize: "12px", color: "#a1a1aa", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    Personal Details
                  </div>
                  <Field label="First Name *"><Inp formData={formData} onField={onField} fkey="first_name" /></Field>
                  <Field label="Last Name"><Inp formData={formData} onField={onField} fkey="last_name" /></Field>
                  <Field label="Email"><Inp formData={formData} onField={onField} fkey="email" type="email" /></Field>
                  <Field label="Mobile *"><Inp formData={formData} onField={onField} fkey="mobile" /></Field>
                  <Field label="Address">
                    <textarea
                      value={formData.address || ""}
                      onChange={e => onField("address", e.target.value)}
                      style={{ ...S.input, height: "60px", resize: "vertical" }}
                    />
                  </Field>
                  <Field label="Country">
                    <input value={formData.country || "India"} onChange={e => onField("country", e.target.value)} style={S.input} />
                  </Field>
                  <Field label="State">
                    <select value={formData.state || ""} onChange={e => onField("state", e.target.value)} style={S.select}>
                      <option value="">— Select —</option>
                      {INDIAN_STATES.map(s => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </Field>
                  <Field label="City"><Inp formData={formData} onField={onField} fkey="city" /></Field>
                </div>

                {/* RIGHT: KYC & financial */}
                <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                  <div style={{ fontWeight: 700, fontSize: "12px", color: "#a1a1aa", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                    KYC & Financial
                  </div>
                  <Field label="Aadhar Number"><Inp formData={formData} onField={onField} fkey="aadhar_number" placeholder="XXXX XXXX XXXX" /></Field>
                  <Field label="PAN Number"><Inp formData={formData} onField={onField} fkey="pan_number" placeholder="ABCDE1234F" /></Field>
                  <Field label="UPI"><Inp formData={formData} onField={onField} fkey="upi" placeholder="name@upi" /></Field>
                  <Field label="Bank Account"><Inp formData={formData} onField={onField} fkey="bank_account" placeholder="Account number / IFSC" /></Field>

                  <div style={{ height: "1px", background: "var(--border)" }} />

                  <DocField label="Aadhar Card"       fieldKey="aadhar_doc"           value={formData.aadhar_doc}           onChange={onField} onView={handleViewDocument} />
                  <DocField label="Cancelled Cheque"  fieldKey="cancelled_cheque_doc" value={formData.cancelled_cheque_doc} onChange={onField} onView={handleViewDocument} />
                  <DocField label="PAN Card"           fieldKey="pan_card_doc"          value={formData.pan_card_doc}          onChange={onField} onView={handleViewDocument} />

                  <div style={{ height: "1px", background: "var(--border)" }} />

                  <Field label="Brokerage Plan">
                    <select value={formData.brokerage_plan || BROKERAGE_PLANS[0]} onChange={e => onField("brokerage_plan", e.target.value)} style={S.select}>
                      {BROKERAGE_PLANS.map(p => <option key={p} value={p}>{p}</option>)}
                    </select>
                  </Field>
                </div>
              </div>
            </div>

            <div style={{ ...S.mFooter, flexDirection: isMobile ? "column" : "row", padding: isMobile ? "12px 14px" : "16px 24px" }}>
              <button onClick={handleSubmit} disabled={saving}
                style={{ ...S.btn("#2563eb"), flex: 1, padding: "10px", fontSize: "14px" }}>
                {saving ? "Saving…" : "SAVE DETAILS"}
              </button>
              <button onClick={() => setShowModal(false)}
                style={{ ...S.btn("#27272a"), flex: 1, padding: "10px", fontSize: "14px", border: "1px solid #3f3f46" }}>
                CANCEL
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Document Viewer Modal ───────────────────────────────────────── */}
      {showDocViewer && (
        <DocumentViewer
          docData={viewingDocData}
          docTitle={viewingDocTitle}
          onClose={() => setShowDocViewer(false)}
        />
      )}

      {/* ── Add Funds Modal ───────────────────────────────────────────── */}
      {fundsModal && fundsUser && (
        <div style={S.overlay} onMouseDown={e => { if (e.target === e.currentTarget) setFundsModal(false); }}>
          <div style={{ ...S.modal, maxWidth: "420px" }} onMouseDown={e => e.stopPropagation()}>
            <div style={S.mHeader}>
              <span style={{ fontSize: "16px", fontWeight: 700 }}>Add / Deduct Funds</span>
              <button onClick={() => setFundsModal(false)}
                style={{ background: "none", border: "none", color: "#a1a1aa", fontSize: "20px", cursor: "pointer" }}>✕</button>
            </div>
            <div style={S.mBody}>
              <div style={{ marginBottom: "14px", padding: "12px 14px", background: "var(--surface2)", borderRadius: "8px", fontSize: "13px" }}>
                <div style={{ color: "var(--muted)", marginBottom: "4px" }}>User</div>
                <div style={{ fontWeight: 700 }}>
                  {fundsUser.first_name} {fundsUser.last_name} ({fundsUser.mobile})
                </div>
                <div style={{ color: "var(--muted)", marginTop: "6px" }}>
                  Current Balance:{" "}
                  <span style={{ fontWeight: 700, color: "var(--text)" }}>{fmt(fundsUser.wallet_balance)}</span>
                </div>
              </div>
              <div style={{ marginBottom: "12px" }}>
                <div style={S.label}>Amount (positive = credit, negative = debit)</div>
                <input
                  type="number" placeholder="e.g. 5000 or -1000"
                  value={fundsAmount} onChange={e => setFundsAmount(e.target.value)}
                  style={S.input}
                />
              </div>
              <div>
                <div style={S.label}>Note (optional)</div>
                <input
                  placeholder="e.g. Manual adjustment"
                  value={fundsNote} onChange={e => setFundsNote(e.target.value)}
                  style={S.input}
                />
              </div>
            </div>
            <div style={S.mFooter}>
              <button onClick={submitFunds} disabled={fundsBusy || !fundsAmount}
                style={{ ...S.btn("#2563eb"), flex: 1, padding: "10px", fontSize: "14px" }}>
                {fundsBusy ? "Processing…" : "Confirm"}
              </button>
              <button onClick={() => setFundsModal(false)}
                style={{ ...S.btn("#27272a"), flex: 1, padding: "10px", fontSize: "14px", border: "1px solid #3f3f46" }}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default UsersPage;
