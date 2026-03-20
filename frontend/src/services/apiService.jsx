/**
 * apiService — thin adapter for Trading Nexus backend at /api/v2
 * Preserves the same interface the original UI components expect.
 *
 * VITE_API_URL is baked in at Docker build time for production:
 *   e.g.  https://api.tradingnexus.pro/api/v2
 * For dev/prod, default is same-origin '/api/v2' (Vite dev proxy / Nginx reverse proxy)
 */

const rawEnvBase = (import.meta.env.VITE_API_URL || '').trim();
const BASE_URL = rawEnvBase ? rawEnvBase.replace(/\/+$/, '') : '/api/v2';

class ApiService {
  constructor() {
    this.baseURL = BASE_URL;
    this._token = localStorage.getItem('authToken') || null;
    this._cache = new Map();
  }

  setAuthToken(token) {
    this._token = token;
    if (token) {
      localStorage.setItem('authToken', token);
    } else {
      localStorage.removeItem('authToken');
    }
  }

  _handleUnauthorized() {
    try {
      localStorage.removeItem('authToken');
      localStorage.removeItem('authUser');
    } catch {
      // ignore
    }
    this._token = null;
    try {
      window.dispatchEvent(new CustomEvent('tn-auth-expired'));
    } catch {
      // ignore
    }
  }

  _getHeaders(extra = {}) {
    const headers = { 'Content-Type': 'application/json', ...extra };
    if (this._token) {
      headers['Authorization'] = `Bearer ${this._token}`;
      // Backend auth supports X-AUTH as a first-class token header.
      // Some reverse proxies / hosting setups may drop Authorization unless explicitly forwarded.
      headers['X-AUTH'] = String(this._token);
    }
    const user = (() => {
      try { return JSON.parse(localStorage.getItem('authUser') || '{}'); } catch { return {}; }
    })();
    if (user?.id) headers['X-USER'] = String(user.id);
    return headers;
  }

  _buildUrl(endpoint, params) {
    const rawEndpoint = endpoint || '';
    if (/^https?:\/\//i.test(rawEndpoint)) {
      if (!params || Object.keys(params).length === 0) return rawEndpoint;
      const qs = new URLSearchParams(
        Object.entries(params).filter(([, v]) => v !== undefined && v !== null)
      ).toString();
      return qs ? `${rawEndpoint}?${qs}` : rawEndpoint;
    }

    const endpointPath = rawEndpoint.startsWith('/') ? rawEndpoint : `/${rawEndpoint}`;
    const baseClean = String(this.baseURL || '').replace(/\/+$/, '');

    // Compatibility: many callers already include /api/v2 in endpoint.
    // Avoid generating /api/v2/api/v2/... URLs.
    let base = `${baseClean}${endpointPath}`;
    if (endpointPath.startsWith(`${baseClean}/`)) {
      base = endpointPath;
    } else if (/^https?:\/\//i.test(baseClean)) {
      try {
        const baseUrl = new URL(baseClean);
        const basePath = baseUrl.pathname.replace(/\/+$/, '');
        if (basePath && endpointPath.startsWith(`${basePath}/`)) {
          base = `${baseUrl.origin}${endpointPath}`;
        }
      } catch {
        // Fallback to computed base above.
      }
    }

    if (!params || Object.keys(params).length === 0) return base;
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== undefined && v !== null)
    ).toString();
    return qs ? `${base}?${qs}` : base;
  }

  _cacheKey(endpoint, params) {
    return JSON.stringify({ endpoint, params: params || {} });
  }

  clearCacheEntry(endpoint, params) {
    this._cache.delete(this._cacheKey(endpoint, params));
  }

  clearCache() {
    this._cache.clear();
  }

  async get(endpoint, params = {}) {
    const url = this._buildUrl(endpoint, params);
    try {
      const res = await fetch(url, { headers: this._getHeaders(), mode: 'cors', credentials: 'include' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        if (res.status === 401) this._handleUnauthorized();
        throw Object.assign(new Error(err.detail || 'Request failed'), { status: res.status, data: err });
      }
      return res.json();
    } catch (err) {
      if (err.message && err.message.includes('Failed to fetch')) {
        throw Object.assign(new Error('Network error: Unable to reach server. Please check your connection and ensure the server is running.'), { status: 0, data: { detail: err.message } });
      }
      throw err;
    }
  }

  async post(endpoint, data = {}) {
    const url = this._buildUrl(endpoint, {});
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: this._getHeaders(),
        body: JSON.stringify(data),
        mode: 'cors',
        credentials: 'include'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        if (res.status === 401) this._handleUnauthorized();
        throw Object.assign(new Error(err.detail || 'Request failed'), { status: res.status, data: err });
      }
      return res.json();
    } catch (err) {
      if (err.message && err.message.includes('Failed to fetch')) {
        throw Object.assign(new Error('Network error: Unable to reach server. Please check your connection and ensure the server is running.'), { status: 0, data: { detail: err.message } });
      }
      throw err;
    }
  }

  async put(endpoint, data = {}) {
    const url = this._buildUrl(endpoint, {});
    try {
      const res = await fetch(url, {
        method: 'PUT',
        headers: this._getHeaders(),
        body: JSON.stringify(data),
        mode: 'cors',
        credentials: 'include'
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        if (res.status === 401) this._handleUnauthorized();
        throw Object.assign(new Error(err.detail || 'Request failed'), { status: res.status, data: err });
      }
      return res.json();
    } catch (err) {
      if (err.message && err.message.includes('Failed to fetch')) {
        throw Object.assign(new Error('Network error: Unable to reach server. Please check your connection and ensure the server is running.'), { status: 0, data: { detail: err.message } });
      }
      throw err;
    }
  }

  async patch(endpoint, data = {}) {
    const url = this._buildUrl(endpoint, {});
    const res = await fetch(url, {
      method: 'PATCH',
      headers: this._getHeaders(),
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (res.status === 401) this._handleUnauthorized();
      throw Object.assign(new Error(err.detail || 'Request failed'), { status: res.status, data: err });
    }
    return res.json();
  }

  async delete(endpoint, data = null) {
    const url = this._buildUrl(endpoint, {});
    const opts = { method: 'DELETE', headers: this._getHeaders() };
    if (data) opts.body = JSON.stringify(data);
    const res = await fetch(url, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (res.status === 401) this._handleUnauthorized();
      throw Object.assign(new Error(err.detail || 'Request failed'), { status: res.status, data: err });
    }
    // Some DELETE responses have no body
    const text = await res.text();
    if (!text) return null;
    try { return JSON.parse(text); } catch { return text; }
  }

  async request(endpoint, options = {}) {
    const { method = 'GET', body, params, ...rest } = options;
    const url = this._buildUrl(endpoint, params || {});
    const fetchOpts = {
      method,
      headers: this._getHeaders(),
    };
    if (body !== undefined) {
      fetchOpts.body = typeof body === 'string' ? body : JSON.stringify(body);
    }
    const res = await fetch(url, fetchOpts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (res.status === 401) this._handleUnauthorized();
      throw Object.assign(new Error(err.detail || 'Request failed'), { status: res.status, data: err });
    }
    const text = await res.text();
    if (!text) return null;
    try { return JSON.parse(text); } catch { return text; }
  }

  async upload(endpoint, formData) {
    const headers = {};
    if (this._token) {
      headers['Authorization'] = `Bearer ${this._token}`;
      headers['X-AUTH'] = String(this._token);
    }
    const url = this._buildUrl(endpoint, {});
    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      if (res.status === 401) this._handleUnauthorized();
      throw Object.assign(new Error(err.detail || 'Upload failed'), { status: res.status });
    }
    return res.json();
  }
}

export const apiService = new ApiService();
export default apiService;
