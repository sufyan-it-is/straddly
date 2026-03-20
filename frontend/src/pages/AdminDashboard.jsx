import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { ADMIN_DASHBOARD_TABS } from '../constants/adminDashboardTabs';

const AdminDashboard = () => {
  const navigate = useNavigate();
  const { user, hasPermission, hasRole } = useAuth();

  const isSuperAdmin = hasRole('SUPER_ADMIN');
  const visibleTabs = isSuperAdmin
    ? ADMIN_DASHBOARD_TABS
    : ADMIN_DASHBOARD_TABS.filter((tab) => hasPermission(tab.permission));

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-zinc-700 bg-zinc-900 p-5">
        <h2 className="text-base font-semibold text-zinc-100">Admin Dashboard</h2>
        <p className="mt-1 text-xs text-zinc-400">
          Welcome {user?.name || user?.mobile || 'Admin'}. Use the tabs below to access your assigned admin modules.
        </p>
      </div>

      {visibleTabs.length === 0 ? (
        <div className="rounded-xl border border-zinc-700 bg-zinc-900 p-5">
          <p className="text-sm text-zinc-300">No admin tabs are currently assigned to your account. Contact Super Admin.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          {visibleTabs.map((tab) => (
            <div key={tab.id} className="rounded-xl border border-zinc-700 bg-zinc-900 p-4 space-y-3">
              <div>
                <h3 className="text-sm font-semibold text-zinc-100">{tab.label}</h3>
                <p className="text-xs text-zinc-400 mt-1">{tab.description}</p>
              </div>
              <button
                type="button"
                onClick={() => navigate(tab.path)}
                className="px-3 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white text-xs font-medium"
              >
                Open {tab.label}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

export default AdminDashboard;
