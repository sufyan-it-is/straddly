import React, { useState, useEffect, useCallback, useRef } from "react";
import { apiService } from '../services/apiService';
import { useAuth } from '../contexts/AuthContext';
import { useMarketPulse } from '../hooks/useMarketPulse';
import { useWebSocket } from '../hooks/useWebSocket';
import { DndContext, PointerSensor, KeyboardSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core';
import { SortableContext, useSortable, verticalListSortingStrategy, arrayMove, sortableKeyboardCoordinates } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { RefreshCw, X, Plus, Search, ChevronDown, Check, GripVertical } from "lucide-react";
import { formatOptionLabel } from '../utils/formatInstrumentLabel';

// ─── helpers ───────────────────────────────────────────────────────────────────
const WATCHLIST_STORAGE_KEY_PREFIX = "watchlists:";
const DEFAULT_TABS = [
  { id: 1, name: "Watchlist 1", instruments: [] },
  { id: 2, name: "Watchlist 2", instruments: [] },
  { id: 3, name: "Watchlist 3", instruments: [] },
];

const loadFromStorage = (userId) => {
  try {
    const raw = localStorage.getItem(WATCHLIST_STORAGE_KEY_PREFIX + userId);
    if (raw) return JSON.parse(raw);
  } catch {}
  return null;
};

const saveToStorage = (userId, tabs) => {
  try {
    localStorage.setItem(WATCHLIST_STORAGE_KEY_PREFIX + userId, JSON.stringify(tabs));
  } catch {}
};

const extractWatchlistItems = (response) => {
  const direct = response?.data;
  if (Array.isArray(direct)) return direct;
  if (Array.isArray(direct?.data)) return direct.data;
  if (Array.isArray(response)) return response;
  return [];
};

const toTokenKey = (value) => {
  const n = Number(value);
  if (Number.isFinite(n) && n > 0) return `n:${n}`;
  const raw = String(value ?? '').trim();
  return raw ? `s:${raw}` : '';
};

const getInstrumentDndId = (inst) => String(inst?.token ?? inst?.id ?? inst?.symbol ?? '').trim();

const SortableInstrumentRow = ({ id, children }) => {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.9 : 1,
  };

  return children({ setNodeRef, attributes, listeners, style, isDragging });
};

// ─── WatchlistPage ──────────────────────────────────────────────────────────────
const WatchlistPage = ({ onOpenOrderModal, onOpenChart, compact = false }) => {
  const { user } = useAuth();
  const { pulse } = useMarketPulse();

  const [tabs, setTabs] = useState(DEFAULT_TABS);
  const [activeTabId, setActiveTabId] = useState(1);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState([]);
  const [isSearching, setIsSearching] = useState(false);
  const searchTimeout = useRef(null);
  const searchSeq = useRef(0);
  const hydrateSeq = useRef(0);
  const [openDepthToken, setOpenDepthToken] = useState(null);
  const [tickByToken, setTickByToken] = useState({});
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const searchBoxRef = useRef(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const wsFeedUrl = (() => {
    try {
      const base = apiService.baseURL;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.host;
      if (base.startsWith('/')) return `${protocol}//${host}${base}/ws/feed`;
      return base.replace(/^https?:/, protocol === 'wss:' ? 'wss:' : 'ws:') + '/ws/feed';
    } catch {
      return null;
    }
  })();

  const { lastMessage: feedMsg, sendMessage: sendFeed, readyState: feedState } = useWebSocket(wsFeedUrl);

  const normaliseDepth = (depth) => {
    if (!Array.isArray(depth)) return [];
    return depth
      .map((lvl) => {
        if (!lvl || typeof lvl !== 'object') return null;
        const price = Number(lvl.price);
        if (!Number.isFinite(price)) return null;
        const qty = Number(lvl.qty);
        return {
          price,
          qty: Number.isFinite(qty) ? Math.max(0, Math.trunc(qty)) : 0,
        };
      })
      .filter(Boolean)
      .slice(0, 5);
  };

  const getDepthLadder = (inst, tick) => {
    const bidDepth = normaliseDepth(tick?.bid_depth?.length ? tick.bid_depth : inst?.bidDepth);
    const askDepth = normaliseDepth(tick?.ask_depth?.length ? tick.ask_depth : inst?.askDepth);
    const rows = [];
    for (let i = 0; i < 5; i += 1) {
      rows.push({
        level: i + 1,
        bid: bidDepth[i] || null,
        ask: askDepth[i] || null,
      });
    }
    return rows;
  };

  const mapServerItems = (serverItems) => (serverItems || []).map(item => ({
    id: item.id || item.token,
    symbol: item.symbol,
    exchange: item.exchange,
    token: item.token,
    instrumentType: item.instrument_type || item.instrumentType,
    ltp: item.ltp ?? null,
    close: item.close ?? null,
    underlying: item.underlying || '',
    expiryDate: item.expiry_date ?? null,
    strikePrice: item.strike_price ?? null,
    optionType: item.option_type ?? null,
    lot_size: item.lot_size ?? item.lotSize ?? 1,
    lotSize: item.lot_size ?? item.lotSize ?? 1,
    change_pct: item.change_pct ?? null,
    bidDepth: Array.isArray(item.bid_depth) ? item.bid_depth : [],
    askDepth: Array.isArray(item.ask_depth) ? item.ask_depth : [],
    bestBid: item.best_bid ?? item.bid ?? item.bid_price ?? null,
    bestAsk: item.best_ask ?? item.ask ?? item.ask_price ?? null,
    tier: item.tier || 'B',  // 'A' = on-demand, 'B' = always subscribed
    hasPosition: item.has_position ?? false,  // whether in open positions
    addedAt: item.added_at ?? null,  // timestamp when added to watchlist
  }));

  const hydrateFromServer = useCallback(async () => {
    if (!user?.id) return { instruments: [], tokens: new Set() };
    // Grab a sequence number BEFORE the async fetch so we can detect stale responses.
    const seq = ++hydrateSeq.current;
    const res = await apiService.get(`/watchlist/${user.id}`);
    // If a newer hydrate call already started (and possibly finished), discard this response
    // to prevent an older, stale server snapshot from overwriting fresher state.
    if (seq !== hydrateSeq.current) return { instruments: [], tokens: new Set() };
    const serverItems = extractWatchlistItems(res);
    const instruments = mapServerItems(serverItems);
    const tokens = new Set(instruments.map(i => String(i.token)));
    const canonicalByToken = new Map(instruments.map(i => [String(i.token), i]));

    setTabs(prev => {
      const base = Array.isArray(prev) && prev.length ? prev : DEFAULT_TABS;
      return base.map(t => {
        const nextInstruments = (t.id === 1 ? instruments : (t.instruments || [])).map(it => {
          const c = canonicalByToken.get(String(it.token));
          return c ? { ...it, ...c } : it;
        });
        return { ...t, instruments: nextInstruments };
      });
    });
    return { instruments, tokens };
  }, [user?.id]);

  // Load from server + localStorage
  useEffect(() => {
    if (!user?.id) return;
    const savedTabs = loadFromStorage(user.id);
    if (savedTabs && savedTabs.length > 0) {
      setTabs(savedTabs);
    }

    (async () => {
      try {
        const { tokens: serverTokens } = await hydrateFromServer();

        // Best-effort: if earlier UI versions stored watchlists only in localStorage,
        // sync those instruments into the server watchlist so prices can hydrate.
        const localTab1 = (savedTabs || []).find(t => t?.id === 1)?.instruments || [];
        const toSync = localTab1
          .map(i => ({
            token: i?.token,
            symbol: i?.symbol,
            exchange: i?.exchange,
            instrumentType: i?.instrumentType || i?.instrument_type,
          }))
          .filter(i => {
            const tok = String(i.token || '').trim();
            if (!tok || !/^\d+$/.test(tok)) return false;
            return !serverTokens.has(tok);
          })
          .slice(0, 50);

        if (toSync.length) {
          await Promise.allSettled(toSync.map(i => apiService.post('/watchlist/add', {
            user_id: String(user?.id || ''),
            token: String(i.token),
            symbol: i.symbol,
            exchange: i.exchange,
          })));
          await hydrateFromServer();
        }
      } catch {}
    })();
  }, [user?.id, hydrateFromServer]);

  useEffect(() => {
    const handler = () => { hydrateFromServer(); };
    window.addEventListener('tn-watchlist-refresh', handler);
    return () => window.removeEventListener('tn-watchlist-refresh', handler);
  }, [hydrateFromServer]);

  // Fallback live refresh: keep watchlist depth/ltp moving even if WS reconnects are flaky.
  useEffect(() => {
    if (!user?.id) return;
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        hydrateFromServer();
      }
    }, 3000);
    return () => clearInterval(timer);
  }, [user?.id, hydrateFromServer]);

  // Persist on change
  useEffect(() => {
    if (user?.id) saveToStorage(user.id, tabs);
  }, [tabs, user]);

  const activeTab = tabs.find(t => t.id === activeTabId) || tabs[0];
  const watchlistTokenKeys = new Set(
    (tabs || [])
      .flatMap((tab) => tab?.instruments || [])
      .map((inst) => toTokenKey(inst?.token))
      .filter(Boolean)
  );
  const activeTabTokenList = (activeTab?.instruments || [])
    .map(i => Number(i.token))
    .filter(n => Number.isFinite(n) && n > 0);
  const activeTabTokenKey = activeTabTokenList.join(',');

  // Close dropdown on outside click or Escape.
  useEffect(() => {
    const onDown = (e) => {
      if (e.key === 'Escape') {
        setDropdownOpen(false);
        setSearchQuery('');
        setSearchResults([]);
      }
    };
    const onClick = (e) => {
      const el = searchBoxRef.current;
      if (!el) return;
      if (!el.contains(e.target)) {
        setDropdownOpen(false);
        setSearchQuery('');
        setSearchResults([]);
      }
    };
    document.addEventListener('keydown', onDown);
    document.addEventListener('mousedown', onClick);
    return () => {
      document.removeEventListener('keydown', onDown);
      document.removeEventListener('mousedown', onClick);
    };
  }, []);

  // Subscribe WS feed to active watchlist tokens (for bid/ask snapshot when market is open).
  useEffect(() => {
    if (feedState !== WebSocket.OPEN) return;
    const tokens = activeTabTokenList;
    if (tokens.length === 0) return;
    sendFeed({ action: 'subscribe', tokens });
    return () => {
      try { sendFeed({ action: 'unsubscribe', tokens }); } catch {}
    };
  }, [feedState, activeTabId, activeTabTokenKey]);

  useEffect(() => {
    if (!feedMsg) return;
    const msg = feedMsg;
    if (msg?.type === 'snapshot' && Array.isArray(msg.data)) {
      setTickByToken(prev => {
        const next = { ...prev };
        msg.data.forEach(t => {
          if (t?.instrument_token) next[String(t.instrument_token)] = t;
        });
        return next;
      });
    }
    if (msg?.type === 'tick' && msg?.data?.instrument_token) {
      setTickByToken(prev => ({ ...prev, [String(msg.data.instrument_token)]: msg.data }));
    }
  }, [feedMsg]);

  // ── search ──
  const handleSearchInput = (e) => {
    const q = e.target.value;
    setSearchQuery(q);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (!q.trim() || q.length < 2) { setSearchResults([]); return; }
    searchTimeout.current = setTimeout(() => runSearch(q.trim()), 350);
  };

  const runSearch = async (q) => {
    const seq = ++searchSeq.current;
    setIsSearching(true);
    try {
      const normalize = (s) => String(s || '').toUpperCase().replace(/[^A-Z0-9]+/g, ' ').trim();
      const canonicalizeToken = (t) => {
        if (t === 'PUT') return 'PE';
        if (t === 'CALL') return 'CE';
        return t;
      };
      const monthMap = {
        JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
        JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
      };
      const monthTokens = new Set(Object.keys(monthMap));
      const qTokens = normalize(q).split(/\s+/).filter(Boolean).map(canonicalizeToken);
      const hasOptionToken = qTokens.includes('PE') || qTokens.includes('CE');
      const hasFutureToken = qTokens.includes('FUT') || qTokens.includes('FUTURE');
      const hasStrikeToken = qTokens.some(t => /^\d{3,}$/.test(t));
      const hasOptionIntent = hasOptionToken || (hasStrikeToken && !hasFutureToken);
      const hasFutureIntent = hasFutureToken;
      const broadQuery = qTokens
        .filter(t => t !== 'CE' && t !== 'PE' && t !== 'FUT' && t !== 'FUTURE')
        .join(' ')
        .trim() || q;

      const qFirstAlpha = qTokens.find(t =>
        /^[A-Z]+$/.test(t) &&
        t !== 'CE' &&
        t !== 'PE' &&
        t !== 'FUT' &&
        t !== 'FUTURE' &&
        !monthTokens.has(t)
      ) || '';

      const parseExpiryFromQuery = () => {
        if (!hasOptionIntent) return null;
        for (let i = 0; i < qTokens.length - 1; i += 1) {
          const a = qTokens[i];
          const b = qTokens[i + 1];

          if (/^\d{1,2}$/.test(a) && monthTokens.has(b)) {
            const day = Number(a);
            const month = monthMap[b];
            if (!Number.isFinite(day) || day < 1 || day > 31) continue;
            const now = new Date();
            let year = now.getFullYear();
            let d = new Date(year, month, day);
            d.setHours(0, 0, 0, 0);
            const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            if (d < today) d = new Date(year + 1, month, day);
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            return `${y}-${m}-${dd}`;
          }

          if (monthTokens.has(a) && /^\d{1,2}$/.test(b)) {
            const day = Number(b);
            const month = monthMap[a];
            if (!Number.isFinite(day) || day < 1 || day > 31) continue;
            const now = new Date();
            let year = now.getFullYear();
            let d = new Date(year, month, day);
            d.setHours(0, 0, 0, 0);
            const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            if (d < today) d = new Date(year + 1, month, day);
            const y = d.getFullYear();
            const m = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            return `${y}-${m}-${dd}`;
          }
        }
        return null;
      };

      const levenshtein = (a, b) => {
        const s = String(a || '');
        const t = String(b || '');
        if (s === t) return 0;
        if (!s.length) return t.length;
        if (!t.length) return s.length;
        const dp = Array.from({ length: s.length + 1 }, (_, i) => [i]);
        for (let j = 1; j <= t.length; j += 1) dp[0][j] = j;
        for (let i = 1; i <= s.length; i += 1) {
          for (let j = 1; j <= t.length; j += 1) {
            const cost = s[i - 1] === t[j - 1] ? 0 : 1;
            dp[i][j] = Math.min(
              dp[i - 1][j] + 1,
              dp[i][j - 1] + 1,
              dp[i - 1][j - 1] + cost,
            );
          }
        }
        return dp[s.length][t.length];
      };

      const parseUnderlyingFromQuery = () => {
        if (!hasOptionIntent || !qFirstAlpha) return null;
        const knownIndexUnderlyings = ['NIFTY', 'BANKNIFTY', 'FINNIFTY', 'MIDCPNIFTY', 'SENSEX', 'BANKEX', 'NIFTYNXT50'];
        if (knownIndexUnderlyings.includes(qFirstAlpha)) return qFirstAlpha;

        let best = null;
        let bestDist = Number.POSITIVE_INFINITY;
        knownIndexUnderlyings.forEach((u) => {
          const d = levenshtein(qFirstAlpha, u);
          if (d < bestDist) {
            bestDist = d;
            best = u;
          }
        });
        if (best && bestDist <= 2) return best;
        return null;
      };

      const parsedExpiry = parseExpiryFromQuery();
      const parsedUnderlying = parseUnderlyingFromQuery();
      const optionParams = { q };
      if (parsedUnderlying) optionParams.underlying = parsedUnderlying;
      if (parsedExpiry) optionParams.expiry = parsedExpiry;

      const [tierA, tierB, futures, optStrikes, equity] = await Promise.allSettled([
        apiService.get('/subscriptions/search', { tier: 'TIER_A', q: broadQuery }),
        apiService.get('/subscriptions/search', { tier: 'TIER_B', q: broadQuery }),
        apiService.get('/instruments/futures/search', { q: broadQuery }),
        apiService.get('/options/strikes/search', optionParams),
        apiService.get('/instruments/search', { q: broadQuery }),
      ]);
      const results = [];
      const seen = new Set();
      const addResults = (res, source) => {
        if (res.status !== 'fulfilled') return;
        const list = res.value?.data || res.value || [];
        if (!Array.isArray(list)) return;
        list.forEach(item => {
          const key = item.token || item.security_id || item.symbol;
          if (!key || seen.has(key)) return;
          seen.add(key);
          results.push({
            id: key,
            symbol: item.symbol || item.tradingsymbol || key,
            exchange: item.exchange_segment || item.exchange || 'NSE',
            token: item.token || item.security_id || key,
            instrumentType: item.instrument_type || item.instrumentType || 'EQ',
            underlying: item.underlying || '',
            displayName: item.display_name || item.displayName || '',
            tradingSymbol: item.trading_symbol || item.tradingSymbol || '',
            strikePrice: item.strike_price ?? item.strikePrice ?? null,
            optionType: item.option_type ?? item.optionType ?? null,
            expiryDate: item.expiry_date ?? item.expiryDate ?? null,
            ltp: item.ltp ?? null,
            close: item.close ?? null,
            change_pct: item.change_pct ?? null,
            source,
          });
        });
      };

      const sourceMap = { tierA, tierB, futures, optStrikes, equity };
      const sourceOrder = hasOptionIntent
        ? ['optStrikes', 'futures', 'equity', 'tierB', 'tierA']
        : hasFutureIntent
          ? ['futures', 'equity', 'tierB', 'tierA', 'optStrikes']
          : ['equity', 'tierB', 'tierA', 'futures', 'optStrikes'];
      sourceOrder.forEach(src => addResults(sourceMap[src], src));

      const qStrike = qTokens.find(t => /^\d{3,}$/.test(t)) || null;
      const qStrikeNum = qStrike ? Number(qStrike) : null;

      const score = (r) => {
        const symU = String(r.symbol || '').toUpperCase();
        const undU = String(r.underlying || '').toUpperCase();
        const dispU = String(r.displayName || '').toUpperCase();
        const tsU = String(r.tradingSymbol || '').toUpperCase();
        const symN = normalize(r.symbol);
        const undN = normalize(r.underlying);
        const dispN = normalize(r.displayName);
        const tsN = normalize(r.tradingSymbol);

        const it = String(r.instrumentType || '').toUpperCase();
        const isOption = it.startsWith('OPT');
        const isFuture = it.startsWith('FUT');
        const isCash = String(r.exchange || '').toUpperCase().includes('_EQ') || it === 'EQUITY';
        const isEtf = isCash && (dispU.includes('ETF') || symU.includes('BEES') || symU.includes('ETF'));
        const isCommodity = String(r.exchange || '').toUpperCase().includes('MCX') || it === 'FUTCOM' || it === 'OPTCOM';

        let s = 0;
        if (hasOptionIntent) {
          if (isOption) s += 14000;
          if (isFuture) s += 3500;
          if (isCash) s += 1000;
        } else if (hasFutureIntent) {
          if (isFuture) s += 14000;
          if (isCommodity) s += 1800;
          if (isCash) s += 1000;
          if (isOption) s += 500;
        } else {
          if (isCash) s += 12000;
          if (isEtf) s += 400;
          if (isFuture) s += 8000;
          if (isOption) s += 6000;
          if (isCommodity) s += 500;
        }

        if (hasOptionIntent) {
          if (r.source === 'optStrikes') s += 2600;
          if (r.source === 'futures') s += 600;
        } else if (hasFutureIntent) {
          if (r.source === 'futures') s += 2600;
          if (r.source === 'equity') s += 600;
        } else {
          if (r.source === 'equity' || r.source === 'tierA' || r.source === 'tierB') s += 1800;
        }

        if (qFirstAlpha) {
          if (undU === qFirstAlpha) s += 5000;
          if (symU === qFirstAlpha) s += 5200;
        }

        const qU = qTokens.join(' ');
        if (symN === qU) s += 5000;

        // starts-with / contains ranking based on first token only
        if (qFirstAlpha) {
          if (symU.startsWith(qFirstAlpha)) s += 4200;
          if (undU.startsWith(qFirstAlpha)) s += 4000;
          if (dispU.startsWith(qFirstAlpha)) s += 4100;
          if (tsU.startsWith(qFirstAlpha)) s += 4050;
          const si = symU.indexOf(qFirstAlpha);
          const ui = undU.indexOf(qFirstAlpha);
          const di = dispU.indexOf(qFirstAlpha);
          const ti = tsU.indexOf(qFirstAlpha);
          if (si >= 0) s += 3000 - Math.min(si, 50);
          if (ui >= 0) s += 2800 - Math.min(ui, 50);
          if (di >= 0) s += 2900 - Math.min(di, 50);
          if (ti >= 0) s += 2850 - Math.min(ti, 50);
        }

        // Exact strike match when user typed one
        if (qStrikeNum !== null && r.strikePrice !== null && r.strikePrice !== undefined) {
          const sp = Number(r.strikePrice);
          if (!Number.isNaN(sp)) {
            if (sp === qStrikeNum) s += 8000;
            else s += Math.max(0, 1200 - Math.min(Math.abs(sp - qStrikeNum), 1200));
          }
        }

        // Prefer nearest expiry for options to keep weekly/monthly contracts on top.
        if (isOption && r.expiryDate) {
          const exp = new Date(r.expiryDate);
          if (!Number.isNaN(exp.getTime())) {
            const today = new Date();
            today.setHours(0, 0, 0, 0);
            const expDay = new Date(exp.getFullYear(), exp.getMonth(), exp.getDate());
            const daysAhead = Math.floor((expDay.getTime() - today.getTime()) / 86400000);
            if (daysAhead >= 0) {
              s += Math.max(0, 1800 - Math.min(daysAhead * 20, 1800));
            }
          }
        }

        s += Math.max(0, 500 - Math.min(symU.length, 500));
        return s;
      };

      const ranked = results
        .filter(r => {
          const symN = normalize(r.symbol);
          const undN = normalize(r.underlying);
          const dispN = normalize(r.displayName);
          const tsN = normalize(r.tradingSymbol);
          const optU = String(r.optionType || '').toUpperCase();
          const isOption = String(r.instrumentType || '').toUpperCase().startsWith('OPT');
          return qTokens.every(t => {
            if (t === 'PE' || t === 'CE') {
              return optU === t || symN.includes(t) || undN.includes(t) || dispN.includes(t) || tsN.includes(t);
            }
            if (/^\d{3,}$/.test(t) && isOption && r.strikePrice !== null && r.strikePrice !== undefined) {
              const strikeVal = Number(r.strikePrice);
              const queryStrike = Number(t);
              if (Number.isFinite(strikeVal) && Number.isFinite(queryStrike)) {
                if (Math.abs(strikeVal - queryStrike) <= 300) return true;
              }
            }
            return symN.includes(t) || undN.includes(t) || dispN.includes(t) || tsN.includes(t);
          });
        })
        .sort((a, b) => score(b) - score(a));

      if (seq === searchSeq.current) {
        setSearchResults(ranked.slice(0, 20));
        setDropdownOpen(true);
      }
    } catch {}
    if (seq === searchSeq.current) setIsSearching(false);
  };

  const handleAddInstrument = async (instrument) => {
    const tokenNum = Number(instrument?.token);
    if (!Number.isFinite(tokenNum) || tokenNum <= 0) {
      return;
    }

    setTabs(prev => prev.map(tab => {
      if (tab.id !== activeTabId) return tab;
      if (tab.instruments.find(i => Number(i.token) === tokenNum)) return tab;
      return { ...tab, instruments: [...tab.instruments, { ...instrument, token: tokenNum }] };
    }));

    try {
      const addRes = await apiService.post('/watchlist/add', {
        user_id: String(user?.id || ''),
        token: String(tokenNum),
        symbol: instrument.symbol,
        exchange: instrument.exchange,
      });

      if (addRes && addRes.success === false) {
        setTabs(prev => prev.map(tab => {
          if (tab.id !== activeTabId) return tab;
          return { ...tab, instruments: tab.instruments.filter(i => Number(i.token) !== tokenNum) };
        }));
        return;
      }

      const serverToken = Number(addRes?.token || tokenNum);
      if (Number.isFinite(serverToken) && serverToken > 0 && serverToken !== tokenNum) {
        setTabs(prev => prev.map(tab => {
          if (tab.id !== activeTabId) return tab;
          return {
            ...tab,
            instruments: tab.instruments.map(i =>
              Number(i.token) === tokenNum ? { ...i, token: serverToken, id: serverToken } : i
            )
          };
        }));
      }

      await hydrateFromServer();
    } catch {
      // rollback optimistic row on failure
      setTabs(prev => prev.map(tab => {
        if (tab.id !== activeTabId) return tab;
        return { ...tab, instruments: tab.instruments.filter(i => Number(i.token) !== tokenNum) };
      }));
    }
    // Keep dropdown open so user can add multiple items via +
  };

  const handleRemoveInstrument = (token) => {
    const tokenKey = String(token);
    if (openDepthToken === tokenKey) {
      setOpenDepthToken(null);
    }
    setTabs(prev => prev.map(tab => {
      if (tab.id !== activeTabId) return tab;
      return { ...tab, instruments: tab.instruments.filter(i => i.token !== token) };
    }));
    apiService.post('/watchlist/remove', { user_id: String(user?.id || ''), token: String(token) }).catch(() => {});
  };

  const persistReorder = useCallback(async (orderedInstruments) => {
    if (!user?.id || activeTabId !== 1) return;
    const tokens = (orderedInstruments || [])
      .map((i) => String(i?.token || '').trim())
      .filter((t) => /^\d+$/.test(t));
    if (!tokens.length) return;
    await apiService.post('/watchlist/reorder', {
      user_id: String(user.id),
      tokens,
    });
  }, [activeTabId, user?.id]);

  const handleDragEnd = async (event) => {
    const { active, over } = event;
    if (!active?.id || !over?.id || active.id === over.id) return;

    let reorderedForPersist = null;

    setTabs((prev) => prev.map((tab) => {
      if (tab.id !== activeTabId) return tab;
      const items = tab.instruments || [];
      const oldIndex = items.findIndex((inst) => getInstrumentDndId(inst) === String(active.id));
      const newIndex = items.findIndex((inst) => getInstrumentDndId(inst) === String(over.id));
      if (oldIndex < 0 || newIndex < 0 || oldIndex === newIndex) return tab;
      const nextInstruments = arrayMove(items, oldIndex, newIndex);
      reorderedForPersist = nextInstruments;
      return { ...tab, instruments: nextInstruments };
    }));

    if (reorderedForPersist) {
      try {
        await persistReorder(reorderedForPersist);
      } catch {
        await hydrateFromServer();
      }
    }
  };

  const getDisplayedPrice = (instrument) => {
    const prices = pulse?.prices;
    const token = String(instrument.token);
    const p = prices ? (prices[token] ?? prices[instrument.symbol]) : null;
    if (p !== null && p !== undefined) return p;

    const liveTick = tickByToken[String(instrument.token)];
    if (liveTick?.ltp !== null && liveTick?.ltp !== undefined) return liveTick.ltp;

    const ex = String(instrument?.exchange || '').toUpperCase();
    const isCommodity = ex.includes('MCX') || ex.includes('COM');
    const marketActive = isCommodity
      ? (pulse?.marketActiveCommodity ?? pulse?.marketActive ?? pulse?.market_active_commodity ?? pulse?.market_active)
      : (pulse?.marketActiveEquity ?? pulse?.marketActive ?? pulse?.market_active_equity ?? pulse?.market_active);

    if (marketActive === false) return instrument.close ?? instrument.ltp ?? null;
    return instrument.ltp ?? null;
  };

  const formatOptionLabel = (r) => {
    const it = String(r.instrumentType || r.instrument_type || '').toUpperCase();
    const expiry = r.expiryDate || r.expiry_date;
    const strike = r.strikePrice ?? r.strike_price;
    const opt = r.optionType || r.option_type;
    const underlying = (r.underlying || r.symbol || '').toUpperCase();
    const isOpt = it.startsWith('OPT') && expiry && strike !== null && strike !== undefined && opt;
    if (!isOpt) return null;
    const d = new Date(expiry);
    const monthShort = d.toLocaleString('en-GB', { month: 'short' });
    const day = String(d.getDate()).padStart(2, '0');
    const strikeNum = Number(strike);
    const strikeTxt = Number.isFinite(strikeNum) ? String(Math.trunc(strikeNum)) : String(strike);
    return `${underlying} ${strikeTxt} ${String(opt).toUpperCase()} ${day} ${monthShort}`;
  };

  const getChangePct = (instrument, ltpOverride = null) => {
    const ltp = ltpOverride !== null && ltpOverride !== undefined ? ltpOverride : instrument?.ltp;
    const close = instrument?.close;
    if (typeof instrument?.change_pct === 'number') return instrument.change_pct;
    if (ltp == null || close == null || Number(close) === 0) return null;
    const pct = ((Number(ltp) - Number(close)) / Number(close)) * 100;
    return Number.isFinite(pct) ? pct : null;
  };

  // styles
  const card = { backgroundColor: 'var(--surface)', borderRadius: '8px', border: '1px solid var(--border)', overflow: 'hidden' };
  const tabRow = { display: 'flex', borderBottom: '2px solid var(--border)', backgroundColor: 'var(--surface2)' };
  const tabBtn = (active) => ({ flex: 1, padding: '10px 4px', border: 'none', cursor: 'pointer', fontSize: '12px', fontWeight: active ? 700 : 500, color: active ? 'var(--accent)' : 'var(--muted)', backgroundColor: 'transparent', borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent', marginBottom: '-2px', transition: 'all 0.15s' });
  const instrRow = { display: 'flex', alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--border)', gap: '8px' };
  const symbolStyle = { flex: 1, fontSize: '13px', fontWeight: 600, color: 'var(--text)' };
  const exStyle = { fontSize: '10px', color: 'var(--muted)', marginLeft: '4px' };
  const ltpStyle = (v) => ({ fontSize: '13px', fontWeight: 600, color: v === null ? 'var(--muted)' : 'var(--text)', minWidth: '60px', textAlign: 'right' });
  const removeBtn = { border: 'none', background: 'none', cursor: 'pointer', padding: '4px', color: '#dc2626', display: 'flex', alignItems: 'center' };

  return (
    <div
      style={{
        minHeight: compact ? '100%' : '100vh',
        height: compact ? '100%' : 'auto',
        overflow: compact ? 'hidden' : 'visible',
        padding: compact ? '0' : '24px',
        backgroundColor: compact ? 'transparent' : 'var(--bg)',
        fontFamily: "system-ui, -apple-system, sans-serif",
        color: 'var(--text)'
      }}
    >
      <div
        style={{
          maxWidth: compact ? '100%' : '480px',
          margin: compact ? '0' : '0 auto',
          height: compact ? '100%' : 'auto',
          minHeight: compact ? 0 : undefined,
          display: compact ? 'flex' : 'block',
          flexDirection: compact ? 'column' : 'row'
        }}
      >
        {!compact && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
            <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 700, color: 'var(--text)' }}>Watchlist</h2>
          </div>
        )}

        <div ref={searchBoxRef} style={{ ...card, marginBottom: compact ? '8px' : '12px', padding: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', border: '1px solid var(--border)', borderRadius: '8px', padding: '6px 10px', backgroundColor: 'var(--surface)' }}>
            <Search size={14} color="var(--muted)" />
            <input
              value={searchQuery}
              onChange={handleSearchInput}
              onFocus={() => { if (searchResults.length) setDropdownOpen(true); }}
              placeholder="Search symbol, equities/ETFs, futures, options…"
              style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontSize: '13px', color: 'var(--text)' }}
            />
            {isSearching && <RefreshCw size={14} className="animate-spin" color="var(--muted)" />}
          </div>
          {dropdownOpen && searchResults.length > 0 && (
            <div style={{ marginTop: '8px', maxHeight: '240px', overflowY: 'auto' }}>
              {searchResults.map(r => {
                const isAdded = watchlistTokenKeys.has(toTokenKey(r.token));
                return (
                <div key={r.id} style={{ padding: '8px 10px', borderRadius: '6px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'var(--surface2)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', flex: 1 }}>
                    {(() => {
                        const label = formatOptionLabel(r);
                        if (label) {
                        const price = r.ltp ?? r.close;
                        const pct = (typeof r.change_pct === 'number') ? r.change_pct : null;
                        return (
                          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                              <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text)' }}>{label}</span>
                            <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '10px', fontSize: '12px' }}>
                              {pct !== null && <span style={{ color: '#a1a1aa' }}>{pct.toFixed(2)} %</span>}
                              {price != null && <span style={{ color: 'var(--text)', fontWeight: 800 }}>{Number(price).toFixed(2)}</span>}
                            </span>
                          </div>
                        );
                      }
                      return (
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '10px' }}>
                          <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text)' }}>{r.symbol}</span>
                          <span style={{ fontSize: '11px', color: 'var(--muted)' }}>{r.exchange} · {r.instrumentType}</span>
                        </div>
                      );
                    })()}
                  </div>

                  <button
                    type="button"
                    onClick={() => { if (!isAdded) handleAddInstrument(r); }}
                    disabled={isAdded}
                    style={{
                      border: `1px solid ${isAdded ? '#16a34a55' : 'var(--border)'}`,
                      background: 'var(--surface)',
                      color: isAdded ? '#16a34a' : 'var(--text)',
                      cursor: isAdded ? 'default' : 'pointer',
                      borderRadius: '8px',
                      padding: '6px 10px',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      opacity: isAdded ? 0.95 : 1,
                    }}
                    title={isAdded ? "Added" : "Add"}
                  >
                    {isAdded ? <Check size={14} /> : <Plus size={14} />}
                  </button>
                </div>
              )})}
            </div>
          )}
          {searchQuery.length >= 2 && !isSearching && searchResults.length === 0 && (
            <div style={{ padding: '8px', fontSize: '13px', color: 'var(--muted)', textAlign: 'center' }}>No results</div>
          )}
        </div>

        <div style={compact ? { ...card, display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 } : card}>
          <div style={tabRow}>
            {tabs.map(tab => (
              <button key={tab.id} style={tabBtn(tab.id === activeTabId)} onClick={() => setActiveTabId(tab.id)}>
                {tab.name} <span style={{ ...exStyle, display: 'inline' }}>({tab.instruments.length})</span>
              </button>
            ))}
          </div>
          <div style={compact ? { flex: 1, minHeight: 0, overflowY: 'auto' } : undefined}>
            {activeTab.instruments.length === 0 ? (
              <div
                style={compact
                  ? { minHeight: '220px', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px', textAlign: 'center', color: 'var(--muted)', fontSize: '13px' }
                  : { padding: '32px', textAlign: 'center', color: 'var(--muted)', fontSize: '13px' }
                }
              >
                No instruments in this watchlist.<br />Click Add to search and add instruments.
              </div>
            ) : (
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
                <SortableContext
                  items={(activeTab.instruments || []).map((inst) => getInstrumentDndId(inst)).filter(Boolean)}
                  strategy={verticalListSortingStrategy}
                >
                {activeTab.instruments.map(inst => {
                const dndId = getInstrumentDndId(inst);
                if (!dndId) return null;
                return (
                <SortableInstrumentRow key={inst.token} id={dndId}>
                {({ setNodeRef, attributes, listeners, style: dndStyle }) => {
                const ltp = getDisplayedPrice(inst);
                const ex = String(inst?.exchange || '').toUpperCase();
                const isCommodity = ex.includes('MCX') || ex.includes('COM');
                const marketActive = isCommodity
                  ? (pulse?.marketActiveCommodity ?? pulse?.marketActive ?? pulse?.market_active_commodity ?? pulse?.market_active) !== false
                  : (pulse?.marketActiveEquity ?? pulse?.marketActive ?? pulse?.market_active_equity ?? pulse?.market_active) !== false;
                const tick = tickByToken[String(inst.token)];
                const ladder = getDepthLadder(inst, tick);
                const bestBid = ladder[0]?.bid?.price ?? tick?.best_bid ?? tick?.bid ?? tick?.bid_price ?? inst?.bestBid ?? null;
                const bestAsk = ladder[0]?.ask?.price ?? tick?.best_ask ?? tick?.ask ?? tick?.ask_price ?? inst?.bestAsk ?? null;

                const label = formatOptionLabel({
                  instrumentType: inst.instrumentType,
                  expiryDate: inst.expiryDate,
                  strikePrice: inst.strikePrice,
                  optionType: inst.optionType,
                  underlying: inst.underlying,
                  symbol: inst.symbol,
                });
                const title = label || inst.symbol;
                const pct = getChangePct(inst, ltp);
                return (
                  <div ref={setNodeRef} style={{ ...instrRow, ...dndStyle, flexDirection: 'column', alignItems: 'stretch' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <button
                      type="button"
                      style={{ border: 'none', background: 'transparent', cursor: 'grab', padding: '4px', color: 'var(--muted)', display: 'flex', alignItems: 'center' }}
                      title="Drag to reorder"
                      {...attributes}
                      {...listeners}
                    >
                      <GripVertical size={14} />
                    </button>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                        <span style={symbolStyle}>{title}</span>
                      </div>
                      <span style={exStyle}>{inst.exchange}</span>
                    </div>
                    {pct !== null && <span style={{ fontSize: '12px', color: 'var(--muted)', minWidth: '70px', textAlign: 'right' }}>{pct.toFixed(2)} %</span>}
                    <span style={ltpStyle(ltp)}>{ltp !== null ? Number(ltp).toFixed(2) : '—'}</span>

                    <button
                      style={{ border: 'none', background: 'transparent', cursor: 'pointer', padding: '4px', color: 'var(--muted)', display: 'flex', alignItems: 'center' }}
                      title="Bid/Ask"
                      onClick={() => {
                        const tokenKey = String(inst.token);
                        setOpenDepthToken(prev => (prev === tokenKey ? null : tokenKey));
                      }}
                    >
                      <ChevronDown size={14} />
                    </button>

                    <button className="trade-btn buy" onClick={() => onOpenOrderModal?.({ symbol: inst.symbol, displaySymbol: label || inst.symbol, token: inst.token, exchange: inst.exchange, ltp: ltp, instrumentType: inst.instrumentType, expiryDate: inst.expiryDate, strikePrice: inst.strikePrice, optionType: inst.optionType, underlying: inst.underlying, lot_size: inst.lot_size, lotSize: inst.lot_size }, 'BUY')}>BUY</button>
                    <button className="trade-btn sell" onClick={() => onOpenOrderModal?.({ symbol: inst.symbol, displaySymbol: label || inst.symbol, token: inst.token, exchange: inst.exchange, ltp: ltp, instrumentType: inst.instrumentType, expiryDate: inst.expiryDate, strikePrice: inst.strikePrice, optionType: inst.optionType, underlying: inst.underlying, lot_size: inst.lot_size, lotSize: inst.lot_size }, 'SELL')}>SELL</button>
                    <button
                      className="trade-btn"
                      style={{ background: '#2563eb', color: '#fff' }}
                      onClick={() => onOpenChart?.(inst, label || inst.symbol)}
                    >
                      Chart
                    </button>
                    <button style={removeBtn} onClick={() => handleRemoveInstrument(inst.token)} title="Remove"><X size={14} /></button>
                    </div>

                    {openDepthToken === String(inst.token) && (
                      <div style={{ marginTop: '8px', padding: '10px 12px', borderRadius: '8px', border: '1px solid var(--border)', background: 'var(--surface)' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '8px' }}>
                          <div style={{ fontSize: '12px', color: 'var(--text)' }}>
                            <div style={{ fontSize: '11px', color: 'var(--muted)', marginBottom: '4px' }}>Best Bid</div>
                            <div style={{ fontWeight: 700 }}>{bestBid !== null ? Number(bestBid).toFixed(2) : '—'}</div>
                          </div>
                          <div style={{ fontSize: '12px', color: 'var(--text)', textAlign: 'right' }}>
                            <div style={{ fontSize: '11px', color: 'var(--muted)', marginBottom: '4px' }}>Best Ask</div>
                            <div style={{ fontWeight: 700 }}>{bestAsk !== null ? Number(bestAsk).toFixed(2) : '—'}</div>
                          </div>
                        </div>

                        <div style={{ display: 'grid', gridTemplateColumns: '44px 1fr 1fr 1fr 1fr', gap: '6px', fontSize: '11px', color: 'var(--muted)', marginBottom: '6px' }}>
                          <div>Lvl</div>
                          <div style={{ textAlign: 'right' }}>Bid Qty</div>
                          <div style={{ textAlign: 'right' }}>Bid Px</div>
                          <div style={{ textAlign: 'right' }}>Ask Px</div>
                          <div style={{ textAlign: 'right' }}>Ask Qty</div>
                        </div>

                        {ladder.map((row) => (
                          <div key={`${inst.token}-depth-${row.level}`} style={{ display: 'grid', gridTemplateColumns: '44px 1fr 1fr 1fr 1fr', gap: '6px', fontSize: '12px', color: 'var(--text)', padding: '2px 0' }}>
                            <div style={{ color: 'var(--muted)' }}>{row.level}</div>
                            <div style={{ textAlign: 'right' }}>{row.bid ? row.bid.qty.toLocaleString() : '—'}</div>
                            <div style={{ textAlign: 'right' }}>{row.bid ? row.bid.price.toFixed(2) : '—'}</div>
                            <div style={{ textAlign: 'right' }}>{row.ask ? row.ask.price.toFixed(2) : '—'}</div>
                            <div style={{ textAlign: 'right' }}>{row.ask ? row.ask.qty.toLocaleString() : '—'}</div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              }}
              </SortableInstrumentRow>
                )})}
                </SortableContext>
              </DndContext>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default WatchlistPage;
