import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../../contexts/AuthContext';

const ProtectedRoute = ({ children, requiredRoles = [], requiredPermission = null }) => {
  const { isAuthenticated, hasRole, hasPermission, loading } = useAuth();
  const location = useLocation();

  if (loading) return null;

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (requiredRoles && requiredRoles.length > 0) {
    const permitted = requiredRoles.some((role) => hasRole(role));
    if (!permitted) {
      return <Navigate to="/trade" replace />;
    }
  }

  if (requiredPermission && !hasPermission(requiredPermission)) {
    return <Navigate to="/admin-dashboard" replace />;
  }

  return children;
};

export default ProtectedRoute;
