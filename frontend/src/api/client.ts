/**
 * src/api/client.ts
 * Axios instance for Straddly API.
 *
 * VITE_API_URL is baked in at Docker build time for production:
 *   e.g.  https://api.straddly.pro/api/v2
 * Falls back to /api/v2 for local dev (Nginx / Vite proxy).
 */
import axios from 'axios'

const envApiUrl = ((import.meta.env.VITE_API_URL as string) || '').trim()
const isNativeCapacitor =
  typeof window !== 'undefined' &&
  typeof (window as Window & { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor !== 'undefined' &&
  !!(window as Window & { Capacitor?: { isNativePlatform?: () => boolean } }).Capacitor?.isNativePlatform?.()

const fallbackApiUrl = isNativeCapacitor
  ? 'https://api.straddly.pro/api/v2'
  : '/api/v2'

const api = axios.create({
  baseURL: envApiUrl || fallbackApiUrl,
  timeout: 15_000,
  headers: { 'Content-Type': 'application/json' },
})

export default api
