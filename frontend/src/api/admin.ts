/** src/api/admin.ts — typed wrappers for all /admin endpoints */
import api from './client'

// ── Types ────────────────────────────────────────────────────────────────────

export interface TokenStatus {
  effective_mode:   'auto_totp' | 'manual' | 'static_ip' | 'disabled'
  totp_configured:  boolean
  token_expiry_utc: string | null
  time_remaining:   string
  expiring_soon:    boolean
}

export interface AuthModeStatus {
  effective_mode:   'auto_totp' | 'manual' | 'static_ip' | 'disabled'
  db_mode:          string
  totp_configured:  boolean
  description:      { auto_totp: string; manual: string }
}

export interface WsStatus {
  live_feed_slots: Record<string, unknown>[]
  depth_ws:        Record<string, unknown>
  pending_orders:  number
}

export interface RateLimitStats {
  total_calls:     number
  throttle_events: number
  endpoints:       Record<string, unknown>
}

// ── API calls ────────────────────────────────────────────────────────────────

export const fetchTokenStatus    = () => api.get<TokenStatus>('/admin/token/status')
export const forceTokenRefresh   = () => api.post<{ success: boolean; new_expiry: string | null }>('/admin/token/refresh')

export const fetchAuthMode       = () => api.get<AuthModeStatus>('/admin/auth-mode')
export const setAuthMode         = (mode: 'auto_totp' | 'manual' | 'static_ip') =>
  api.post<{ success: boolean; message: string; previous_mode: string; current_mode: string; verification: string; error?: string | null }>('/admin/auth-mode/switch', { auth_mode: mode })

export const switchAuthMode      = (payload: { auth_mode: 'auto_totp' | 'manual' | 'static_ip'; force?: boolean; daily_token?: string }) =>
  api.post<{ success: boolean; message: string; previous_mode: string; current_mode: string; verification: string; error?: string | null }>('/admin/auth-mode/switch', payload)

export const rotateToken         = (access_token: string, reconnect_ws = true) =>
  api.post<{ success: boolean; message: string }>('/admin/credentials/rotate', { access_token, reconnect_ws })

export const fetchCredentials    = () =>
  api.get<{ client_id: string; token_masked: string }>('/admin/credentials')

export const fetchWsStatus       = () => api.get<WsStatus>('/admin/ws/status')
export const fetchRateLimits     = () => api.get<RateLimitStats>('/admin/rate-limits')
export const fetchSubscriptions  = () => api.get('/admin/subscriptions')
