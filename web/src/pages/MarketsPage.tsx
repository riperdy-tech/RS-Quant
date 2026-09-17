import React, { useEffect, useState } from 'react';
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BarChart2,
  CheckCircle2,
  LineChart as LucideLineChart,
  Pause,
  Play,
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

export const MarketsPage: React.FC = () => {
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [isVisualPaused, setIsVisualPaused] = useState(false);
  const [spread, setSpread] = useState('0.50');
  const [microprice, setMicroprice] = useState('65248.80');
  const [obi, setObi] = useState('0.18'); // Order Book Imbalance (-1 to +1)
  const [cvd, setCvd] = useState('+14.25'); // Cumulative Volume Delta
  const [ofi, setOfi] = useState('+4.10'); // Order Flow Imbalance

  const [priceHistory, setPriceHistory] = useState([
    { time: '12:00:00', price: 65150, cvd: 0 },
    { time: '12:00:15', price: 65180, cvd: 2.1 },
    { time: '12:00:30', price: 65210, cvd: 5.4 },
    { time: '12:00:45', price: 65200, cvd: 4.8 },
    { time: '12:01:00', price: 65230, cvd: 8.2 },
    { time: '12:01:15', price: 65250, cvd: 14.25 },
  ]);

  const bids = [
    { price: '65248.50', size: '1.42', total: '1.42' },
    { price: '65248.00', size: '3.10', total: '4.52' },
    { price: '65247.50', size: '5.80', total: '10.32' },
    { price: '65247.00', size: '8.40', total: '18.72' },
    { price: '65246.50', size: '12.00', total: '30.72' },
  ];

  const asks = [
    { price: '65249.00', size: '1.15', total: '1.15' },
    { price: '65249.50', size: '2.80', total: '3.95' },
    { price: '65250.00', size: '4.60', total: '8.55' },
    { price: '65250.50', size: '7.20', total: '15.75' },
    { price: '65251.00', size: '11.50', total: '27.25' },
  ];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
              Market Depth & Microstructure
            </h1>
            <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
              Live L2 order book ladder, microprice, orderflow imbalance (OFI), and CVD.
            </p>
          </div>
          <span className="px-2.5 py-1 bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-400 border border-indigo-200 dark:border-indigo-900 rounded-lg text-xs font-mono font-bold">
            {symbol}
          </span>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => setIsVisualPaused(!isVisualPaused)}
            className={`px-3.5 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-colors ${
              isVisualPaused
                ? 'bg-amber-600 text-white hover:bg-amber-700'
                : 'bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 hover:bg-slate-200'
            }`}
          >
            {isVisualPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
            {isVisualPaused ? 'Resume UI Stream' : 'Pause UI Stream'}
          </button>
        </div>
      </div>

      {/* Microstructure Metric Gauges (§15.2) */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Best Spread</span>
          <span className="text-xl font-mono font-bold text-slate-800 dark:text-slate-200">${spread}</span>
          <span className="text-[10px] text-slate-400 block">0.76 bps</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Microprice</span>
          <span className="text-xl font-mono font-bold text-indigo-600 dark:text-indigo-400">${microprice}</span>
          <span className="text-[10px] text-slate-400 block">Queue-weighted mid</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Book Imbalance (OBI)</span>
          <span className="text-xl font-mono font-bold text-emerald-600 dark:text-emerald-400">{obi}</span>
          <span className="text-[10px] text-slate-400 block">Net bid leaning</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">CVD (Delta)</span>
          <span className="text-xl font-mono font-bold text-emerald-600 dark:text-emerald-400">{cvd}</span>
          <span className="text-[10px] text-slate-400 block">Cumulative taker vol</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm">
          <span className="text-[11px] text-slate-400 font-semibold uppercase block">Order Flow Imbalance (OFI)</span>
          <span className="text-xl font-mono font-bold text-indigo-600 dark:text-indigo-400">{ofi}</span>
          <span className="text-[10px] text-slate-400 block">Durable delta flow</span>
        </div>
      </div>

      {/* Main Grid: Chart & Order Book Ladder */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Real-time Microstructure Chart */}
        <div className="lg:col-span-2 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 p-5 shadow-sm space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <BarChart2 className="w-4 h-4 text-indigo-600" />
              Microprice & Taker CVD Flow
            </h2>
            {isVisualPaused && (
              <span className="px-2 py-0.5 rounded text-[11px] bg-amber-100 text-amber-800 font-semibold">
                Visual Frozen (Engine Active)
              </span>
            )}
          </div>

          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={priceHistory}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#64748b' }} />
                <YAxis
                  domain={['dataMin - 20', 'dataMax + 20']}
                  tick={{ fontSize: 11, fill: '#64748b' }}
                  tickFormatter={(v) => `$${v}`}
                />
                <Tooltip
                  contentStyle={{
                    borderRadius: '8px',
                    border: 'none',
                    boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)',
                  }}
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
          </div>
        </div>

        {/* L2 Depth Ladder (§15.2) */}
        <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden flex flex-col">
          <div className="p-3.5 border-b border-slate-100 dark:border-slate-800">
            <h2 className="font-bold text-sm text-slate-900 dark:text-slate-100">
              L2 Order Book Ladder
            </h2>
          </div>

          <div className="p-3 space-y-3 flex-1 flex flex-col justify-between text-xs">
            {/* Asks (Red, top down) */}
            <div className="space-y-1">
              <div className="flex justify-between text-[10px] text-slate-400 font-semibold uppercase">
                <span>Price</span>
                <span>Size</span>
                <span>Total</span>
              </div>
              {asks.slice().reverse().map((a, i) => (
                <div key={i} className="flex justify-between font-mono text-[11px] text-rose-600">
                  <span>${a.price}</span>
                  <span className="text-slate-600 dark:text-slate-400">{a.size}</span>
                  <span className="text-slate-400">{a.total}</span>
                </div>
              ))}
            </div>

            {/* Mid Price Spread Indicator */}
            <div className="py-2 px-3 bg-slate-50 dark:bg-slate-800 rounded-lg text-center font-mono font-bold text-slate-900 dark:text-slate-100 border border-slate-200 dark:border-slate-700">
              Mid: $65,248.75 | Spread: ${spread}
            </div>

            {/* Bids (Green, bottom) */}
            <div className="space-y-1">
              {bids.map((b, i) => (
                <div key={i} className="flex justify-between font-mono text-[11px] text-emerald-600 dark:text-emerald-400">
                  <span>${b.price}</span>
                  <span className="text-slate-600 dark:text-slate-400">{b.size}</span>
                  <span className="text-slate-400">{b.total}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
