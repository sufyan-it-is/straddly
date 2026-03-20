import React, { lazy, Suspense } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import { AppProvider } from './contexts/AppContext';
import ErrorBoundary from './components/core/ErrorBoundary';
import ProtectedRoute from './components/core/ProtectedRoute';
import Layout from './components/core/Layout';

const Login = lazy(() => import('./pages/Login'));
const Trade = lazy(() => import('./pages/Trade'));
const PositionsMIS = lazy(() => import('./pages/PositionsMIS'));
const PositionsNormal = lazy(() => import('./pages/PositionsNormal'));
const PositionsUserwise = lazy(() => import('./pages/PositionsUserwise'));
const Portfolio = lazy(() => import('./pages/Portfolio'));
const PandL = lazy(() => import('./pages/PandL'));
const Users = lazy(() => import('./pages/Users'));
const Payouts = lazy(() => import('./pages/Payouts'));
const Ledger = lazy(() => import('./pages/Ledger'));
const TradeHistory = lazy(() => import('./pages/HistoricOrders'));
const Profile = lazy(() => import('./pages/Profile'));
const SuperAdmin = lazy(() => import('./pages/SuperAdmin'));
const AdminDashboard = lazy(() => import('./pages/AdminDashboard'));
const Payin = lazy(() => import('./pages/Payin'));
const MarketData = lazy(() => import('./pages/MarketData'));
const Chart = lazy(() => import('./pages/Chart'));

const LandingPage = lazy(() => import('./pages/nexus/LandingPage'));
const CourseEnrollPage = lazy(() => import('./pages/nexus/course-enroll'));
const AccountSignupPage = lazy(() => import('./pages/nexus/AccountSignupPage'));
const CrashCourse = lazy(() => import('./pages/nexus/CrashCourse'));
const AboutPage = lazy(() => import('./pages/nexus/AboutPage'));
const FundedProgram = lazy(() => import('./pages/nexus/FundedProgram'));
const Rules = lazy(() => import('./pages/nexus/Rules'));
const Background = lazy(() => import('./components/nexus/Background'));

const Loader = () => (
  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', background: '#0d1117', color: '#e6edf3', fontSize: '16px' }}>
    Loading...
  </div>
);

const NexusPortal = () => {
  return (
    <Suspense fallback={<Loader />}>
      <Background />
      <Routes>
        <Route path="/" element={<CrashCourse />} />
        <Route path="/landingpage" element={<CrashCourse />} />
        <Route path="/home" element={<LandingPage />} />
        <Route path="/login" element={<Login />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/course" element={<CrashCourse />} />
        <Route path="/enroll" element={<CourseEnrollPage />} />
        <Route path="/sign-up" element={<AccountSignupPage />} />
        <Route path="/crash-course" element={<CrashCourse />} />
        <Route path="/funded" element={<FundedProgram />} />
        <Route path="/rules" element={<Rules />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  );
};

export default function App() {
  const hostname = window.location.hostname.toLowerCase();
  const isEducationalPortal = hostname === 'learn.straddly.pro' || hostname.startsWith('learn.');

  return (
    <ErrorBoundary>
      <BrowserRouter>
        <AuthProvider>
          <AppProvider>
            {isEducationalPortal ? (
              <NexusPortal />
            ) : (
              <Suspense fallback={<Loader />}>
                <Routes>
                  <Route path="/" element={<LandingPage />} />
                  <Route path="/landingpage" element={<LandingPage />} />
                  <Route path="/about" element={<AboutPage />} />
                  <Route path="/course" element={<CrashCourse />} />
                  <Route path="/enroll" element={<CourseEnrollPage />} />
                  <Route path="/sign-up" element={<AccountSignupPage />} />
                  <Route path="/crash-course" element={<CrashCourse />} />
                  <Route path="/funded" element={<FundedProgram />} />
                  <Route path="/rules" element={<Rules />} />
                  <Route path="/login" element={<Login />} />
                  <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
                    <Route path="/trade" element={<Trade />} />
                    <Route path="/trade/all-positions" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_positions_mis"><PositionsMIS /></ProtectedRoute>
                    } />
                    <Route path="/all-positions-normal" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_positions_normal"><PositionsNormal /></ProtectedRoute>
                    } />
                    <Route path="/all-positions-userwise" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_positions_userwise"><PositionsUserwise /></ProtectedRoute>
                    } />
                    <Route path="/pandl" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_pnl"><PandL /></ProtectedRoute>
                    } />
                    <Route path="/users" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_users"><Users /></ProtectedRoute>
                    } />
                    <Route path="/payouts" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_payouts"><Payouts /></ProtectedRoute>
                    } />
                    <Route path="/ledger" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_ledger"><Ledger /></ProtectedRoute>
                    } />
                    <Route path="/trade-history" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']} requiredPermission="admin_tab_trade_history"><TradeHistory /></ProtectedRoute>
                    } />
                    <Route path="/profile" element={<Profile />} />
                    <Route path="/payin" element={<Payin />} />
                    <Route path="/portfolio" element={<Portfolio />} />
                    <Route path="/market-data" element={<MarketData />} />
                    <Route path="/chart" element={<Chart />} />
                    <Route path="/dashboard" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']}><SuperAdmin /></ProtectedRoute>
                    } />
                    <Route path="/admin-dashboard" element={
                      <ProtectedRoute requiredRoles={['ADMIN', 'SUPER_ADMIN']}><AdminDashboard /></ProtectedRoute>
                    } />
                  </Route>
                  <Route path="*" element={<Navigate to="/trade" replace />} />
                </Routes>
              </Suspense>
            )}
          </AppProvider>
        </AuthProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
