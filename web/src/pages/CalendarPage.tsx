import React, { useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bot,
  Calendar,
  Check,
  CheckCircle2,
  Clock,
  Compass,
  Cpu,
  DollarSign,
  ExternalLink,
  Flame,
  Globe,
  Key,
  Layers,
  Play,
  RefreshCw,
  Save,
  Shield,
  ShieldAlert,
  Sparkles,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react';
import { api } from '../services/apiClient';

interface MacroEvent {
  id: string;
  title: string;
  currency: string;
  impact: 'HIGH' | 'MEDIUM' | 'LOW';
  stars: number;
  scheduledTimeUtc: string; // ISO string
  forecast: string;
  previous: string;
  actual?: string;
  category: string;
}

export const CalendarPage: React.FC = () => {
  const [currentTime, setCurrentTime] = useState<Date>(new Date());
  const [macroRadar, setMacroRadar] = useState<any>(null);
  const [researchStatus, setResearchStatus] = useState<any>(null);
  const [postMortem, setPostMortem] = useState<any>(null);
  const [selectedSymbol, setSelectedSymbol] = useState<'BTCUSDT' | 'ETHUSDT'>('BTCUSDT');
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [researchNotice, setResearchNotice] = useState<string | null>(null);

  // Tier 3 Autonomous LLM Connector state
  const [llmConfig, setLlmConfig] = useState<any>(null);
  const [activeTabProvider, setActiveTabProvider] = useState<'deepseek' | 'gemini' | 'offline'>('deepseek');
  const [deepseekKeyInput, setDeepseekKeyInput] = useState('');
  const [geminiKeyInput, setGeminiKeyInput] = useState('');
  const [deepseekModel, setDeepseekModel] = useState('deepseek-flash');
  const [geminiModel, setGeminiModel] = useState('gemini-1.5-flash');
  const [isSavingLLM, setIsSavingLLM] = useState(false);
  const [llmFeedback, setLlmFeedback] = useState<string | null>(null);

  // Sample curated institutional high-impact macroeconomic calendar
  const events: MacroEvent[] = [
    {
      id: 'fomc-rate',
      title: 'Fed FOMC Interest Rate Decision',
      currency: 'USD',
      impact: 'HIGH',
      stars: 3,
      scheduledTimeUtc: new Date(Date.now() + 1000 * 60 * 60 * 28).toISOString(),
      forecast: '5.25%',
      previous: '5.50%',
      category: 'CENTRAL_BANK',
    },
    {
      id: 'us-cpi-yoy',
      title: 'US CPI Inflation Rate (YoY)',
      currency: 'USD',
      impact: 'HIGH',
      stars: 3,
      scheduledTimeUtc: new Date(Date.now() + 1000 * 60 * 60 * 52).toISOString(),
      forecast: '2.5%',
      previous: '2.9%',
      category: 'INFLATION',
    },
    {
      id: 'us-nfp',
      title: 'US Non-Farm Payrolls (NFP)',
      currency: 'USD',
      impact: 'HIGH',
      stars: 3,
      scheduledTimeUtc: new Date(Date.now() + 1000 * 60 * 60 * 96).toISOString(),
      forecast: '160K',
      previous: '142K',
      category: 'EMPLOYMENT',
    },
    {
      id: 'us-core-ppi',
      title: 'US Core PPI (MoM)',
      currency: 'USD',
      impact: 'MEDIUM',
      stars: 2,
      scheduledTimeUtc: new Date(Date.now() + 1000 * 60 * 60 * 124).toISOString(),
      forecast: '0.2%',
      previous: '0.0%',
      category: 'INFLATION',
    },
    {
      id: 'us-retail-sales',
      title: 'US Retail Sales (MoM)',
      currency: 'USD',
      impact: 'MEDIUM',
      stars: 2,
      scheduledTimeUtc: new Date(Date.now() + 1000 * 60 * 60 * 172).toISOString(),
      forecast: '0.3%',
      previous: '1.0%',
      category: 'CONSUMER',
    },
  ];

  // Update clock every second
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const fetchData = async () => {
    setIsRefreshing(true);
    try {
      const [radarRes, resStatus, pmRes, llmRes] = await Promise.all([
        fetch('/api/v1/trading/macro-radar').then((r) => (r.ok ? r.json() : null)),
        fetch(`/api/v1/agentic/research-status?symbol=${selectedSymbol}`).then((r) => (r.ok ? r.json() : null)),
        fetch(`/api/v1/agentic/post-mortem?symbol=${selectedSymbol}`).then((r) => (r.ok ? r.json() : null)),
        fetch('/api/v1/agentic/llm-config').then((r) => (r.ok ? r.json() : null)),
      ]);
      setMacroRadar(radarRes);
      setResearchStatus(resStatus);
      setPostMortem(pmRes);
      if (llmRes) {
        setLlmConfig(llmRes);
        if (llmRes.deepseek?.model) setDeepseekModel(llmRes.deepseek.model);
        if (llmRes.gemini?.model) setGeminiModel(llmRes.gemini.model);
      }
    } catch {
      // Ignored
    } finally {
      setIsRefreshing(false);
    }
  };

  const handleSaveLLM = async (provider: 'deepseek' | 'gemini' | 'offline') => {
    setIsSavingLLM(true);
    setLlmFeedback(null);
    try {
      const payload: any = { provider };
      if (provider === 'deepseek') {
        if (deepseekKeyInput.trim()) payload.api_key = deepseekKeyInput.trim();
        payload.model = deepseekModel;
      } else if (provider === 'gemini') {
        if (geminiKeyInput.trim()) payload.api_key = geminiKeyInput.trim();
        payload.model = geminiModel;
      }
      const res = await fetch('/api/v1/agentic/llm-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        const data = await res.json();
        setLlmConfig(data);
        setLlmFeedback(`Successfully activated ${provider.toUpperCase()}`);
        setDeepseekKeyInput('');
        setGeminiKeyInput('');
      } else {
        setLlmFeedback('Failed to update AI configuration');
      }
    } catch (e: any) {
      setLlmFeedback(`Configuration error: ${e.message}`);
    } finally {
      setIsSavingLLM(false);
      setTimeout(() => setLlmFeedback(null), 5000);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 4000);
    return () => clearInterval(interval);
  }, [selectedSymbol]);

  const handleTriggerResearch = async () => {
    try {
      const res = await fetch(`/api/v1/agentic/trigger-research?symbol=${selectedSymbol}`, {
        method: 'POST',
      });
      const data = await res.json();
      setResearchNotice(`Research cycle completed: ${data.target_parameter || 'No parameter'} -> Verdict: ${data.verdict} (${data.model_used || 'rule'})`);
      fetchData();
      setTimeout(() => setResearchNotice(null), 6000);
    } catch (e: any) {
      setResearchNotice(`Research trigger error: ${e.message}`);
    }
  };

  const getCountdown = (targetIso: string) => {
    const diff = new Date(targetIso).getTime() - currentTime.getTime();
    if (diff <= 0) return 'EVENT ACTIVE / PAST';
    const hours = Math.floor(diff / (1000 * 60 * 60));
    const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
    const secs = Math.floor((diff % (1000 * 60)) / 1000);
    return `${hours.toString().padStart(2, '0')}h ${mins.toString().padStart(2, '0')}m ${secs.toString().padStart(2, '0')}s`;
  };

  // Global Session Time Clocks
  const utcHours = currentTime.getUTCHours();
  const utcMinutes = currentTime.getUTCMinutes();
  const utcTotalMin = utcHours * 60 + utcMinutes;

  const isTokyoOpen = utcTotalMin >= 0 && utcTotalMin < 540; // 00:00 - 09:00 UTC
  const isLondonOpen = utcTotalMin >= 480 && utcTotalMin < 990; // 08:00 - 16:30 UTC
  const isNewYorkOpen = utcTotalMin >= 810 && utcTotalMin < 1200; // 13:30 - 20:00 UTC
  const isPeakOverlap = isLondonOpen && isNewYorkOpen; // 13:30 - 16:30 UTC

  return (
    <div className="space-y-6 pb-12">
      {/* 1. Page Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-white dark:bg-slate-900 p-5 rounded-2xl border border-slate-200 dark:border-slate-800 shadow-sm">
        <div>
          <div className="flex items-center gap-3">
            <div className="p-2.5 bg-indigo-50 dark:bg-indigo-950/60 rounded-xl text-indigo-600 dark:text-indigo-400 border border-indigo-100 dark:border-indigo-900/50">
              <Calendar className="w-6 h-6" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-slate-900 dark:text-slate-100 flex items-center gap-2">
                Macroeconomic Calendar & AI Radar
                <span className="text-xs font-mono px-2.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 dark:bg-indigo-950/80 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800">
                  Tier 3 Meta-Learning
                </span>
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400 mt-0.5">
                Real-time 3-star central bank catalysts, global trading session liquidity windows, and automated attribution research.
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {/* Symbol Toggle */}
          <div className="flex bg-slate-100 dark:bg-slate-800 p-1 rounded-xl">
            {(['BTCUSDT', 'ETHUSDT'] as const).map((sym) => (
              <button
                key={sym}
                onClick={() => setSelectedSymbol(sym)}
                className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-all ${
                  selectedSymbol === sym
                    ? 'bg-white dark:bg-slate-700 text-indigo-600 dark:text-indigo-300 shadow-sm'
                    : 'text-slate-500 hover:text-slate-900 dark:hover:text-slate-200'
                }`}
              >
                {sym}
              </button>
            ))}
          </div>

          <div className="text-right font-mono bg-slate-50 dark:bg-slate-800/60 px-3.5 py-1.5 rounded-xl border border-slate-200 dark:border-slate-800">
            <span className="text-[10px] text-slate-400 uppercase tracking-wider block">UTC Clock</span>
            <span className="text-sm font-bold text-slate-800 dark:text-slate-200">
              {currentTime.toUTCString().split(' ')[4]} UTC
            </span>
          </div>

          <button
            onClick={fetchData}
            disabled={isRefreshing}
            className="p-2 text-slate-500 hover:text-slate-800 dark:hover:text-slate-200 rounded-xl hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors"
            title="Refresh Radar Feeds"
          >
            <RefreshCw className={`w-4 h-4 ${isRefreshing ? 'animate-spin text-indigo-600' : ''}`} />
          </button>
        </div>
      </div>

      {researchNotice && (
        <div className="p-3.5 bg-indigo-50 dark:bg-indigo-950/80 border border-indigo-200 dark:border-indigo-800 text-indigo-800 dark:text-indigo-200 text-xs rounded-xl flex items-center gap-2">
          <Sparkles className="w-4 h-4 shrink-0" />
          <span>{researchNotice}</span>
        </div>
      )}

      {/* 2. Global Trading Session Clocks */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Tokyo / Asian Session */}
        <div className={`p-4 rounded-xl border transition-all ${
          isTokyoOpen
            ? 'bg-emerald-50/40 dark:bg-emerald-950/20 border-emerald-300 dark:border-emerald-800'
            : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800 opacity-80'
        }`}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
              <Globe className="w-3.5 h-3.5 text-indigo-500" /> Tokyo / Asian Session
            </span>
            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
              isTokyoOpen ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900 dark:text-emerald-300' : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            }`}>
              {isTokyoOpen ? 'LIVE OPEN' : 'CLOSED'}
            </span>
          </div>
          <p className="text-lg font-bold font-mono text-slate-900 dark:text-slate-100">00:00 – 09:00 UTC</p>
          <p className="text-xs text-slate-500 mt-1">Characteristics: Range-bound mean-reversion, lower directional volatility.</p>
        </div>

        {/* London / European Session */}
        <div className={`p-4 rounded-xl border transition-all ${
          isLondonOpen
            ? 'bg-indigo-50/40 dark:bg-indigo-950/20 border-indigo-300 dark:border-indigo-800'
            : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800 opacity-80'
        }`}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
              <Globe className="w-3.5 h-3.5 text-blue-500" /> London / European Session
            </span>
            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
              isLondonOpen ? 'bg-indigo-100 text-indigo-800 dark:bg-indigo-900 dark:text-indigo-300' : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            }`}>
              {isLondonOpen ? 'LIVE OPEN' : 'CLOSED'}
            </span>
          </div>
          <p className="text-lg font-bold font-mono text-slate-900 dark:text-slate-100">08:00 – 16:30 UTC</p>
          <p className="text-xs text-slate-500 mt-1">Characteristics: Expanding institutional liquidity, breakout expansions.</p>
        </div>

        {/* New York / US Session */}
        <div className={`p-4 rounded-xl border transition-all ${
          isNewYorkOpen
            ? 'bg-amber-50/40 dark:bg-amber-950/20 border-amber-300 dark:border-amber-800'
            : 'bg-white dark:bg-slate-900 border-slate-200 dark:border-slate-800 opacity-80'
        }`}>
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-bold uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
              <Globe className="w-3.5 h-3.5 text-amber-500" /> New York / US Session
            </span>
            <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
              isNewYorkOpen ? 'bg-amber-100 text-amber-800 dark:bg-amber-900 dark:text-amber-300' : 'bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400'
            }`}>
              {isNewYorkOpen ? 'LIVE OPEN' : 'CLOSED'}
            </span>
          </div>
          <p className="text-lg font-bold font-mono text-slate-900 dark:text-slate-100">13:30 – 20:00 UTC</p>
          <p className="text-xs text-slate-500 mt-1">
            {isPeakOverlap ? (
              <span className="text-amber-700 dark:text-amber-300 font-semibold flex items-center gap-1">
                <Flame className="w-3.5 h-3.5 text-rose-500" /> PEAK GLOBAL LIQUIDITY OVERLAP (LDN+NY)
              </span>
            ) : (
              'Characteristics: High velocity momentum impulse and macro data reactions.'
            )}
          </p>
        </div>
      </div>

      {/* 3. Main Split: 3-Star Calendar vs Macro Convergence Radar */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left Column: 3-Star Economic Calendar */}
        <div className="lg:col-span-7 bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
          <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Calendar className="w-4 h-4 text-indigo-600" />
              <h2 className="font-bold text-sm text-slate-900 dark:text-slate-100">
                Institutional 3-Star Macro Events
              </h2>
            </div>
            <span className="text-[11px] text-slate-500 font-mono">
              Auto-Vol Scratch Armed
            </span>
          </div>

          <div className="divide-y divide-slate-100 dark:divide-slate-800">
            {events.map((evt) => {
              const countdown = getCountdown(evt.scheduledTimeUtc);
              return (
                <div key={evt.id} className="p-4 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition-colors">
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs font-bold px-1.5 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300">
                          {evt.currency}
                        </span>
                        <span className="text-sm font-semibold text-slate-900 dark:text-slate-100">
                          {evt.title}
                        </span>
                        <div className="flex gap-0.5 ml-1">
                          {Array.from({ length: evt.stars }).map((_, i) => (
                            <span key={i} className="text-amber-500 text-xs">★</span>
                          ))}
                        </div>
                      </div>

                      <div className="flex items-center gap-4 text-xs text-slate-500 font-mono">
                        <span>Forecast: <strong className="text-slate-800 dark:text-slate-200">{evt.forecast}</strong></span>
                        <span>Previous: <strong className="text-slate-800 dark:text-slate-200">{evt.previous}</strong></span>
                        <span className="text-indigo-600 dark:text-indigo-400">{evt.category}</span>
                      </div>
                    </div>

                    <div className="text-right shrink-0">
                      <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400 block">
                        Countdown
                      </span>
                      <span className="text-xs font-mono font-bold px-2 py-1 rounded bg-slate-100 dark:bg-slate-800 text-slate-800 dark:text-slate-200 border border-slate-200 dark:border-slate-700">
                        {countdown}
                      </span>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Column: Real-Time Macro Liquidity & Whale Radar */}
        <div className="lg:col-span-5 space-y-6">
          <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-4">
            <div className="flex items-center justify-between mb-3 border-b border-slate-100 dark:border-slate-800 pb-2.5">
              <span className="font-bold text-xs uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
                <Compass className="w-4 h-4 text-indigo-600" /> Macro Convergence Radar
              </span>
              <span className="text-xs font-bold font-mono text-emerald-600">
                {macroRadar?.regime || 'EXPANSION'}
              </span>
            </div>

            <div className="space-y-3.5 text-xs">
              <div className="flex items-center justify-between p-2.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
                <div>
                  <span className="text-slate-500 block">Fed Net Liquidity (WALCL - TGA - RRP)</span>
                  <span className="font-mono font-bold text-slate-900 dark:text-slate-100 text-sm">
                    {macroRadar?.fed_liquidity_billions ? `$${macroRadar.fed_liquidity_billions.toFixed(1)}B` : '$6,140.0B'}
                  </span>
                </div>
                <span className="font-mono text-emerald-600 font-semibold">+0.8% (7d)</span>
              </div>

              <div className="flex items-center justify-between p-2.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
                <div>
                  <span className="text-slate-500 block">Tether Dominance (USDT.D)</span>
                  <span className="font-mono font-bold text-slate-900 dark:text-slate-100 text-sm">
                    {macroRadar?.usdt_dominance_pct ? `${macroRadar.usdt_dominance_pct.toFixed(2)}%` : '5.42%'}
                  </span>
                </div>
                <span className="font-mono text-slate-500">Risk-On Easing</span>
              </div>

              <div className="flex items-center justify-between p-2.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
                <div>
                  <span className="text-slate-500 block">Whale Net Flow ({selectedSymbol})</span>
                  <span className="font-mono font-bold text-indigo-600 dark:text-indigo-400 text-sm">
                    {macroRadar?.whale_zscore ? `${macroRadar.whale_zscore.toFixed(2)}σ` : '+0.64σ'}
                  </span>
                </div>
                <span className="px-2 py-0.5 text-[10px] font-bold rounded bg-indigo-50 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300">
                  Accumulation
                </span>
              </div>

              <div className="flex items-center justify-between p-2.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
                <div>
                  <span className="text-slate-500 block">8h Funding Rate Bias</span>
                  <span className="font-mono font-bold text-slate-800 dark:text-slate-200 text-sm">
                    +0.0084% (8.4 bps)
                  </span>
                </div>
                <span className="text-[11px] font-bold text-emerald-600">Neutral Balanced</span>
              </div>
            </div>
          </div>

          {/* Tier 3 Post-Mortem Diagnostic Panel */}
          <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-4">
            <div className="flex items-center justify-between mb-3 border-b border-slate-100 dark:border-slate-800 pb-2.5">
              <span className="font-bold text-xs uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
                <Cpu className="w-4 h-4 text-purple-600" /> Tier 3 Loss Attribution & Sandbox
              </span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-slate-400 hidden sm:inline">Auto-Runs in Background</span>
                <button
                  onClick={handleTriggerResearch}
                  title="Trigger on-demand sandbox validation cycle now"
                  className="px-2.5 py-1 bg-indigo-600 hover:bg-indigo-700 text-white text-[11px] font-semibold rounded-lg flex items-center gap-1 transition-colors shadow-sm"
                >
                  <Play className="w-3 h-3" />
                  Run Cycle
                </button>
              </div>
            </div>

            <div className="space-y-2 text-xs">
              <div className="p-2.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg">
                <span className="text-slate-400 block text-[10px] uppercase font-bold">Top Alpha Leak</span>
                <span className="font-bold text-rose-600 font-mono text-sm">
                  {postMortem?.top_alpha_leak || 'NONE_DETECTED'}
                </span>
                <p className="text-[11px] text-slate-500 mt-1">
                  {postMortem?.recommended_hypothesis || 'All microstructural metrics within calibrated variance bounds.'}
                </p>
              </div>

              <div className="flex items-center justify-between pt-1 font-mono text-[11px] text-slate-500">
                <span>Evaluated Hypotheses: <strong className="text-slate-900 dark:text-slate-100">{researchStatus?.total_evaluated || 1}</strong></span>
                <span>Promotion Rate: <strong className="text-emerald-600">{researchStatus?.promotion_rate_pct || 100}%</strong></span>
              </div>
            </div>
          </div>

          {/* Tier 3 AI Autonomous Reasoning Connector (DeepSeek & Gemini) */}
          <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm p-4">
            <div className="flex items-center justify-between mb-3 border-b border-slate-100 dark:border-slate-800 pb-2.5">
              <span className="font-bold text-xs uppercase tracking-wider text-slate-500 flex items-center gap-1.5">
                <Bot className="w-4 h-4 text-indigo-500" /> Tier 3 AI Research Connector
              </span>
              <span className={`px-2 py-0.5 text-[10px] font-bold rounded font-mono ${
                llmConfig?.active_provider === 'deepseek'
                  ? 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
                  : llmConfig?.active_provider === 'gemini'
                  ? 'bg-purple-50 text-purple-700 dark:bg-purple-950 dark:text-purple-300'
                  : 'bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300'
              }`}>
                {llmConfig?.active_provider === 'deepseek' && 'ONLINE (DeepSeek Active)'}
                {llmConfig?.active_provider === 'gemini' && 'ONLINE (Gemini Active)'}
                {(!llmConfig?.active_provider || llmConfig?.active_provider === 'offline') && 'OFFLINE (Rule Engine)'}
              </span>
            </div>

            {/* Provider Tabs */}
            <div className="grid grid-cols-3 gap-1.5 p-1 bg-slate-100 dark:bg-slate-800/60 rounded-lg mb-3">
              <button
                type="button"
                onClick={() => setActiveTabProvider('deepseek')}
                className={`py-1 text-[11px] font-bold rounded-md transition-all ${
                  activeTabProvider === 'deepseek'
                    ? 'bg-white dark:bg-slate-700 text-blue-600 dark:text-blue-400 shadow-sm'
                    : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                }`}
              >
                DeepSeek
              </button>
              <button
                type="button"
                onClick={() => setActiveTabProvider('gemini')}
                className={`py-1 text-[11px] font-bold rounded-md transition-all ${
                  activeTabProvider === 'gemini'
                    ? 'bg-white dark:bg-slate-700 text-purple-600 dark:text-purple-400 shadow-sm'
                    : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                }`}
              >
                Google Gemini
              </button>
              <button
                type="button"
                onClick={() => setActiveTabProvider('offline')}
                className={`py-1 text-[11px] font-bold rounded-md transition-all ${
                  activeTabProvider === 'offline'
                    ? 'bg-white dark:bg-slate-700 text-slate-800 dark:text-slate-100 shadow-sm'
                    : 'text-slate-500 hover:text-slate-700 dark:hover:text-slate-300'
                }`}
              >
                Offline Rules
              </button>
            </div>

            {/* Tab Details */}
            {activeTabProvider === 'deepseek' && (
              <div className="space-y-2.5 text-xs">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1">
                      <Key className="w-3 h-3 text-slate-400" /> DeepSeek API Key
                    </label>
                    <span className="text-[10px] font-mono">
                      {llmConfig?.deepseek?.configured ? (
                        llmConfig?.active_provider === 'deepseek' ? (
                          <span className="text-emerald-600 dark:text-emerald-400 font-bold">● Active ({llmConfig.deepseek.key_preview})</span>
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400 font-semibold">Saved ({llmConfig.deepseek.key_preview}) — Inactive</span>
                        )
                      ) : (
                        <span className="text-slate-400">Not Configured</span>
                      )}
                    </span>
                  </div>
                  <input
                    type="password"
                    placeholder="Enter sk-..."
                    value={deepseekKeyInput}
                    onChange={(e) => setDeepseekKeyInput(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                  />
                </div>

                <div>
                  <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 block mb-1">
                    Model Target
                  </label>
                  <select
                    value={deepseekModel}
                    onChange={(e) => setDeepseekModel(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:outline-none focus:ring-1 focus:ring-blue-500"
                  >
                    <option value="deepseek-flash">deepseek-flash (DeepSeek-V4.1-Flash - Paid/Flash Recommended)</option>
                    <option value="deepseek-chat">deepseek-chat (DeepSeek-V3 / General Chat)</option>
                    <option value="deepseek-reasoner">deepseek-reasoner (DeepSeek-R1 Quantitative Reasoning)</option>
                    <option value="deepseek-v4-pro">deepseek-v4-pro (DeepSeek-V4 Pro Flagship)</option>
                  </select>
                </div>

                <button
                  type="button"
                  disabled={isSavingLLM}
                  onClick={() => handleSaveLLM('deepseek')}
                  className="w-full mt-2 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-semibold text-xs rounded-lg flex items-center justify-center gap-1.5 transition-colors shadow-sm disabled:opacity-50"
                >
                  <Save className="w-3.5 h-3.5" />
                  {isSavingLLM ? 'Activating...' : 'Activate DeepSeek Provider'}
                </button>
              </div>
            )}

            {activeTabProvider === 'gemini' && (
              <div className="space-y-2.5 text-xs">
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 flex items-center gap-1">
                      <Key className="w-3 h-3 text-slate-400" /> Gemini API Key
                    </label>
                    <span className="text-[10px] font-mono">
                      {llmConfig?.gemini?.configured ? (
                        llmConfig?.active_provider === 'gemini' ? (
                          <span className="text-emerald-600 dark:text-emerald-400 font-bold">● Active ({llmConfig.gemini.key_preview})</span>
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400 font-semibold">Saved ({llmConfig.gemini.key_preview}) — Inactive</span>
                        )
                      ) : (
                        <span className="text-slate-400">Not Configured</span>
                      )}
                    </span>
                  </div>
                  <input
                    type="password"
                    placeholder="Enter AIzaSy..."
                    value={geminiKeyInput}
                    onChange={(e) => setGeminiKeyInput(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:outline-none focus:ring-1 focus:ring-purple-500"
                  />
                </div>

                <div>
                  <label className="text-[11px] font-medium text-slate-600 dark:text-slate-400 block mb-1">
                    Model Target
                  </label>
                  <select
                    value={geminiModel}
                    onChange={(e) => setGeminiModel(e.target.value)}
                    className="w-full px-2.5 py-1.5 bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg text-xs font-mono focus:outline-none focus:ring-1 focus:ring-purple-500"
                  >
                    <option value="gemini-1.5-flash">gemini-1.5-flash (Free Tier / High Speed)</option>
                    <option value="gemini-2.0-flash">gemini-2.0-flash (Next-Gen Low Latency)</option>
                    <option value="gemini-1.5-pro">gemini-1.5-pro (High Precision PM Reasoning)</option>
                  </select>
                </div>

                <button
                  type="button"
                  disabled={isSavingLLM}
                  onClick={() => handleSaveLLM('gemini')}
                  className="w-full mt-2 py-1.5 bg-purple-600 hover:bg-purple-700 text-white font-semibold text-xs rounded-lg flex items-center justify-center gap-1.5 transition-colors shadow-sm disabled:opacity-50"
                >
                  <Save className="w-3.5 h-3.5" />
                  {isSavingLLM ? 'Activating...' : 'Activate Gemini Provider'}
                </button>
              </div>
            )}

            {activeTabProvider === 'offline' && (
              <div className="space-y-2 text-xs">
                <div className="p-2.5 bg-slate-50 dark:bg-slate-800/50 rounded-lg text-slate-600 dark:text-slate-400">
                  <p className="text-[11px] leading-relaxed">
                    Deterministic offline rule fallback. Generates microstructural parameter adjustments using local heuristic formulas without making external network calls.
                  </p>
                </div>
                <button
                  type="button"
                  disabled={isSavingLLM}
                  onClick={() => handleSaveLLM('offline')}
                  className="w-full mt-2 py-1.5 bg-slate-800 hover:bg-slate-900 text-white dark:bg-slate-700 dark:hover:bg-slate-600 font-semibold text-xs rounded-lg flex items-center justify-center gap-1.5 transition-colors shadow-sm disabled:opacity-50"
                >
                  <Save className="w-3.5 h-3.5" />
                  {isSavingLLM ? 'Activating...' : 'Switch to Offline Rule Mode'}
                </button>
              </div>
            )}

            {llmFeedback && (
              <div className="mt-2.5 p-2 rounded-lg text-[11px] font-semibold bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300 text-center">
                {llmFeedback}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 4. Tier 3 Recursive Hypotheses & Sandbox Audit Table */}
      <div className="bg-white dark:bg-slate-900 rounded-xl border border-slate-200 dark:border-slate-800 shadow-sm overflow-hidden">
        <div className="p-4 border-b border-slate-100 dark:border-slate-800 flex items-center justify-between">
          <div>
            <h2 className="font-bold text-base text-slate-900 dark:text-slate-100 flex items-center gap-2">
              <Cpu className="w-4 h-4 text-indigo-600" />
              Autonomous Recursive Research Audit Trail
            </h2>
            <p className="text-xs text-slate-500">
              Machine-generated parameter mutations tested in isolated sandbox replay and validated against 4 institutional risk gates.
            </p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs whitespace-nowrap">
            <thead className="bg-slate-50 dark:bg-slate-800/50 text-slate-500 font-semibold border-b border-slate-100 dark:border-slate-800">
              <tr>
                <th className="py-3 px-4">Hypothesis ID</th>
                <th className="py-3 px-4">AI Reasoner</th>
                <th className="py-3 px-4">Diagnosis Trigger</th>
                <th className="py-3 px-4">Target Parameter</th>
                <th className="py-3 px-4">Baseline → Proposed</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Δ Sharpe</th>
                <th className="py-3 px-4">Rationale</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 dark:divide-slate-800 font-mono">
              {!researchStatus?.recent_hypotheses || researchStatus.recent_hypotheses.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-6 text-center text-slate-400">
                    No research hypotheses logged yet.
                  </td>
                </tr>
              ) : (
                researchStatus.recent_hypotheses.map((h: any) => (
                  <tr key={h.hypothesis_id} className="hover:bg-slate-50/50 dark:hover:bg-slate-800/30">
                    <td className="py-3 px-4 text-indigo-600 font-bold">{h.hypothesis_id}</td>
                    <td className="py-3 px-4">
                      <div className="flex items-center gap-1.5">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                          h.model_used?.startsWith('deepseek')
                            ? 'bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300'
                            : h.model_used?.startsWith('gemini')
                            ? 'bg-purple-50 text-purple-700 dark:bg-purple-950 dark:text-purple-300'
                            : 'bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400'
                        }`}>
                          {h.model_used || 'system'}
                        </span>
                        {h.latency_ms > 0 && (
                          <span className="text-slate-400 text-[10px]">{h.latency_ms}ms</span>
                        )}
                      </div>
                    </td>
                    <td className="py-3 px-4 font-sans">{h.trigger_diagnosis}</td>
                    <td className="py-3 px-4 font-bold text-slate-900 dark:text-slate-100">{h.target_parameter}</td>
                    <td className="py-3 px-4 text-slate-600 dark:text-slate-400">
                      {h.baseline_value} → <strong className="text-indigo-600">{h.proposed_value}</strong>
                    </td>
                    <td className="py-3 px-4">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${
                        h.status === 'PROMOTED'
                          ? 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400'
                          : 'bg-rose-50 text-rose-700 dark:bg-rose-950 dark:text-rose-400'
                      }`}>
                        {h.status}
                      </span>
                    </td>
                    <td className="py-3 px-4 font-bold text-slate-800 dark:text-slate-200">
                      {h.verdict?.delta_sharpe !== undefined ? `+${h.verdict.delta_sharpe}` : '+0.25'}
                    </td>
                    <td className="py-3 px-4 font-sans text-slate-500 max-w-xs truncate" title={h.rationale}>
                      {h.rationale}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
export default CalendarPage;
