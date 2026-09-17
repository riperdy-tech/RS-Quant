import { WebSocketServer, WebSocket } from 'ws';
import express from 'express';
import cors from 'cors';
import http from 'http';

const app = express();
app.use(cors());

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

// AI & Account State
const BASE_CAPITAL = 10000.00;
const TAKER_FEE_RATE = 0.0005; // 0.05% Bitget Taker Fee
let totalPnl = 0;
let currentCapital = BASE_CAPITAL;
let peakCapital = BASE_CAPITAL;
let maxDrawdownPct = 0;
let tradesCount = 0;
let winningTrades = 0;
let tradePnls = []; // For Sharpe Ratio calculation

let clients = [];

wss.on('connection', (ws) => {
  console.log('React UI Connected to Python/Node Backend Engine');
  clients.push(ws);
  ws.on('close', () => {
    clients = clients.filter(c => c !== ws);
  });
});

function broadcast(data) {
  clients.forEach(client => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(JSON.stringify(data));
    }
  });
}

// Helper for Sharpe Ratio
function calculateSharpe(returns) {
  if (returns.length < 2) return 0.00;
  const mean = returns.reduce((a, b) => a + b) / returns.length;
  const variance = returns.reduce((a, b) => a + Math.pow(b - mean, 2), 0) / (returns.length - 1);
  const stdDev = Math.sqrt(variance);
  if (stdDev === 0) return 0.00;
  // Annualized assuming ~1000 trades per day, approx sqrt(252000) for high frequency, but we'll use a standard proxy
  return (mean / stdDev) * Math.sqrt(100); 
}

console.log('Connecting to Global Crypto Order Books...');
const binanceWs = new WebSocket('wss://stream.binance.com:9443/ws/btcusdt@trade/ethusdt@trade');

binanceWs.on('message', (data) => {
  const trade = JSON.parse(data);
  if (trade.e !== 'trade') return;

  if (Math.random() > 0.05) return; // Throttle for UI

  const symbol = trade.s === 'BTCUSDT' ? 'BTC-USD' : 'ETH-USD';
  const price = parseFloat(trade.p);
  const size = parseFloat(trade.q);
  
  const isBuy = trade.m ? false : true; 
  
  // 1. Calculate Exchange Fees (0.05% of notion value)
  const notionalValue = price * size;
  const fee = notionalValue * TAKER_FEE_RATE;

  // 2. Simulated Gross PnL from the strategy closing out a position
  const grossPnl = (Math.random() * (notionalValue * 0.002)) - (notionalValue * 0.0008); 
  
  // 3. Net PnL = Gross - Fees
  const netTradePnl = grossPnl - fee;
  
  // Update Account Balances
  totalPnl += netTradePnl;
  currentCapital = BASE_CAPITAL + totalPnl;
  
  // Drawdown Tracking
  if (currentCapital > peakCapital) {
    peakCapital = currentCapital;
  }
  const currentDrawdown = ((peakCapital - currentCapital) / peakCapital) * 100;
  if (currentDrawdown > maxDrawdownPct) {
    maxDrawdownPct = currentDrawdown;
  }

  // Win Rate Tracking
  tradesCount++;
  if (netTradePnl > 0) winningTrades++;
  const winRate = (winningTrades / tradesCount) * 100;

  // Sharpe Ratio
  // Convert PnL to percentage return on capital for this trade
  const pctReturn = (netTradePnl / BASE_CAPITAL) * 100;
  tradePnls.push(pctReturn);
  if (tradePnls.length > 500) tradePnls.shift(); // Keep last 500
  const sharpe = calculateSharpe(tradePnls);

  const now = new Date();
  const ms = now.getMilliseconds().toString().padStart(3, '0');
  const timeStr = `${now.getHours()}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}.${ms}`;

  const fillEvent = {
    type: 'FILL',
    id: trade.t.toString(),
    time: timeStr,
    chartTime: `${now.getHours()}:${now.getMinutes().toString().padStart(2, '0')}:${now.getSeconds().toString().padStart(2, '0')}`,
    symbol: symbol,
    side: isBuy ? 'BUY' : 'SELL',
    size: size.toFixed(4),
    price: price.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}),
    rawPrice: price,
    pnl: netTradePnl,
    fee: fee,
    totalPnl: totalPnl,
    metrics: {
      capital: currentCapital,
      maxDrawdown: maxDrawdownPct,
      winRate: winRate,
      sharpe: sharpe,
      totalTrades: tradesCount
    }
  };

  broadcast(fillEvent);
});

server.listen(8000, () => {
  console.log('Backend Trading Engine listening on http://localhost:8000');
});
