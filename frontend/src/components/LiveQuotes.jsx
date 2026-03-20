// Live Quotes Component - Data Flow Indicators with Real-time Price Display
import React, { useState, useEffect } from 'react';
import { TrendingUp, TrendingDown, Activity } from 'lucide-react';
import { useMarketPulse } from '../hooks/useMarketPulse';

const CORE_INSTRUMENTS = [
  { key: 'NIFTY', label: 'NIFTY 50', exchange: 'NSE', badge: 'bg-blue-100 text-blue-700' },
  { key: 'BANKNIFTY', label: 'BANKNIFTY', exchange: 'NSE', badge: 'bg-indigo-100 text-indigo-700' },
  { key: 'SENSEX', label: 'SENSEX', exchange: 'BSE', badge: 'bg-amber-100 text-amber-700' },
  { key: 'CRUDEOIL', label: 'CRUDEOIL FUT', exchange: 'MCX', badge: 'bg-purple-100 text-purple-700' },
  { key: 'RELIANCE', label: 'RELIANCE', exchange: 'NSE', badge: 'bg-emerald-100 text-emerald-700' },
];

const LiveQuotes = () => {
  const { pulse, marketActive } = useMarketPulse();
  const [quotes, setQuotes] = useState(() =>
    CORE_INSTRUMENTS.reduce((acc, instrument) => {
      acc[instrument.key] = {
        symbol: instrument.key,
        price: null,
        status: 'loading',
        exchange: instrument.exchange,
      };
      return acc;
    }, {})
  );
  const [lastUpdate, setLastUpdate] = useState(null);
  const [dataFlowStatus, setDataFlowStatus] = useState('checking');
  const [marketDataSource, setMarketDataSource] = useState('unknown');

  useEffect(() => {
    if (!pulse?.timestamp) {
      return;
    }

    setQuotes((prev) => {
      const updated = { ...prev };
      CORE_INSTRUMENTS.forEach(({ key }) => {
        const raw = pulse?.prices?.[key];
        const price = Number(raw);
        if (Number.isFinite(price) && price > 0) {
          updated[key] = { ...updated[key], price, status: 'success' };
        } else {
          updated[key] = { ...updated[key], status: marketActive ? 'no_data' : 'success' };
        }
      });
      return updated;
    });

    setLastUpdate(new Date(pulse.timestamp));
    setDataFlowStatus(marketActive ? 'active' : 'waiting');
    setMarketDataSource(marketActive ? 'live_cache' : 'snapshot');
  }, [pulse?.timestamp, pulse?.prices, marketActive]);

  const getStatusColor = (status) => {
    switch (status) {
      case 'success': return 'bg-green-100 text-green-800';
      case 'no_data': return 'bg-yellow-100 text-yellow-800';
      case 'error': return 'bg-red-100 text-red-800';
      case 'loading': return 'bg-gray-100 text-gray-600';
      default: return 'bg-gray-100 text-gray-600';
    }
  };

  const getDataFlowIndicator = () => {
    switch (dataFlowStatus) {
      case 'active': return { color: 'bg-green-500', text: 'Data Flowing', pulse: true };
      case 'waiting': return { color: 'bg-yellow-500', text: 'Waiting for Data', pulse: false };
      case 'error': return { color: 'bg-red-500', text: 'Connection Error', pulse: false };
      default: return { color: 'bg-gray-400', text: 'Checking...', pulse: false };
    }
  };

  const formatPrice = (price) => {
    if (price === null || price === undefined || price === 0) return '--';
    return typeof price === 'number' ? price.toFixed(2) : price;
  };

  const getSourceBadge = () => {
    switch (marketDataSource) {
      case 'commodity_futures': return { text: 'MCX CURRENT FUT', classes: 'bg-purple-100 text-purple-800' };
      case 'live_cache': return { text: 'LIVE CACHE', classes: 'bg-green-100 text-green-800' };
      case 'snapshot': return { text: 'SNAPSHOT', classes: 'bg-yellow-100 text-yellow-800' };
      case 'mixed': return { text: 'MIXED', classes: 'bg-blue-100 text-blue-800' };
      default: return { text: 'UNKNOWN', classes: 'bg-gray-100 text-gray-700' };
    }
  };

  const dataFlowIndicator = getDataFlowIndicator();
  const sourceBadge = getSourceBadge();

  return (
    <div className="rounded-lg p-4 mb-4 bg-zinc-800 border border-zinc-700">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center space-x-3">
          <Activity className="w-5 h-5 text-blue-600" />
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold">Market Data Stream</h3>
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${sourceBadge.classes}`}>
                {sourceBadge.text}
              </span>
            </div>
            <p className="text-xs tn-muted">Real-time instrument prices</p>
          </div>
        </div>
        <div className="flex items-center space-x-3">
          <div className="text-right">
              <div className="text-xs tn-muted">Last Update</div>
              <div className="text-sm font-mono">
                {lastUpdate ? lastUpdate.toLocaleTimeString() : 'Waiting...'}
              </div>
            </div>
          <div className={`flex items-center space-x-2 px-3 py-1 rounded-full ${getStatusColor('success')}`}>
            <div className={`w-2 h-2 rounded-full ${dataFlowIndicator.color} ${dataFlowIndicator.pulse ? 'animate-pulse' : ''}`}></div>
            <span className="text-xs font-medium">{dataFlowIndicator.text}</span>
          </div>
        </div>
      </div>

      {/* Price Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2 mb-4">
        {CORE_INSTRUMENTS.map(({ key, label, exchange, badge }) => (
          <div key={key} className="rounded-lg p-2 transition-shadow bg-zinc-900 border border-zinc-700">
            <div className="flex items-center justify-between mb-1">
              <div className="text-xs font-semibold">{label}</div>
              <div className={`text-[10px] px-1.5 py-0.5 rounded-full ${badge}`}>{exchange}</div>
            </div>
            <div className={`text-lg font-bold ${quotes[key].status === 'success' ? 'text-green-600' : 'text-gray-400'}`}>
              {formatPrice(quotes[key].price)}
            </div>
            <div className={`text-[11px] mt-0.5 tn-muted`}>
              {quotes[key].status === 'success'
                ? '✓ Live'
                : quotes[key].status === 'no_data'
                  ? 'No Data'
                  : quotes[key].status}
            </div>
          </div>
        ))}
      </div>

      {/* Data Summary Footer */}
      <div className="rounded-lg p-3 bg-zinc-900 border border-zinc-700">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <div className="text-sm">
              <span className="tn-muted">Active Instruments: </span>
              <span className="font-semibold text-green-600">
                {Object.values(quotes).filter(q => q.status === 'success').length} / {CORE_INSTRUMENTS.length}
              </span>
            </div>
            <div className="text-sm">
              <span className="tn-muted">Update Frequency: </span>
              <span className="font-semibold text-blue-600">WebSocket Push</span>
            </div>
          </div>
          <div className="text-xs tn-muted">
            Endpoint: /api/v2/ws/prices | Status: {dataFlowStatus}
          </div>
        </div>
      </div>
    </div>
  );
};

export default LiveQuotes;
