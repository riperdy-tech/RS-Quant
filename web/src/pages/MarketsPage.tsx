import React, { useEffect, useState, useRef } from 'react';
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart2,
  CheckCircle2,
  LineChart as LucideLineChart,
  Pause,
  Play,
  Radio,
  RefreshCw,
  Sliders,
} from 'lucide-react';
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts';
import { api } from '../services/apiClient';

export const MarketsPage: React.FC = () => {
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [isVisualPaused, setIsVisualPaused] = useState(false);
  const [isLiveStreaming, setIsLiveStreaming] = useState(true);
  const [spread, setSpread] = useState('0.10');
  const [microprice, setMicroprice] = useState('76119.50');
  const [obi, setObi] = useState('0.00');
  const [cvd, setCvd] = useState('+0.00');
  const [ofi, setOfi] = useState('+0.00');

  const [priceHistory, setPriceHistory] = useState<Array<{ time: string; price: number; cvd: number }>>([]);
  const [bids, setBids] = useState<Array<{ price: string; size: string; total: string }>>([]);
  const [asks, setAsks] = useState<Array<{ price: string; size: string; total: string }>>([]);
  const [ticker, setTicker] = useState<any>(null);

  const cvdAccumulator = useRef<number>(0);

  const fetchLiveMarketData = async () => {
    if (isVisualPaused) return;
    try {
      const [depthData, tickerData] = await Promise.all([
        api.getMarketDepth(symbol).catch(() => null),
        api.getMarketTicker(symbol).catch(() => null),
      ]);

      if (tickerData) {
        setTicker(tickerData);
        const lastPr = Number(tickerData.last_price || tickerData.mark_price || 0);
        if (lastPr > 0) {
          const nowStr = new Date().toLocaleTimeString();
          setPriceHistory((prev) => {
            const next = [...prev, { time: nowStr, price: lastPr, cvd: Number(cvdAccumulator.current.toFixed(2)) }];
            return next.slice(-30);
          });
        }
      }

      if (depthData && depthData.bids && depthData.asks) {
        let runningBidTotal = 0;
        const formattedBids = depthData.bids.slice(0, 8).map(([p, s]: [string, string]) => {
          runningBidTotal += Number(s);
          return { price: Number(p).toFixed(2), size: Number(s).toFixed(4), total: runningBidTotal.toFixed(4) };
        });

        let runningAskTotal = 0;
        const formattedAsks = depthData.asks.slice(0, 8).map(([p, s]: [string, string]) => {
          runningAskTotal += Number(s);
          return { price: Number(p).toFixed(2), size: Number(s).toFixed(4), total: runningAskTotal.toFixed(4) };
        });

        setBids(formattedBids);
        setAsks(formattedAsks);

        if (formattedBids.length > 0 && formattedAsks.length > 0) {
          const b1 = Number(formattedBids[0].price);
          const a1 = Number(formattedAsks[0].price);
          const b_sz = Number(formattedBids[0].size);
          const a_sz = Number(formattedAsks[0].size);

          const spr = Math.max(0, a1 - b1);
          setSpread(spr.toFixed(2));

          const micro = (b1 * a_sz + a1 * b_sz) / (a_sz + b_sz);
          setMicroprice(micro.toFixed(2));

          const imb = (b_sz - a_sz) / (b_sz + a_sz);
          setObi(imb > 0 ? `+${imb.toFixed(2)}` : imb.toFixed(2));
        }
      }
    } catch {
      // transient network error
    }
  };

  useEffect(() => {
    fetchLiveMarketData();
    const interval = setInterval(fetchLiveMarketData, 1000);
    return () => clearInterval(interval);
  }, [symbol, isVisualPaused]);

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
                Market Depth & Microstructure
              </h1>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-emerald-50 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800 rounded-full text-[11px] font-bold">
                <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                Bitget Live Stream
              </span>
            </div>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              Live Bitget UTA V3 L2 order book ladder, microprice, book imbalance (OBI), and funding rate.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Symbol Toggle */}
          <div className="flex items-center bg-slate-100 dark:bg-slate-800 p-1 rounded-lg border border-slate-200 dark:border-slate-700">
            {['BTCUSDT', 'ETHUSDT'].map((sym) => (
              <button
                key={sym}
                onClick={() => setSymbol(sym)}
                className={`px-3 py-1 rounded-md text-xs font-mono font-bold transition-colors ${
                  symbol === sym
                    ? 'bg-white dark:bg-slate-900 text-indigo-600 dark:text-indigo-400 shadow-sm'
                    : 'text-slate-600 dark:text-slate-400 hover:text-slate-900'
                }`}
              >
                {sym}
              </button>
            ))}
          </div>

          <button
            onClick={() => setIsVisualPaused(!isVisualPaused)}
            className={`px-3.5 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors ${
              isVisualPaused
                ? 'bg-amber-600 text-white hover:bg-amber-700'
                : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200'
            }`}
          >
            {isVisualPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            {isVisualPaused ? 'Resume Live Stream' : 'Pause Live Stream'}
          </button>
        </div>
      </div>

      {/* Microstructure Metric Gauges (§15.2) */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Best Spread</span>
          <span className="text-xl font-mono font-bold text-slate-800 dark:text-slate-200">${spread}</span>
          <span className="text-[10px] text-slate-400 block">Bitget live spread</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Microprice</span>
          <span className="text-xl font-mono font-bold text-indigo-600 dark:text-indigo-400">${microprice}</span>
          <span className="text-[10px] text-slate-400 block">Queue-weighted mid</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Book Imbalance (OBI)</span>
          <span className={`text-xl font-mono font-bold ${Number(obi) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>
            {obi}
          </span>
          <span className="text-[10px] text-slate-400 block">Net bid leaning [-1, +1]</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">24h Volume</span>
          <span className="text-xl font-mono font-bold text-slate-800 dark:text-slate-200">
            {ticker?.volume_24h ? `${Number(ticker.volume_24h).toLocaleString()} ${symbol.slice(0, 3)}` : 'Live'}
          </span>
          <span className="text-[10px] text-slate-400 block">Bitget 24h Base Volume</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Funding Rate</span>
          <span className="text-xl font-mono font-bold text-indigo-600 dark:text-indigo-400">
            {ticker?.funding_rate ? `${(Number(ticker.funding_rate) * 100).toFixed(4)}%` : '0.0064%'}
          </span>
          <span className="text-[10px] text-slate-400 block">8h Bitget UTA Rate</span>
        </div>
      </div>

      {/* Main Grid: Chart & Order Book Ladder */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Real-time Microstructure Chart */}
        <div className="lg:col-span-2 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <BarChart2 className="w-4 h-4 text-indigo-600" />
              Live Bitget Price Stream ({symbol})
            </h2>
            {isVisualPaused && (
              <span className="px-2 py-0.5 rounded text-[11px] bg-amber-100 text-amber-800 font-semibold">
                Visual Frozen (Engine Active)
              </span>
            )}
          </div>

          <div className="h-72">
            {priceHistory.length === 0 ? (
              <div className="h-full flex items-center justify-center text-slate-400 text-xs">
                Streaming initial Bitget ticks...
              </div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={priceHistory}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                  <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#64748b' }} />
                  <YAxis
                    domain={['dataMin - 10', 'dataMax + 10']}
                    tick={{ fontSize: 11, fill: '#64748b' }}
                    tickFormatter={(v) => `$${Number(v).toLocaleString()}`}
                  />
                  <Tooltip
                    contentStyle={{
                      borderRadius: '8px',
                      border: 'none',
                      boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)',
                    }}
                    formatter={(val: any) => [`$${Number(val).toLocaleString()}`, 'Price']}
                  />
                  <Line
                    type="monotone"
                    dataKey="price"
                    stroke="#4f46e5"
                    strokeWidth={2}
                    dot={false}
                    isAnimationActive={!isVisualPaused}
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* L2 Depth Ladder (§15.2) */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden flex flex-col">
          <div className="p-3.5 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
            <h2 className="font-bold text-sm text-slate-900 dark:text-slate-100">
              Live Bitget L2 Depth
            </h2>
            <span className="text-[11px] font-mono text-slate-400">Top 8 Levels</span>
          </div>

          <div className="p-3 space-y-3 flex-1 flex flex-col justify-between text-xs">
            {/* Asks (Red, top down) */}
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-slate-400 font-semibold uppercase">
                <span>Price (USDT)</span>
                <span>Size</span>
                <span>Total</span>
              </div>
              {asks.length === 0 ? (
                <div className="text-slate-400 py-3 text-center">Loading asks...</div>
              ) : (
                asks.slice().reverse().map((a, i) => (
                  <div key={i} className="flex justify-between font-mono text-[11px] text-rose-600">
                    <span>${a.price}</span>
                    <span className="text-slate-600 dark:text-slate-400">{a.size}</span>
                    <span className="text-slate-400">{a.total}</span>
                  </div>
                ))
              )}
            </div>

            {/* Mid Price Spread Indicator */}
            <div className="py-2 px-3 bg-slate-50 dark:bg-slate-800 rounded-lg text-center font-mono font-bold text-slate-900 dark:text-slate-100 border border-slate-200 dark:border-slate-700">
              Mid: ${microprice} | Spread: ${spread}
            </div>

            {/* Bids (Green, bottom) */}
            <div className="space-y-1">
              {bids.length === 0 ? (
                <div className="text-slate-400 py-3 text-center">Loading bids...</div>
              ) : (
                bids.map((b, i) => (
                  <div key={i} className="flex justify-between font-mono text-[11px] text-emerald-600 dark:text-emerald-400">
                    <span>${b.price}</span>
                    <span className="text-slate-600 dark:text-slate-400">{b.size}</span>
                    <span className="text-slate-400">{b.total}</span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
