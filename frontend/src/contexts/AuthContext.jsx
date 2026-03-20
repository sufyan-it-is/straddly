import React, { createContext, useContext, useState, useEffect } from 'react';
import { authService } from '../services/authService';
import { apiService } from '../services/apiService';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const onExpired = () => {
      authService.logout();
      setUser(null);
      setToken(null);
    };
    window.addEventListener('tn-auth-expired', onExpired);
    return () => window.removeEventListener('tn-auth-expired', onExpired);
  }, []);

  useEffect(() => {
    const storedToken = localStorage.getItem('authToken');
    const storedUser  = (() => {
      try { return JSON.parse(localStorage.getItem('authUser') || 'null'); } catch { return null; }
    })();
    if (storedToken && storedUser) {
      setToken(storedToken);
      setUser(storedUser);
      apiService.setAuthToken(storedToken);

      // Validate token with backend; if DB was reset or session expired, force logout.
      apiService.get('/auth/me').then((liveUser) => {
        if (liveUser?.id) {
          setUser(liveUser);
          try { localStorage.setItem('authUser', JSON.stringify(liveUser)); } catch {}
        }
      }).catch((err) => {
        if (err?.status === 401) {
          authService.logout();
          setUser(null);
          setToken(null);
        }
      });
    }
    setLoading(false);
  }, []);

  const login = async (credentials) => {
    const result = await authService.login(credentials);
    if (result.success) {
      const t = result.token || result.access_token;
      setUser(result.user);
      setToken(t);
      if (t) {
        localStorage.setItem('authToken', t);
        localStorage.setItem('authUser', JSON.stringify(result.user));
        apiService.setAuthToken(t);
      }
    }
    return result;
  };

  const logout = () => {
    authService.logout();
    setUser(null);
    setToken(null);
  };

  const hasRole = (role) => {
    if (!user) return false;
    if (Array.isArray(role)) return role.includes(user.role);
    return user.role === role;
  };

  const hasPermission = (permission) => {
    if (!user) return false;
    if (user.role === 'SUPER_ADMIN') return true;
    if (user.permissions && Array.isArray(user.permissions)) {
      return user.permissions.includes(permission);
    }
    return false;
  };

  const value = {
    user,
    token,
    isAuthenticated: !!token && !!user,
    loading,
    login,
    logout,
    hasRole,
    hasPermission,
  };

  if (loading) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'#0d1117', color:'#e6edf3' }}>
        Loading...
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
};

export default AuthContext;
