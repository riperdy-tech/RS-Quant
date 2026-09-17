import React, { useEffect, useState } from 'react';
import {
  Activity,
  AlertOctagon,
  ArrowDownRight,
  ArrowUpRight,
  CheckCircle2,
  DollarSign,
  Download,
  Eye,
  Layers,
  RefreshCw,
  Search,
  Shield,
  Trash2,
  TrendingDown,
  TrendingUp,
  XCircle,
} from 'lucide-react';
import { api } from '../services/apiClient';
import { OrderTraceModal } from '../components/OrderTraceModal';

export interface TradingPageProps {
  onOpenCommand: (type: string, target?: Record<string, any>) => void;
  isViewer?: boolean;
}

export const TradingPage: React.FC<TradingPageProps> = ({ onOpenCommand, isViewer = false }) => {
  const [positions, setPositions] = useState<any[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [fills, setFills] = useState<any[]>([]);
  const [balances, setBalances] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedOrderId, setSelectedOrderId] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState('');

  useEffect(() => {
    fetchTradingData();
  }, []);

  const fetchTradingData = async () => {
    setLoading(true);
    try {
      const [pos, ords, fls, bals] = await Promise.all([
        api.getPositions().catch(() => []),
        api.getOrders().catch(() => []),
        api.getFills().catch(() => []),
        api.getBalances().catch(() => []),
      ]);
      setPositions(pos);
      setOrders(ords);
      setFills(fls);
      setBalances(bals);
    } finally {
      setLoading(false);
    }
  };

  const handleCancelOrder = (orderId: string, symbol: string) => {
    onOpenCommand('CANCEL_ORDER', {
      order_id: orderId,
      symbol,
      account_id: 'paper-demo',
    });
  };

  const handleFlattenInstrument = (symbol: string) => {
    onOpenCommand('FLATTEN_POSITION', {
      instrument_id: symbol,
      account_id: 'paper-demo',
    });
  };

  const filteredFills = fills.filter((f) =>
    (f.instrument_id || f.symbol || '').toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-slate-100">
            Trading Execution Desk
          </h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mt-0.5">
            Real-time positions, active OMS orders, fill logs, and balanced double-entry accounting.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchTradingData}
            disabled={loading}
            className="p-2 border border-slate-200 dark:border-slate-800 rounded-lg text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title="Refresh trading read models"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          {!isViewer && (
            <button
              onClick={() => handleFlattenInstrument('BTCUSDT')}
              className="px-3.5 py-2 bg-rose-50 dark:bg-rose-950/50 text-rose-700 dark:text-rose-400 border border-rose-200 dark:border-rose-900 rounded-lg text-xs font-bold hover:bg-rose-100 dark:hover:bg-rose-900/50 transition-colors flex items-center gap-1.5"
            >
              <AlertOctagon className="w-4 h-4" />
              Flatten Positions
            </button>
          )}
        </div>
      </div>

      {/* Account Balances and Margin Summary Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Total Equity</span>
          <div className="text-2xl font-bold font-mono text-slate-900 dark:text-slate-100">
            ${balances[0]?.total || '10,000.00'}
          </div>
          <span className="text-xs text-slate-400">USDT Isolated Collateral</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Available Cash</span>
          <div className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">
            ${balances[0]?.available || '8,500.00'}
          </div>
          <span className="text-xs text-slate-400">Unencumbered purchasing power</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Locked Margin</span>
          <div className="text-2xl font-bold font-mono text-indigo-600 dark:text-indigo-400">
            ${balances[0]?.locked_margin || '1,500.00'}
          </div>
          <span className="text-xs text-slate-400">Initial & maintenance reservation</span>
        </div>

        <div className="bg-white dark:bg-slate-900 p-4 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm space-y-1">
          <span className="text-xs font-medium text-slate-500 uppercase tracking-wider">Unrealized PnL</span>
          <div className={`text-2xl font-bold font-mono ${
            Number(balances[0]?.unrealized_pnl || 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'
          }`}>
            {Number(balances[0]?.unrealized_pnl || 0) >= 0 ? '+' : ''}
            ${balances[0]?.unrealized_pnl || '25.00'}
          </div>
          <span className="text-xs text-slate-400">Mark-to-market valuation</span>
        </div>
      </div>

      {/* Positions Table (§15.2) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
              Open Positions
            </h2>
            <p className="text-xs text-slate-500">
              Active isolated-margin positions with real-time mark valuation and liquidation estimates.
            </p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table data-testid="positions-table" className="w-full text-left text-xs whitespace-nowrap">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-4">Instrument</th>
                <th className="py-3 px-4">Side</th>
                <th className="py-3 px-4">Lots / Size</th>
                <th className="py-3 px-4">Entry Price</th>
                <th className="py-3 px-4">Mark Price</th>
                <th className="py-3 px-4">Unrealized PnL</th>
                <th className="py-3 px-4">Realized PnL</th>
                <th className="py-3 px-4">Margin Equity</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {positions.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-slate-400">
                    No open positions held. Flat across all instruments.
                  </td>
                </tr>
              ) : (
                positions.map((pos, idx) => (
                  <tr key={idx} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                    <td className="py-3 px-4 font-bold font-mono text-slate-900 dark:text-slate-100">
                      {pos.instrument_id}
                    </td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded font-bold uppercase ${
                        pos.side === 'BUY'
                          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                          : 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400'
                      }`}>
                        {pos.side}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono font-medium">{pos.lots}</td>
                    <td className="py-3 px-4 font-mono">${pos.entry_price}</td>
                    <td className="py-3 px-4 font-mono">${pos.mark_price}</td>
                    <td className={`py-3 px-4 font-mono font-bold ${
                      Number(pos.unrealized_pnl) >= 0 ? 'text-emerald-600' : 'text-rose-600'
                    }`}>
                      {Number(pos.unrealized_pnl) >= 0 ? '+' : ''}${pos.unrealized_pnl}
                    </td>
                    <td className="py-3 px-4 font-mono text-slate-600 dark:text-slate-400">
                      ${pos.realized_pnl}
                    </td>
                    <td className="py-3 px-4 font-mono text-slate-600 dark:text-slate-400">
                      ${pos.margin_equity}
                    </td>
                    <td className="py-3 px-4 text-right">
                      {!isViewer && (
                        <button
                          onClick={() => handleFlattenInstrument(pos.instrument_id)}
                          className="px-2.5 py-1 text-[11px] font-semibold bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400 border border-rose-200 dark:border-rose-900 rounded hover:bg-rose-100 transition-colors"
                        >
                          Flatten
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Active Orders Section */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
              Active OMS Orders
            </h2>
            <p className="text-xs text-slate-500">
              Working orders currently active in the exchange order book or pending gateway ack.
            </p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs whitespace-nowrap">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-4">Order ID</th>
                <th className="py-3 px-4">Strategy</th>
                <th className="py-3 px-4">Symbol</th>
                <th className="py-3 px-4">Side</th>
                <th className="py-3 px-4">Type</th>
                <th className="py-3 px-4">Qty</th>
                <th className="py-3 px-4">Price</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {orders.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-6 text-center text-slate-400">
                    No active working orders.
                  </td>
                </tr>
              ) : (
                orders.map((ord) => (
                  <tr key={ord.order_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                    <td className="py-3 px-4 font-mono font-medium text-slate-700 dark:text-slate-300">
                      {ord.order_id}
                    </td>
                    <td className="py-3 px-4 text-slate-600 dark:text-slate-400">{ord.strategy_id}</td>
                    <td className="py-3 px-4 font-bold font-mono text-slate-900 dark:text-slate-100">{ord.instrument_id}</td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded font-bold uppercase ${
                        ord.side === 'BUY'
                          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                          : 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400'
                      }`}>
                        {ord.side}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono">{ord.order_type}</td>
                    <td className="py-3 px-4 font-mono">{ord.qty}</td>
                    <td className="py-3 px-4 font-mono">${ord.limit_price}</td>
                    <td className="py-3 px-4">
                      <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-400">
                        {ord.status}
                      </span>
                    </td>
                    <td className="py-3 px-4 text-right space-x-2">
                      <button
                        onClick={() => setSelectedOrderId(ord.order_id)}
                        className="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 font-semibold"
                      >
                        Trace
                      </button>
                      {!isViewer && ord.status !== 'FILLED' && ord.status !== 'CANCELED' && (
                        <button
                          onClick={() => handleCancelOrder(ord.order_id, ord.instrument_id)}
                          className="text-rose-600 hover:text-rose-800 dark:text-rose-400 font-semibold"
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Trade Fills Table with Audit Trace Hook (§15.2) */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100">
              Trade Execution Fills
            </h2>
            <p className="text-xs text-slate-500">
              Venue-confirmed trade executions verified against double-entry ledger postings.
            </p>
          </div>

          <div className="relative w-full sm:w-64">
            <Search className="w-4 h-4 absolute left-3 top-2.5 text-slate-400" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="Filter by symbol..."
              className="w-full pl-9 pr-3 py-1.5 text-xs rounded-lg border border-slate-300 dark:border-slate-700 bg-slate-50 dark:bg-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>
        </div>

        <div className="overflow-x-auto">
          <table data-testid="fills-table" className="w-full text-left text-xs whitespace-nowrap">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-4">Fill ID</th>
                <th className="py-3 px-4">Time</th>
                <th className="py-3 px-4">Instrument</th>
                <th className="py-3 px-4">Side</th>
                <th className="py-3 px-4">Size</th>
                <th className="py-3 px-4">Price</th>
                <th className="py-3 px-4">Fee</th>
                <th className="py-3 px-4">Role</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
              {filteredFills.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-8 text-center text-slate-400">
                    No fills executed yet.
                  </td>
                </tr>
              ) : (
                filteredFills.map((fill: any) => (
                  <tr key={fill.fill_id || fill.id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                    <td className="py-3 px-4 font-mono text-slate-500">{fill.fill_id || fill.id}</td>
                    <td className="py-3 px-4 text-slate-500">
                      {fill.timestamp_ns
                        ? new Date(fill.timestamp_ns / 1_000_000).toLocaleTimeString()
                        : fill.time || 'now'}
                    </td>
                    <td className="py-3 px-4 font-mono font-bold text-slate-900 dark:text-slate-100">
                      {fill.instrument_id || fill.symbol}
                    </td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded font-bold uppercase ${
                        fill.side === 'BUY'
                          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                          : 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400'
                      }`}>
                        {fill.side}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-mono font-medium text-slate-700 dark:text-slate-300">
                      {fill.qty || fill.size}
                    </td>
                    <td className="py-3 px-4 font-mono font-medium text-slate-700 dark:text-slate-300">
                      ${fill.price}
                    </td>
                    <td className="py-3 px-4 font-mono text-slate-500">
                      {fill.fee ? `${fill.fee} ${fill.fee_currency || 'USDT'}` : '0.00 USDT'}
                    </td>
                    <td className="py-3 px-4 font-mono text-[11px] text-slate-500">
                      {fill.liquidity || 'TAKER'}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <button
                        onClick={() => setSelectedOrderId(fill.order_id || 'ord-btc-001')}
                        className="px-2.5 py-1 text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300 bg-indigo-50 dark:bg-indigo-950/60 rounded-md transition-colors"
                      >
                        View trace
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Order Trace Modal */}
      {selectedOrderId && (
        <OrderTraceModal
          orderId={selectedOrderId}
          onClose={() => setSelectedOrderId(null)}
        />
      )}
    </div>
  );
};
