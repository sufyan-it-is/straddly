import React, { useState, useEffect, useCallback } from 'react';
import { Activity, Database, Server, Wifi, WifiOff, AlertCircle, RefreshCw, Play, Square, ChevronDown, ChevronUp } from 'lucide-react';
import LiveQuotes from './LiveQuotes';

const API_BASE = '/api/v2';

const authFetch = (path, opts = {}) => {
  const token = localStorage.getItem('authToken');
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { 'X-AUTH': token, Authorization: `Bearer ${token}` } : {}),
    ...(opts.headers || {}),
  };
  return fetch(`${API_BASE}${path}`, {
    ...opts,
    headers,
    credentials: 'include',
  });
};

const StatusBadge = ({ status }) => {
  const map = {
    ok: 'bg-green-500',
    healthy: 'bg-green-500',
    connected: 'bg-green-500',
    active: 'bg-green-500',
    open: 'bg-green-500',
    error: 'bg-red-500',
    disconnected: 'bg-red-500',
    closed: 'bg-red-500',
    degraded: 'bg-yellow-500',
    unknown: 'bg-gray-400',
  };
  const color = map[(status || '').toLowerCase()] || 'bg-gray-400';
  return (
    <span className={`inline-block w-2.5 h-2.5 rounded-full ${color} flex-shrink-0`} title={status} />
  );
};

const formatAge = (ts) => {
  if (!ts) return 'stale';
  const ageSec = Math.max(0, Math.floor((Date.now() - new Date(ts).getTime()) / 1000));
  if (ageSec < 5) return 'just now';
  if (ageSec < 60) return `${ageSec}s ago`;
  const mins = Math.floor(ageSec / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ago`;
};

const FreshnessBadge = ({ ts, label = 'Updated' }) => (
  <span className="rounded-full border border-zinc-600 px-2 py-0.5 text-[10px] text-zinc-300">
    {label}: {formatAge(ts)}
  </span>
);

const ConfirmModal = ({ open, title, message, onConfirm, onCancel }) => {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-xl border border-zinc-700 bg-zinc-900 p-5 shadow-2xl">
        <h3 className="text-sm font-semibold text-zinc-100">{title}</h3>
        <p className="mt-2 whitespace-pre-line text-xs text-zinc-300">{message}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onCancel} className="rounded-lg border border-zinc-600 bg-zinc-800 px-3 py-2 text-xs font-medium text-zinc-200 hover:bg-zinc-700">
            No
          </button>
          <button onClick={onConfirm} className="rounded-lg bg-red-600 px-3 py-2 text-xs font-medium text-white hover:bg-red-500">
            Yes
          </button>
        </div>
      </div>
    </div>
  );
};

const StatusCard = ({ title, status, detail, icon: Icon, refreshedAt }) => (
  <div className="rounded-xl p-3 flex flex-col gap-1 sa-card border">
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2 text-xs tn-muted">
        {Icon && <Icon size={13} />}
        <span>{title}</span>
      </div>
      <div className="flex items-center gap-2">
        <FreshnessBadge ts={refreshedAt} />
        <StatusBadge status={status} />
      </div>
    </div>
    <div className="text-sm font-semibold capitalize">{status || '—'}</div>
    {detail && <div className="text-xs truncate tn-muted">{detail}</div>}
  </div>
);

const SeverityPill = ({ level }) => {
  const lvl = (level || "info").toLowerCase();
  const style = {
    error:    "sa-severity-error",
    critical: "sa-severity-error",
    warning:  "sa-severity-warning",
    warn:     "sa-severity-warning",
    info:     "sa-severity-info",
  }[lvl] || "sa-severity-info";
  return (
    <span className={`text-[11px] font-semibold px-2 py-1 rounded-md uppercase tracking-wide ${style}`}>
      {lvl.toUpperCase()}
    </span>
  );
};

const formatWhen = (ts) => {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString();
  } catch (err) {
    return ts;
  }
};

const toNumber = (...candidates) => {
  for (const value of candidates) {
    if (value === null || value === undefined || value === '') continue;
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string') {
      const cleaned = value.replace(/[^0-9.+-]/g, '');
      const parsed = Number.parseFloat(cleaned);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return 0;
};

const Sparkline = ({ title, values, colorClass, suffix = '', refreshedAt }) => {
  const safeValues = (values || []).slice(-40);
  const width = 220;
  const height = 60;
  const min = 0;
  const max = Math.max(1, ...safeValues, min);

  const points = safeValues
    .map((v, i) => {
      const x = (i / Math.max(1, safeValues.length - 1)) * width;
      const y = height - ((Math.max(min, v) - min) / (max - min || 1)) * height;
      return `${x},${y}`;
    })
    .join(' ');

  const latest = safeValues.length ? safeValues[safeValues.length - 1] : 0;

  return (
    <div className="rounded-xl p-2.5 sa-card border">
      <div className="flex items-center justify-between mb-1">
        <div className="text-xs tn-muted">{title}</div>
        <div className="flex items-center gap-2">
          <FreshnessBadge ts={refreshedAt} />
          <div className="text-xs font-semibold">{latest.toFixed(1)}{suffix}</div>
        </div>
      </div>
      {safeValues.length > 1 ? (
        <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-16">
          <polyline
            fill="none"
            strokeWidth="2"
            points={points}
            className={colorClass}
          />
        </svg>
      ) : (
        <div className="h-16 flex items-center text-xs text-zinc-500">No samples yet</div>
      )}
    </div>
  );
};

const SystemMonitoring = () => {
  const [health, setHealth] = useState(null);
  const [streamStatus, setStreamStatus] = useState(null);
  const [dhanRuntimeStatus, setDhanRuntimeStatus] = useState(null);
  const [etfStatus, setEtfStatus] = useState(null);
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reconnecting, setReconnecting] = useState(false);
  const [rollingOver, setRollingOver] = useState(false);
  const [rolloverResult, setRolloverResult] = useState(null);
  const [lastRefreshed, setLastRefreshed] = useState(null);
  const [monitorStatus, setMonitorStatus] = useState(null);
  const [monitorSamples, setMonitorSamples] = useState([]);
  const [monitorBusy, setMonitorBusy] = useState(false);
  const [monitorError, setMonitorError] = useState('');
  const [sourceFreshness, setSourceFreshness] = useState({
    health: null,
    stream: null,
    dhanRuntime: null,
    etf: null,
    notifications: null,
    monitor: null,
  });
  const [confirmDialog, setConfirmDialog] = useState({
    open: false,
    title: '',
    message: '',
    onConfirm: null,
  });
  const [showResourceCharts, setShowResourceCharts] = useState(false);

  const askConfirm = (title, message, onConfirm) => {
    setConfirmDialog({ open: true, title, message, onConfirm });
  };

  const closeConfirm = () => {
    setConfirmDialog({ open: false, title: '', message: '', onConfirm: null });
  };

  const runConfirmedAction = async () => {
    const fn = confirmDialog.onConfirm;
    closeConfirm();
    if (typeof fn === 'function') {
      await fn();
    }
  };

  const fetchMonitor = useCallback(async () => {
    try {
      const [statusRes, samplesRes] = await Promise.all([
        authFetch('/admin/vps-monitor/status'),
        authFetch('/admin/vps-monitor/samples?limit=120'),
      ]);

      if (statusRes.ok) {
        setMonitorStatus(await statusRes.json());
        setMonitorError('');
        setSourceFreshness((prev) => ({ ...prev, monitor: new Date().toISOString() }));
      } else {
        setMonitorStatus(null);
        setMonitorError(`Monitor status failed (HTTP ${statusRes.status})`);
      }

      if (samplesRes.ok) {
        const data = await samplesRes.json();
        setMonitorSamples(Array.isArray(data?.samples) ? data.samples : []);
        setSourceFreshness((prev) => ({ ...prev, monitor: new Date().toISOString() }));
      } else {
        setMonitorSamples([]);
        setMonitorError(`Monitor samples failed (HTTP ${samplesRes.status})`);
      }
    } catch (err) {
      console.error('VPS monitor fetch error:', err);
      setMonitorError(err?.message || 'Failed to fetch monitor data');
    }
  }, []);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [healthRes, streamRes, dhanRuntimeRes, etfRes, notifRes, monitorStatusRes, monitorSamplesRes] = await Promise.allSettled([
        fetch(`${API_BASE}/health`),
        authFetch('/market/stream-status'),
        authFetch('/admin/dhan/status'),
        authFetch('/market/etf-tierb-status'),
        authFetch('/admin/notifications?limit=100'),
        authFetch('/admin/vps-monitor/status'),
        authFetch('/admin/vps-monitor/samples?limit=120'),
      ]);

      if (healthRes.status === 'fulfilled' && healthRes.value.ok) {
        setHealth(await healthRes.value.json());
        setSourceFreshness((prev) => ({ ...prev, health: new Date().toISOString() }));
      } else {
        setHealth(null);
      }

      if (streamRes.status === 'fulfilled' && streamRes.value.ok) {
        setStreamStatus(await streamRes.value.json());
        setSourceFreshness((prev) => ({ ...prev, stream: new Date().toISOString() }));
      } else {
        setStreamStatus(null);
      }

      if (dhanRuntimeRes.status === 'fulfilled' && dhanRuntimeRes.value.ok) {
        setDhanRuntimeStatus(await dhanRuntimeRes.value.json());
        setSourceFreshness((prev) => ({ ...prev, dhanRuntime: new Date().toISOString() }));
      } else {
        setDhanRuntimeStatus(null);
      }

      if (etfRes.status === 'fulfilled' && etfRes.value.ok) {
        setEtfStatus(await etfRes.value.json());
        setSourceFreshness((prev) => ({ ...prev, etf: new Date().toISOString() }));
      } else {
        setEtfStatus(null);
      }

      if (notifRes.status === 'fulfilled' && notifRes.value.ok) {
        setNotifications(await notifRes.value.json());
        setSourceFreshness((prev) => ({ ...prev, notifications: new Date().toISOString() }));
      } else {
        setNotifications([]);
      }

      if (monitorStatusRes.status === 'fulfilled' && monitorStatusRes.value.ok) {
        setMonitorStatus(await monitorStatusRes.value.json());
        setSourceFreshness((prev) => ({ ...prev, monitor: new Date().toISOString() }));
      } else {
        setMonitorStatus(null);
        if (monitorStatusRes.status === 'fulfilled') {
          setMonitorError(`Monitor status failed (HTTP ${monitorStatusRes.value.status})`);
        }
      }

      if (monitorSamplesRes.status === 'fulfilled' && monitorSamplesRes.value.ok) {
        const data = await monitorSamplesRes.value.json();
        setMonitorSamples(Array.isArray(data?.samples) ? data.samples : []);
        setSourceFreshness((prev) => ({ ...prev, monitor: new Date().toISOString() }));
      } else {
        setMonitorSamples([]);
        if (monitorSamplesRes.status === 'fulfilled') {
          setMonitorError(`Monitor samples failed (HTTP ${monitorSamplesRes.value.status})`);
        }
      }

      setLastRefreshed(new Date());
    } catch (err) {
      console.error('SystemMonitoring fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 30000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  useEffect(() => {
    if (!monitorStatus?.running) return undefined;
    const interval = setInterval(fetchMonitor, 5000);
    return () => clearInterval(interval);
  }, [monitorStatus?.running, fetchMonitor]);

  const handleReconnect = async () => {
    setReconnecting(true);
    try {
      const res = await authFetch('/market/stream-reconnect', { method: 'POST' });
      if (res.ok) {
        setTimeout(fetchAll, 2000);
      }
    } catch (err) {
      console.error('Reconnect error:', err);
    } finally {
      setReconnecting(false);
    }
  };

  const handleRollover = async () => {
    setRollingOver(true);
    setRolloverResult(null);
    try {
      const res = await authFetch('/admin/subscriptions/rollover', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        setRolloverResult(data);
        setTimeout(fetchAll, 1000);
      } else {
        setRolloverResult({ error: `HTTP ${res.status}` });
      }
    } catch (err) {
      setRolloverResult({ error: err.message });
    } finally {
      setRollingOver(false);
    }
  };

  const handleStartMonitor = async () => {
    setMonitorBusy(true);
    try {
      const res = await authFetch('/admin/vps-monitor/start', {
        method: 'POST',
        body: JSON.stringify({ interval_seconds: 5 }),
      });
      if (res.ok) {
        setMonitorError('');
        await fetchMonitor();
      } else {
        setMonitorError(`Monitor start failed (HTTP ${res.status})`);
      }
    } catch (err) {
      console.error('Start monitor error:', err);
      setMonitorError(err?.message || 'Failed to start monitor');
    } finally {
      setMonitorBusy(false);
    }
  };

  const handleStopMonitor = async () => {
    setMonitorBusy(true);
    try {
      const res = await authFetch('/admin/vps-monitor/stop', { method: 'POST' });
      if (res.ok) {
        setMonitorError('');
        await fetchMonitor();
      } else {
        setMonitorError(`Monitor stop failed (HTTP ${res.status})`);
      }
    } catch (err) {
      console.error('Stop monitor error:', err);
      setMonitorError(err?.message || 'Failed to stop monitor');
    } finally {
      setMonitorBusy(false);
    }
  };

  const dbStatus = health?.database || health?.db || 'unknown';
  const apiStatus = health?.status || 'unknown';
  const healthDhanStatus = (health?.dhan_api || health?.dhan || 'unknown').toLowerCase();
  const dhanStatus = healthDhanStatus === 'disabled'
    ? 'disabled'
    : dhanRuntimeStatus?.connected
      ? 'connected'
      : dhanRuntimeStatus?.tick_processor
        ? 'degraded'
        : dhanRuntimeStatus?.has_credentials
          ? 'disconnected'
          : 'unknown';
  const dhanDetail = healthDhanStatus === 'disabled'
    ? 'Dhan integration is disabled by environment configuration.'
    : dhanRuntimeStatus?.connected
      ? `${dhanRuntimeStatus.connected_slots ?? 0}/${dhanRuntimeStatus?.slots?.length ?? 0} WS slots active`
      : dhanRuntimeStatus?.tick_processor
        ? 'Services started, waiting for websocket connection'
        : dhanRuntimeStatus?.has_credentials
          ? 'Credentials saved, no active websocket slots'
          : 'No runtime credentials available';
  const equityWsStatus = streamStatus?.equity?.status || streamStatus?.nse_ws || 'unknown';
  const mcxWsStatus = streamStatus?.mcx?.status || streamStatus?.mcx_ws || 'unknown';
  const etfTierbStatus = etfStatus?.status || 'unknown';
  const marketSession = streamStatus?.market_session || health?.market_session || 'unknown';
  const latestSample = monitorSamples.length ? monitorSamples[monitorSamples.length - 1] : null;

  const cpuSeries = monitorSamples.map((s) =>
    toNumber(s.system_cpu_percent, s.cpu_percent, s.system_cpu_usage, s.cpu_usage)
  );
  const appCpuSeries = monitorSamples.map((s) =>
    toNumber(s.app_cpu_percent, s.process_cpu_percent, s.app_cpu_usage)
  );
  const memSeries = monitorSamples.map((s) =>
    toNumber(s.memory_used_percent, s.memory_percent, s.mem_percent, s.memory_usage)
  );
  const loadSeries = monitorSamples.map((s) => toNumber(s.load_1m, s.load1, s.load));

  const statusCards = [
    { title: 'Database', status: dbStatus, icon: Database, refreshedAt: sourceFreshness.health },
    { title: 'API Server', status: apiStatus, detail: health?.version, icon: Server, refreshedAt: sourceFreshness.health },
    { title: 'Dhan Runtime', status: dhanStatus, detail: dhanDetail, icon: Activity, refreshedAt: sourceFreshness.dhanRuntime || sourceFreshness.health },
    { title: 'Equity WS', status: equityWsStatus, detail: streamStatus?.equity?.subscriptions ? `${streamStatus.equity.subscriptions} subs` : undefined, icon: Wifi, refreshedAt: sourceFreshness.stream },
    { title: 'MCX WS', status: mcxWsStatus, detail: streamStatus?.mcx?.recent_ticks != null ? `${streamStatus.mcx.recent_ticks} ticks/3m` : undefined, icon: Wifi, refreshedAt: sourceFreshness.stream },
    { title: 'ETF Tier-B', status: etfTierbStatus, icon: Activity, refreshedAt: sourceFreshness.etf },
    { title: 'Market Session', status: marketSession, icon: Activity, refreshedAt: sourceFreshness.stream },
  ];

  return (
    <div className="space-y-4">
      {/* Live Quotes */}
      <LiveQuotes />

      {/* Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold flex items-center gap-2">
          <Activity size={15} />
          System Status
        </h3>
        <div className="flex items-center gap-2">
          {lastRefreshed && (
            <span className="text-xs tn-muted">
              Updated {lastRefreshed.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={fetchAll}
            disabled={loading}
            className="p-1.5 rounded transition-colors disabled:opacity-50 sa-nested border"
            title="Refresh"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-7 gap-2.5">
        {statusCards.map((card) => (
          <StatusCard key={card.title} {...card} />
        ))}
      </div>

      {/* Admin Actions */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={() => askConfirm('Confirm Purge', 'Are you sure to purge expired subscriptions now?', handleRollover)}
          disabled={rollingOver}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium border transition-colors disabled:opacity-50 sa-input hover:opacity-80"
          title="Unsubscribe expired contracts from Dhan WS and clean up the active subscription map"
        >
          <RefreshCw size={11} className={rollingOver ? 'animate-spin' : ''} />
          {rollingOver ? 'Purging...' : 'Purge Expired Subs'}
        </button>
        {rolloverResult && !rolloverResult.error && (
          <span className="text-xs text-green-400">
            ✓ {rolloverResult.evicted} evicted &nbsp;·&nbsp; {rolloverResult.tokens_before} → {rolloverResult.tokens_after} subs
          </span>
        )}
        {rolloverResult?.error && (
          <span className="text-xs text-red-400">✗ {rolloverResult.error}</span>
        )}
      </div>

      {/* Reconnect */}
      {(equityWsStatus === 'disconnected' || mcxWsStatus === 'disconnected') && (
        <div className="flex items-center gap-3 bg-yellow-900/30 border border-yellow-700/50 rounded-lg p-3">
          <WifiOff size={16} className="text-yellow-400 flex-shrink-0" />
          <span className="text-xs text-yellow-300 flex-1">WebSocket disconnected. Streams may be stale.</span>
          <button
            onClick={() => askConfirm('Confirm Reconnect', 'Are you sure to reconnect websocket streams now?', handleReconnect)}
            disabled={reconnecting}
            className="flex items-center gap-1 px-3 py-1.5 bg-yellow-500 text-black rounded text-xs font-medium hover:bg-yellow-400 transition-colors disabled:opacity-50"
          >
            <RefreshCw size={11} className={reconnecting ? 'animate-spin' : ''} />
            {reconnecting ? 'Reconnecting...' : 'Reconnect'}
          </button>
        </div>
      )}

      {/* Monitoring + Alerts */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
      <div className="rounded-xl p-3 border sa-card">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
          <div>
            <h3 className="text-sm font-semibold flex items-center gap-2">
              <Activity size={14} />
              VPS Live Monitor
            </h3>
            <div className="text-[11px] text-zinc-400">Default ON at startup. Stop only when intentional maintenance is needed.</div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`text-xs ${monitorStatus?.running ? 'text-green-400' : 'text-zinc-400'}`}>
              {monitorStatus?.running ? 'Running' : 'Stopped'}
            </span>
            <button
              onClick={() => askConfirm('Confirm Start Monitor', 'Are you sure to start VPS monitor sampling?', handleStartMonitor)}
              disabled={monitorBusy || monitorStatus?.running}
              className="flex items-center gap-1 px-3 py-1.5 rounded text-xs font-medium border border-green-700 bg-green-600/20 hover:bg-green-600/30 transition-colors disabled:opacity-50"
            >
              <Play size={11} /> Start
            </button>
            <button
              onClick={() => askConfirm('Confirm Stop Monitor', 'Are you sure to stop VPS monitor sampling?', handleStopMonitor)}
              disabled={monitorBusy || !monitorStatus?.running}
              className="flex items-center gap-1 px-3 py-1.5 rounded text-xs font-medium border border-red-700 bg-red-600/20 hover:bg-red-600/30 transition-colors disabled:opacity-50"
            >
              <Square size={11} /> Stop
            </button>
          </div>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-2.5 mb-3">
          <div className="rounded-lg p-3 sa-nested border">
            <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400"><span>CPU</span><FreshnessBadge ts={sourceFreshness.monitor} /></div>
            <div className="text-sm font-semibold">{toNumber(latestSample?.system_cpu_percent, latestSample?.cpu_percent, latestSample?.system_cpu_usage, latestSample?.cpu_usage).toFixed(1)}%</div>
          </div>
          <div className="rounded-lg p-3 sa-nested border">
            <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400"><span>App CPU</span><FreshnessBadge ts={sourceFreshness.monitor} /></div>
            <div className="text-sm font-semibold">{toNumber(latestSample?.app_cpu_percent, latestSample?.process_cpu_percent, latestSample?.app_cpu_usage).toFixed(1)}%</div>
          </div>
          <div className="rounded-lg p-3 sa-nested border">
            <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400"><span>Memory</span><FreshnessBadge ts={sourceFreshness.monitor} /></div>
            <div className="text-sm font-semibold">{toNumber(latestSample?.memory_used_percent, latestSample?.memory_percent, latestSample?.mem_percent, latestSample?.memory_usage).toFixed(1)}%</div>
          </div>
          <div className="rounded-lg p-3 sa-nested border">
            <div className="mb-1 flex items-center justify-between text-[11px] text-zinc-400"><span>Load (1m)</span><FreshnessBadge ts={sourceFreshness.monitor} /></div>
            <div className="text-sm font-semibold">{toNumber(latestSample?.load_1m, latestSample?.load1, latestSample?.load).toFixed(2)}</div>
          </div>
        </div>

        {monitorError && (
          <div className="text-xs text-red-400 mb-2">{monitorError}</div>
        )}

        <button
          onClick={() => setShowResourceCharts((v) => !v)}
          className="w-full flex items-center justify-between rounded-lg px-3 py-2 border sa-nested text-xs font-medium"
          title="Toggle VPS resource charts"
        >
          <span>VPS Resource Charts ({monitorSamples.length} samples)</span>
          {showResourceCharts ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>

        {showResourceCharts && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5 mt-2.5">
            <Sparkline title="System CPU" values={cpuSeries} colorClass="stroke-red-400" suffix="%" refreshedAt={sourceFreshness.monitor} />
            <Sparkline title="App CPU" values={appCpuSeries} colorClass="stroke-orange-400" suffix="%" refreshedAt={sourceFreshness.monitor} />
            <Sparkline title="Memory Used" values={memSeries} colorClass="stroke-blue-400" suffix="%" refreshedAt={sourceFreshness.monitor} />
            <Sparkline title="Load 1m" values={loadSeries} colorClass="stroke-emerald-400" refreshedAt={sourceFreshness.monitor} />
          </div>
        )}
      </div>

      <div className="rounded-xl p-3 border sa-card">
        <div className="flex items-center justify-between gap-2 mb-2">
          <h3 className="text-sm font-semibold flex items-center gap-2">
            <AlertCircle size={14} />
            Admin Alerts
          </h3>
          <span className="text-[11px] text-zinc-400">
            {notifications.length ? `Last ${notifications.length}` : "No recent alerts"}
          </span>
        </div>

        <div className="mb-2">
          <FreshnessBadge ts={sourceFreshness.notifications} />
        </div>

        <div className="space-y-2 max-h-[420px] overflow-y-auto">
          {notifications.length === 0 && (
            <div className="text-xs text-zinc-500">No alerts yet. Background tasks look clean.</div>
          )}

          {notifications.map((notif, idx) => (
            <div
              key={notif.id || idx}
              className="flex items-start gap-3 rounded-lg p-3 text-xs sa-nested border"
            >
              <SeverityPill level={notif.severity || notif.level} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between gap-3">
                  <div className="font-semibold text-zinc-100 truncate">{notif.title || notif.message}</div>
                  {notif.created_at && (
                    <span className="text-[11px] text-zinc-500 whitespace-nowrap">{formatWhen(notif.created_at)}</span>
                  )}
                </div>
                {notif.title && notif.message && (
                  <div className="text-[12px] text-zinc-200 mt-1 leading-relaxed">
                    {notif.message}
                  </div>
                )}
                {notif.category && (
                  <div className="text-[10px] text-zinc-500 mt-1 uppercase tracking-wide">{notif.category}</div>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
      </div>

      <ConfirmModal
        open={confirmDialog.open}
        title={confirmDialog.title}
        message={confirmDialog.message}
        onCancel={closeConfirm}
        onConfirm={runConfirmedAction}
      />
    </div>
  );
};

export default SystemMonitoring;
