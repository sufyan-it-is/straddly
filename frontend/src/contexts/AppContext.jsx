import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { apiService } from '../services/apiService';
import { useAuth } from './AuthContext';

const AppContext = createContext(null);

export const AppProvider = ({ children }) => {
  const { isAuthenticated, user } = useAuth();
  const isAdmin = user?.role === 'ADMIN' || user?.role === 'SUPER_ADMIN';

  const [users, setUsers]             = useState([]);
  const [orders, setOrders]           = useState([]);
  const [positions, setPositions]     = useState([]);
  const [baskets, setBaskets]         = useState([]);
  const [loadingUsers, setLoadingUsers] = useState(false);

  const loadUsers = useCallback(async () => {
    if (!isAuthenticated || !isAdmin) {
      setUsers([]);
      return;
    }
    setLoadingUsers(true);
    try {
      const data = await apiService.get('/admin/users');
      setUsers(Array.isArray(data) ? data : data.data || data.users || []);
    } catch (e) {
      console.warn('Could not load users:', e.message);
      console.error('Users load error:', e);
      setUsers([]);
    } finally {
      setLoadingUsers(false);
    }
  }, [isAuthenticated, isAdmin]);

  useEffect(() => {
    if (isAuthenticated) loadUsers();
  }, [isAuthenticated, loadUsers]);

  const refreshOrders = useCallback(async () => {
    try {
      const data = await apiService.get('/trading/orders');
      setOrders(Array.isArray(data) ? data : data.orders || []);
    } catch (e) {
      console.warn('Could not load orders:', e.message);
    }
  }, []);

  const refreshPositions = useCallback(async () => {
    try {
      const data = await apiService.get('/portfolio/positions');
      setPositions(Array.isArray(data) ? data : data.positions || []);
    } catch (e) {
      console.warn('Could not load positions:', e.message);
    }
  }, []);

  const refreshBaskets = useCallback(async () => {
    try {
      const data = await apiService.get('/trading/basket-orders');
      setBaskets(Array.isArray(data) ? data : data.baskets || []);
    } catch (e) {
      console.warn('Could not load baskets:', e.message);
    }
  }, []);

  const value = {
    users,
    orders,
    positions,
    baskets,
    loadingUsers,
    loadUsers,
    refreshOrders,
    refreshPositions,
    refreshBaskets,
    setOrders,
    setPositions,
    setBaskets,
  };

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
};

export const useApp = () => {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error('useApp must be used inside AppProvider');
  return ctx;
};

export default AppContext;
