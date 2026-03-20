import React from 'react';
import { Outlet } from 'react-router-dom';
import Header from './Header';

const Layout = () => (
  <div className="min-h-screen" style={{ background: 'var(--bg)', color: 'var(--text)' }}>
    <Header />
    <Outlet />
  </div>
);

export default Layout;
