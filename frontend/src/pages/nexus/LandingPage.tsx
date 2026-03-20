import { useEffect, useState } from 'react';
import {
    Activity,
    ArrowRight,
    BarChart3,
    Brain,
    CandlestickChart,
    CheckCircle2,
    CircleDollarSign,
    Flame,
    Gauge,
    Globe,
    Link2,
    LogIn,
    ShieldCheck,
    Sparkles,
    TrendingUp,
    UserPlus,
    Users,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import '../../styles/nexus/NeoTheme.css';
import NexusAssetImage from '../../components/nexus/NexusAssetImage';
import { usePortalLogo } from '../../hooks/usePortalLogo';

interface TickerSymbol {
    symbol: string;
    price: number;
    change: number;
}

interface LandingData {
    ticker:      TickerSymbol[];
    pulse_tiles: { label: string; value: string; change: string }[];
    heatmap:     { name: string; value: number }[];
    movers:      { name: string; change: number }[];
    top_traded:  { name: string; volume: number }[];
}

interface InsightCard {
    title: string;
    desc: string;
    icon: React.ReactNode;
    accent: string;
}

// Fallback static data shown while API loads or when market is closed/unavailable
const FALLBACK_TICKER: TickerSymbol[] = [
    { symbol: 'NIFTY 50',  price: 22145.4, change:  0.34 },
    { symbol: 'BANKNIFTY', price: 48210.2, change: -0.42 },
    { symbol: 'SENSEX',    price: 73210.7, change:  0.21 },
    { symbol: 'FINNIFTY',  price: 20560.1, change:  0.62 },
    { symbol: 'RELIANCE',  price: 2845.4,  change:  1.18 },
    { symbol: 'HDFCBANK',  price: 1562.8,  change: -0.81 },
    { symbol: 'INFY',      price: 1620.9,  change:  0.93 },
    { symbol: 'TCS',       price: 3980.4,  change:  0.49 },
];

// Heatmap layout sizes are fixed — only values come from the API
const HEATMAP_SIZES: Record<string, string> = {
    BANKS:  'col-span-3 row-span-2',
    IT:     'col-span-2 row-span-2',
    AUTO:   'col-span-2 row-span-1',
    FMCG:   'col-span-2 row-span-1',
    ENERGY: 'col-span-3 row-span-1',
    METAL:  'col-span-2 row-span-1',
    PHARMA: 'col-span-2 row-span-1',
};

const pulseCandles = [40, 58, 52, 76, 68, 92, 74, 105, 96, 122, 114, 134];

const pulseSignals = [
    { label: 'Momentum', value: 'Bullish', tone: 'positive' },
    { label: 'Volume', value: 'Above Avg', tone: 'positive' },
    { label: 'Risk', value: 'Defined', tone: 'neutral' },
];

const insightCards: InsightCard[] = [
    {
        title: 'Heatmap Intelligence',
        desc: 'Sector-wise momentum map with instant bullish and bearish clusters.',
        icon: <Flame className="h-7 w-7" />,
        accent: 'var(--neo-color-orange)',
    },
    {
        title: 'Market Moves',
        desc: 'Track sharp intraday movers and institutional rotation signals.',
        icon: <TrendingUp className="h-7 w-7" />,
        accent: 'var(--neo-color-green)',
    },
    {
        title: 'Top Traded Stocks',
        desc: 'High-participation names with liquidity and opportunity overlays.',
        icon: <BarChart3 className="h-7 w-7" />,
        accent: 'var(--neo-color-blue)',
    },
    {
        title: 'Live Option Lens',
        desc: 'Monitor key strikes, OI pressure zones, and expiry shifts.',
        icon: <CircleDollarSign className="h-7 w-7" />,
        accent: 'var(--neo-color-purple)',
    },
    {
        title: 'AI Trade Insights',
        desc: 'Signal confidence view with context-aware risk framing.',
        icon: <Brain className="h-7 w-7" />,
        accent: 'var(--neo-color-pink)',
    },
    {
        title: 'Execution Clarity',
        desc: 'Entry, stop, target, and sizing logic in one clean workspace.',
        icon: <CandlestickChart className="h-7 w-7" />,
        accent: 'var(--neo-color-red)',
    },
    {
        title: 'Mentor Community',
        desc: 'Live room support, replay notes, and weekly breakdown sessions.',
        icon: <Users className="h-7 w-7" />,
        accent: 'var(--neo-color-blue)',
    },
    {
        title: 'Broker Connect',
        desc: 'Bridge strategy to execution with guided broker integration.',
        icon: <Link2 className="h-7 w-7" />,
        accent: 'var(--neo-color-green)',
    },
];

const LandingPage: React.FC = () => {
    const logo = usePortalLogo();
    const [data, setData] = useState<LandingData | null>(null);

    useEffect(() => {
        let cancelled = false;

        const fetchSnapshot = async () => {
            try {
                const res = await fetch('/api/v2/market/public-snapshot');
                if (!res.ok) return;
                const json: LandingData = await res.json();
                if (!cancelled) setData(json);
            } catch {
                // silently keep showing fallback / previous data
            }
        };

        fetchSnapshot();
        const id = window.setInterval(fetchSnapshot, 15000);
        return () => {
            cancelled = true;
            window.clearInterval(id);
        };
    }, []);

    return (
        <main className="neo-page relative flex min-h-screen w-full max-w-none flex-col gap-16 px-3 pb-20 pt-8 sm:px-5 md:px-8 md:pt-12 xl:px-10 2xl:px-16 lg:pt-16">
            <nav className="neo-card relative z-50 flex flex-wrap items-center justify-between gap-4 px-5 py-4 md:px-7 md:py-5">
                <Link to="/" className="group transition-transform hover:-translate-y-1" aria-label="Go to home page">
                    {logo ? (
                        <img src={logo} alt="Straddly" className="h-[84px] w-auto max-w-[300px] animate-float-slow object-contain drop-shadow-[0_15px_15px_var(--neo-shadow-dark)] md:h-[100px] md:max-w-[360px]" />
                    ) : (
                        <span className="text-2xl font-black tracking-wide text-[var(--neo-text-main)]">Straddly</span>
                    )}
                </Link>

                <div className="flex w-full flex-wrap items-center justify-end gap-3 md:w-auto md:gap-4">
                    <Link to="/" className="neo-btn px-4 py-2.5 text-xs uppercase tracking-[0.08em]">Home</Link>
                    <Link to="/login" className="neo-btn px-4 py-2.5 text-xs uppercase tracking-[0.08em]">
                        <LogIn className="h-4 w-4" /> Login
                    </Link>
                    <Link to="/sign-up" className="neo-btn px-4 py-2.5 text-xs uppercase tracking-[0.08em]">
                        <UserPlus className="h-4 w-4" /> Sign Up
                    </Link>
                    <a
                        href="https://learn.straddly.pro"
                        target="_blank"
                        rel="noreferrer"
                        className="neo-btn neo-btn-purple px-4 py-2.5 text-xs uppercase tracking-[0.08em]"
                    >
                        <Globe className="h-4 w-4" /> Free Course
                    </a>
                    <Link to="/funded" className="neo-btn-solid-blue px-4 py-2.5 text-xs uppercase tracking-[0.08em]">
                        <Gauge className="h-4 w-4" /> Eligibility
                    </Link>
                </div>
            </nav>

            <section className="neo-card overflow-hidden py-3">
                <div className="ticker-track flex items-center">
                    {(() => {
                        const ticker = data?.ticker?.length ? data.ticker : FALLBACK_TICKER;
                        return [...ticker, ...ticker].map((item, idx) => (
                            <div key={`${item.symbol}-${idx}`} className="ticker-item">
                                <span className="ticker-logo">{item.symbol[0]}</span>
                                <span className="text-xs font-black tracking-wide text-[var(--neo-text-main)]">{item.symbol}</span>
                                <span className="text-sm font-bold text-[var(--neo-text-main)]">INR {item.price.toLocaleString()}</span>
                                <span className={`text-xs font-black ${item.change >= 0 ? 'text-[var(--neo-color-green)]' : 'text-[var(--neo-color-red)]'}`}>
                                    {item.change >= 0 ? '▲' : '▼'} {Math.abs(item.change).toFixed(2)}%
                                </span>
                            </div>
                        ));
                    })()}
                </div>
            </section>

            <section className="grid items-center gap-12 md:grid-cols-[1.1fr_1fr]">
                <div className="relative z-10 space-y-8">
                    <div className="neo-badge border-white shadow-md">
                        <Sparkles className="h-4 w-4 text-[var(--neo-color-pink)]" />
                        <span className="text-[var(--neo-color-purple)]">Unified Trading, Learning, and Growth Platform</span>
                    </div>

                    <div className="space-y-6">
                        <div className="space-y-4">
                            <p className="text-xs font-black uppercase tracking-[0.28em] text-[var(--neo-color-blue)] sm:text-sm">
                                Market-grade intelligence
                            </p>
                            <h1 className="hero-title-3d text-[3.9rem] font-extrabold leading-[0.92] tracking-[-0.04em] sm:text-[4.8rem] lg:text-[5.6rem] xl:text-[6.1rem] 2xl:text-[6.6rem]">
                                <span>Trade</span>
                                <span>Like a</span>
                                <span>Pro</span>
                            </h1>
                        </div>
                        <p className="text-lg font-black text-[var(--neo-text-muted)] sm:text-2xl">
                            Advanced Indian market tools with real-time insights.
                        </p>
                        <p className="max-w-xl text-lg font-bold leading-relaxed text-[var(--neo-text-muted)] sm:text-xl">
                            One destination for live charts, market pulse, top traded stocks, strategy tools, and a guided learning ecosystem built for both beginners and serious traders.
                        </p>
                    </div>

                    <div className="flex flex-wrap items-center gap-5">
                        <span className="neo-btn-dug bg-[var(--neo-bg)] px-5 py-3 text-xs">
                            <ShieldCheck className="h-5 w-5 text-[var(--neo-color-green)]" />
                            Structured Risk Framework
                        </span>
                        <span className="neo-btn-dug px-5 py-3 text-xs">
                            <Activity className="h-5 w-5 text-[var(--neo-color-blue)]" />
                            Live Data + Actionable Insights
                        </span>
                    </div>

                    <div className="flex flex-wrap items-center gap-4 pt-4">
                        <Link to="/login" className="neo-btn-solid-purple px-8 py-4 text-sm uppercase tracking-[0.12em]">
                            Open Dashboard <ArrowRight className="h-4 w-4" />
                        </Link>
                        <a
                            href="https://learn.straddly.pro"
                            target="_blank"
                            rel="noreferrer"
                            className="neo-btn-solid-green px-8 py-4 text-sm uppercase tracking-[0.12em]"
                        >
                            Start Free Course <ArrowRight className="h-4 w-4" />
                        </a>
                    </div>
                </div>

                <div className="relative flex h-full w-full items-center justify-center">
                    <div className="neo-card w-full max-w-[720px] p-6 sm:p-8">
                        <div className="mb-5 flex items-center justify-between">
                            <div>
                                <p className="text-xs font-black uppercase tracking-[0.18em] text-[var(--neo-text-muted)]">Live Market Screen</p>
                                <h3 className="mt-2 text-2xl font-black text-[var(--neo-text-main)]">Straddly Pulse</h3>
                            </div>
                            <div className="pulse-chip">
                                <Activity className="h-4 w-4 text-[var(--neo-color-blue)]" />
                                Live Setup
                            </div>
                        </div>
                        <div className="pulse-board">
                            <div className="pulse-tiles">
                                {(data?.pulse_tiles ?? [
                                    { label: 'NIFTY 50',  value: '—', change: '—' },
                                    { label: 'BANKNIFTY', value: '—', change: '—' },
                                    { label: 'PCR',       value: '—', change: '—' },
                                ]).map((tile) => (
                                    <div key={tile.label} className="pulse-tile">
                                        <span className="pulse-tile-label">{tile.label}</span>
                                        <strong className="pulse-tile-value">{tile.value}</strong>
                                        <span className={`pulse-tile-change ${tile.change.startsWith('-') ? 'negative' : 'positive'}`}>{tile.change}</span>
                                    </div>
                                ))}
                            </div>

                            <div className="pulse-terminal">
                                <div className="pulse-chart-shell">
                                    <div className="pulse-chart-header">
                                        <div>
                                            <p className="pulse-overline">Index Snapshot</p>
                                            <h4 className="pulse-chart-title">NIFTY Intraday Structure</h4>
                                        </div>
                                        <div className="pulse-mini-badge positive">Trend Intact</div>
                                    </div>
                                    <div className="pulse-chart-grid">
                                        <div className="pulse-grid-overlay"></div>
                                        <div className="pulse-candle-row">
                                            {pulseCandles.map((height, idx) => (
                                                <div key={idx} className="pulse-candle-wrap">
                                                    <span className={`pulse-candle ${idx % 3 === 1 ? 'negative' : 'positive'}`} style={{ height: `${height}px` }}></span>
                                                </div>
                                            ))}
                                        </div>
                                        <div className="pulse-trend-line"></div>
                                    </div>
                                </div>

                                <div className="pulse-side-stack">
                                    <div className="pulse-side-card">
                                        <div className="pulse-side-title-row">
                                            <CandlestickChart className="h-4 w-4 text-[var(--neo-color-purple)]" />
                                            <span>Execution Window</span>
                                        </div>
                                        <div className="pulse-side-metrics">
                                            <div>
                                                <span>Entry</span>
                                                <strong>22,118</strong>
                                            </div>
                                            <div>
                                                <span>Stop</span>
                                                <strong>22,072</strong>
                                            </div>
                                            <div>
                                                <span>Target</span>
                                                <strong>22,224</strong>
                                            </div>
                                        </div>
                                    </div>

                                    <div className="pulse-side-card">
                                        <div className="pulse-side-title-row">
                                            <BarChart3 className="h-4 w-4 text-[var(--neo-color-blue)]" />
                                            <span>Signal Stack</span>
                                        </div>
                                        <div className="pulse-signal-list">
                                            {pulseSignals.map((signal) => (
                                                <div key={signal.label} className="pulse-signal-row">
                                                    <span>{signal.label}</span>
                                                    <strong className={`pulse-signal-${signal.tone}`}>{signal.value}</strong>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </section>

            <section className="space-y-8 pt-2">
                <div className="space-y-3 text-center">
                    <h2 className="text-3xl font-extrabold text-[var(--neo-text-main)] md:text-5xl">Platform Highlights</h2>
                    <p className="mx-auto max-w-3xl text-base font-bold text-[var(--neo-text-muted)] md:text-lg">
                        Rearranged and expanded cards designed for visibility, speed, and actionable decision support.
                    </p>
                </div>

                <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
                    {insightCards.map((card, idx) => (
                        <article key={idx} className="neo-card flex h-full flex-col justify-between gap-4 p-6 transition-transform duration-200 hover:-translate-y-1">
                            <div className="neo-dug flex h-12 w-12 items-center justify-center rounded-2xl" style={{ color: card.accent }}>
                                {card.icon}
                            </div>
                            <div>
                                <h3 className="mb-2 text-lg font-black text-[var(--neo-text-main)]">{card.title}</h3>
                                <p className="text-sm font-semibold leading-relaxed text-[var(--neo-text-muted)]">{card.desc}</p>
                            </div>
                        </article>
                    ))}
                </div>
            </section>

            <section className="grid gap-6 lg:grid-cols-3">
                <article className="neo-card p-6 md:p-8 lg:col-span-1">
                    <div className="mb-5 flex items-center justify-between">
                        <h3 className="text-xl font-black text-[var(--neo-text-main)]">Heatmap</h3>
                        <Flame className="h-6 w-6 text-[var(--neo-color-orange)]" />
                    </div>
                    <div className="grid h-[250px] grid-cols-7 grid-rows-4 gap-2">
                        {(data?.heatmap ?? Object.keys(HEATMAP_SIZES).map((name) => ({ name, value: 0 }))).map((block, idx) => {
                            const isPositive = block.value >= 0;
                            const sizeClass  = HEATMAP_SIZES[block.name] ?? 'col-span-2 row-span-1';
                            return (
                                <div
                                    key={idx}
                                    className={`heatmap-cell ${sizeClass}`}
                                    style={{
                                        background: isPositive
                                            ? 'linear-gradient(145deg, rgba(22, 163, 74, 0.18), rgba(16, 185, 129, 0.32))'
                                            : 'linear-gradient(145deg, rgba(225, 29, 72, 0.18), rgba(244, 63, 94, 0.3))',
                                        borderColor: isPositive ? 'rgba(22, 163, 74, 0.42)' : 'rgba(225, 29, 72, 0.35)',
                                    }}
                                >
                                    <span>{block.name}</span>
                                    <span>{block.value > 0 ? '+' : ''}{block.value.toFixed(1)}%</span>
                                </div>
                            );
                        })}
                    </div>
                </article>

                <article className="neo-card p-6 md:p-8">
                    <div className="mb-5 flex items-center justify-between">
                        <h3 className="text-xl font-black text-[var(--neo-text-main)]">Market Moves</h3>
                        <TrendingUp className="h-6 w-6 text-[var(--neo-color-green)]" />
                    </div>
                    <div className="space-y-3">
                        {(data?.movers ?? []).map((stock) => (
                            <div key={stock.name} className="neo-dug flex items-center justify-between rounded-2xl px-4 py-3">
                                <span className="text-sm font-black tracking-wide text-[var(--neo-text-main)]">{stock.name}</span>
                                <span className={`text-sm font-black ${stock.change >= 0 ? 'text-[var(--neo-color-green)]' : 'text-[var(--neo-color-red)]'}`}>
                                    {stock.change >= 0 ? '+' : ''}{stock.change.toFixed(2)}%
                                </span>
                            </div>
                        ))}
                    </div>
                </article>

                <article className="neo-card p-6 md:p-8">
                    <div className="mb-5 flex items-center justify-between">
                        <h3 className="text-xl font-black text-[var(--neo-text-main)]">Top Traded</h3>
                        <BarChart3 className="h-6 w-6 text-[var(--neo-color-blue)]" />
                    </div>
                    <div className="space-y-4">
                        {(data?.top_traded ?? []).map((stock) => (
                            <div key={stock.name}>
                                <div className="mb-2 flex items-center justify-between text-sm font-black text-[var(--neo-text-main)]">
                                    <span>{stock.name}</span>
                                    <span>{stock.volume}M</span>
                                </div>
                                <div className="neo-dug h-2.5 w-full rounded-full">
                                    <div
                                        className="h-2.5 rounded-full bg-gradient-to-r from-[var(--neo-color-blue)] to-[var(--neo-color-purple)]"
                                        style={{ width: `${stock.volume}%` }}
                                    ></div>
                                </div>
                            </div>
                        ))}
                    </div>
                </article>
            </section>

            <section id="what-you-learn" className="space-y-12">
                <div className="space-y-4 text-center">
                    <h2 className="text-3xl font-extrabold text-[var(--neo-text-main)] md:text-5xl">How We Support Your Growth</h2>
                    <p className="mx-auto max-w-2xl text-base font-bold text-[var(--neo-text-muted)] md:text-lg">
                        Structured learning, live guidance, and execution discipline.
                    </p>
                </div>

                <div className="grid gap-6 md:grid-cols-3">
                    {[
                        {
                            icon: <NexusAssetImage src="/vector_research.png" fallbackSrc="/icon_research.png" alt="Research" className="h-28 w-auto animate-float-slow" />,
                            title: 'Foundation Mastery',
                            desc: 'Build strong understanding before risking capital.',
                            points: ['Market structure', 'Instrument behavior']
                        },
                        {
                            icon: <NexusAssetImage src="/vector_brain_bulb.png" fallbackSrc="/icon_brain_bulb.png" alt="Brain Intel" className="h-28 w-auto animate-float-slow" style={{ animationDelay: '1s' }} />,
                            title: 'Technical Intel',
                            desc: 'Read context, timing, and momentum confirmation.',
                            points: ['Price action maps', 'Volume-led entries']
                        },
                        {
                            icon: <NexusAssetImage src="/vector_cert.png" fallbackSrc="/icon_cert.png" alt="Risk Cert" className="h-28 w-auto animate-float-slow" style={{ animationDelay: '2s' }} />,
                            title: 'Risk Architecture',
                            desc: 'Protect downside and compound consistently.',
                            points: ['Position sizing', 'Defined stop protocols']
                        }
                    ].map((card, idx) => (
                        <div key={idx} className="neo-card group flex flex-col items-center p-8 text-center md:p-10">
                            <div className="mb-5 flex h-36 w-36 items-center justify-center overflow-visible">
                                {card.icon}
                            </div>
                            <h3 className="mb-3 text-2xl font-black text-[var(--neo-text-main)]">{card.title}</h3>
                            <p className="mb-6 text-sm font-bold leading-relaxed text-[var(--neo-text-muted)] md:text-base">{card.desc}</p>
                            <div className="neo-dug mb-6 h-1 w-full rounded-full"></div>
                            <ul className="w-full space-y-4 text-left">
                                {card.points.map((p, i) => (
                                    <li key={i} className="flex items-center justify-center gap-3 text-center text-sm font-black text-[var(--neo-text-main)]">
                                        <CheckCircle2 className="h-5 w-5 shrink-0 text-[var(--neo-color-green)]" />
                                        <span>{p}</span>
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </div>
            </section>

            <section className="neo-card flex flex-col items-start justify-between gap-7 p-8 md:flex-row md:items-center md:p-10">
                <div className="space-y-3">
                    <p className="text-xs font-black uppercase tracking-[0.18em] text-[var(--neo-color-purple)]">Ready to Start</p>
                    <h3 className="text-2xl font-black text-[var(--neo-text-main)] md:text-3xl">Explore the platform and unlock your trading edge.</h3>
                    <p className="text-sm font-semibold text-[var(--neo-text-muted)] md:text-base">Sign in, learn from the free course, and check funded eligibility when ready.</p>
                </div>
                <div className="flex flex-wrap items-center gap-3">
                    <Link to="/login" className="neo-btn-solid-purple px-6 py-3 text-sm uppercase tracking-[0.08em]">Login</Link>
                    <Link to="/sign-up" className="neo-btn-solid-blue px-6 py-3 text-sm uppercase tracking-[0.08em]">Sign Up</Link>
                    <Link to="/funded" className="neo-btn px-6 py-3 text-sm uppercase tracking-[0.08em]">Eligibility</Link>
                </div>
            </section>

            <footer className="space-y-10 pt-[5rem]">
                <div className="neo-divider mx-auto w-full"></div>
                <div className="flex flex-col items-start justify-between gap-12 pt-6 lg:flex-row">
                    <div className="max-w-3xl">
                        <p className="mb-4 text-sm font-black uppercase tracking-[0.2em] text-[var(--neo-color-purple)]">Important Disclaimer</p>
                        <p className="text-justify text-sm font-medium leading-relaxed text-[var(--neo-text-muted)]">
                            Straddly is an educational and technology platform. We do not provide investment advice or guaranteed returns. All market data, examples, and strategy illustrations are for learning and analysis purposes only. Trading and investing involve risk. Please evaluate your objectives and consult a registered financial advisor where needed.
                        </p>
                    </div>
                    <div className="flex shrink-0 flex-col gap-6 text-sm font-black uppercase tracking-[0.1em] text-[var(--neo-text-main)]">
                        <Link to="/login" className="transition-colors hover:text-[var(--neo-color-purple)]">Login</Link>
                        <Link to="/sign-up" className="transition-colors hover:text-[var(--neo-color-green)]">Sign Up</Link>
                        <a href="https://learn.straddly.pro" target="_blank" rel="noreferrer" className="transition-colors hover:text-[var(--neo-color-blue)]">Free Course</a>
                        <Link to="/funded" className="transition-colors hover:text-[var(--neo-color-blue)]">Funded Program</Link>
                        <Link to="/rules" className="transition-colors hover:text-[var(--neo-color-green)]">Eligibility Rules</Link>
                    </div>
                </div>
                <div className="pb-8 pt-8 text-center text-sm font-bold text-[var(--neo-text-muted)]">
                    &copy; {new Date().getFullYear()} Straddly Academy. All rights reserved.
                </div>
            </footer>

            <style>{`
                .ticker-track {
                    width: max-content;
                    animation: tickerScroll 22s linear infinite;
                }

                .ticker-item {
                    display: inline-flex;
                    align-items: center;
                    gap: 10px;
                    margin-right: 30px;
                    padding-right: 6px;
                }

                .ticker-logo {
                    width: 24px;
                    height: 24px;
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    border-radius: 999px;
                    font-size: 11px;
                    font-weight: 800;
                    color: var(--neo-text-main);
                    background: var(--neo-surface);
                    box-shadow: 4px 4px 8px var(--neo-shadow-dark), -4px -4px 8px var(--neo-shadow-light);
                }

                .heatmap-cell {
                    border: 1px solid;
                    border-radius: 14px;
                    padding: 8px;
                    display: flex;
                    flex-direction: column;
                    justify-content: space-between;
                    font-size: 11px;
                    line-height: 1.2;
                    font-weight: 800;
                    color: var(--neo-text-main);
                }

                .hero-title-3d {
                    display: inline-flex;
                    flex-direction: column;
                    gap: 0.05em;
                    margin: 0;
                    color: #15213f;
                    text-shadow:
                        0 1px 0 rgba(255, 255, 255, 0.95),
                        0 2px 0 rgba(255, 255, 255, 0.9),
                        0 3px 0 rgba(202, 214, 242, 0.95),
                        0 4px 0 rgba(178, 193, 230, 0.9),
                        0 14px 24px rgba(126, 145, 198, 0.34);
                }

                .hero-title-3d span {
                    display: block;
                    background: linear-gradient(180deg, #ffffff 0%, #e7efff 18%, #aebfe7 48%, #5d76b0 100%);
                    -webkit-background-clip: text;
                    background-clip: text;
                    color: transparent;
                    position: relative;
                }

                .pulse-chip {
                    display: inline-flex;
                    align-items: center;
                    gap: 8px;
                    padding: 10px 14px;
                    border-radius: 999px;
                    font-size: 11px;
                    font-weight: 900;
                    letter-spacing: 0.16em;
                    text-transform: uppercase;
                    color: var(--neo-text-main);
                    background: linear-gradient(145deg, rgba(255, 255, 255, 0.98), rgba(235, 242, 255, 0.9));
                    box-shadow: 8px 8px 18px rgba(166, 179, 209, 0.18), -8px -8px 18px rgba(255, 255, 255, 0.82);
                }

                .pulse-board {
                    display: flex;
                    flex-direction: column;
                    gap: 18px;
                }

                .pulse-tiles {
                    display: grid;
                    grid-template-columns: repeat(3, minmax(0, 1fr));
                    gap: 12px;
                }

                .pulse-tile,
                .pulse-chart-shell,
                .pulse-side-card {
                    position: relative;
                    overflow: hidden;
                    border-radius: 24px;
                    background: linear-gradient(145deg, rgba(255, 255, 255, 0.96), rgba(228, 236, 250, 0.9));
                    box-shadow: 12px 12px 28px rgba(168, 181, 210, 0.18), -12px -12px 28px rgba(255, 255, 255, 0.78);
                }

                .pulse-tile {
                    display: flex;
                    flex-direction: column;
                    gap: 6px;
                    padding: 14px 16px;
                }

                .pulse-tile-label,
                .pulse-overline {
                    font-size: 10px;
                    font-weight: 900;
                    letter-spacing: 0.18em;
                    text-transform: uppercase;
                    color: var(--neo-text-muted);
                }

                .pulse-tile-value,
                .pulse-chart-title {
                    color: var(--neo-text-main);
                }

                .pulse-tile-value {
                    font-size: 1.05rem;
                    font-weight: 900;
                }

                .pulse-tile-change {
                    font-size: 0.8rem;
                    font-weight: 900;
                }

                .pulse-tile-change.positive,
                .pulse-mini-badge.positive,
                .pulse-signal-positive {
                    color: var(--neo-color-green);
                }

                .pulse-tile-change.negative,
                .pulse-signal-negative {
                    color: var(--neo-color-red);
                }

                .pulse-terminal {
                    display: grid;
                    grid-template-columns: minmax(0, 1.65fr) minmax(220px, 0.95fr);
                    gap: 16px;
                    align-items: stretch;
                }

                .pulse-chart-shell {
                    padding: 18px;
                }

                .pulse-chart-header,
                .pulse-side-title-row,
                .pulse-signal-row {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                }

                .pulse-chart-title {
                    margin-top: 6px;
                    font-size: 1.05rem;
                    font-weight: 900;
                }

                .pulse-mini-badge {
                    padding: 8px 10px;
                    border-radius: 999px;
                    background: rgba(22, 163, 74, 0.12);
                    font-size: 10px;
                    font-weight: 900;
                    letter-spacing: 0.14em;
                    text-transform: uppercase;
                }

                .pulse-chart-grid {
                    position: relative;
                    height: 265px;
                    margin-top: 18px;
                    padding: 18px 12px 12px;
                    border-radius: 22px;
                    overflow: hidden;
                    background:
                        linear-gradient(180deg, rgba(87, 130, 255, 0.18), rgba(87, 130, 255, 0) 35%),
                        linear-gradient(145deg, rgba(241, 246, 255, 0.9), rgba(221, 230, 247, 0.78));
                }

                .pulse-grid-overlay {
                    position: absolute;
                    inset: 0;
                    background-image:
                        linear-gradient(rgba(115, 138, 196, 0.14) 1px, transparent 1px),
                        linear-gradient(90deg, rgba(115, 138, 196, 0.14) 1px, transparent 1px);
                    background-size: 100% 20%, 12.5% 100%;
                    mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.9), transparent 96%);
                }

                .pulse-candle-row {
                    position: absolute;
                    inset: 18px 14px 16px 14px;
                    display: flex;
                    align-items: flex-end;
                    gap: 10px;
                }

                .pulse-candle-wrap {
                    flex: 1;
                    display: flex;
                    align-items: flex-end;
                    justify-content: center;
                    height: 100%;
                }

                .pulse-candle {
                    position: relative;
                    width: 16px;
                    border-radius: 999px;
                    box-shadow: 0 10px 16px rgba(40, 60, 120, 0.18);
                }

                .pulse-candle::before {
                    content: '';
                    position: absolute;
                    left: 50%;
                    top: -10px;
                    bottom: -10px;
                    width: 2px;
                    transform: translateX(-50%);
                    background: rgba(68, 90, 148, 0.35);
                    border-radius: 999px;
                }

                .pulse-candle.positive {
                    background: linear-gradient(180deg, #57e38f 0%, #169f5f 100%);
                }

                .pulse-candle.negative {
                    background: linear-gradient(180deg, #ff8aa6 0%, #e33d69 100%);
                }

                .pulse-trend-line {
                    position: absolute;
                    left: 22px;
                    right: 22px;
                    bottom: 44px;
                    height: 110px;
                    border-radius: 999px;
                    background: linear-gradient(90deg, rgba(72, 102, 188, 0) 0%, rgba(72, 102, 188, 0.16) 15%, rgba(72, 102, 188, 0.32) 55%, rgba(72, 102, 188, 0) 100%);
                    clip-path: polygon(0% 72%, 12% 66%, 22% 69%, 34% 52%, 46% 58%, 57% 42%, 68% 48%, 79% 24%, 91% 31%, 100% 12%, 100% 100%, 0% 100%);
                }

                .pulse-side-stack {
                    display: flex;
                    flex-direction: column;
                    gap: 16px;
                }

                .pulse-side-card {
                    padding: 16px;
                }

                .pulse-side-title-row {
                    gap: 10px;
                    justify-content: flex-start;
                    margin-bottom: 14px;
                    font-size: 0.78rem;
                    font-weight: 900;
                    letter-spacing: 0.08em;
                    text-transform: uppercase;
                    color: var(--neo-text-main);
                }

                .pulse-side-metrics {
                    display: grid;
                    gap: 10px;
                }

                .pulse-side-metrics div,
                .pulse-signal-row {
                    border-radius: 16px;
                    padding: 10px 12px;
                    background: rgba(255, 255, 255, 0.62);
                    box-shadow: inset 1px 1px 0 rgba(255, 255, 255, 0.82), inset -1px -1px 0 rgba(197, 210, 235, 0.36);
                }

                .pulse-side-metrics div {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    gap: 12px;
                }

                .pulse-side-metrics span,
                .pulse-signal-row span {
                    font-size: 0.74rem;
                    font-weight: 800;
                    color: var(--neo-text-muted);
                    text-transform: uppercase;
                    letter-spacing: 0.1em;
                }

                .pulse-side-metrics strong,
                .pulse-signal-row strong {
                    font-size: 0.9rem;
                    font-weight: 900;
                    color: var(--neo-text-main);
                }

                .pulse-signal-list {
                    display: flex;
                    flex-direction: column;
                    gap: 10px;
                }

                .pulse-signal-neutral {
                    color: var(--neo-color-blue);
                }

                @keyframes tickerScroll {
                    0% {
                        transform: translateX(0);
                    }
                    100% {
                        transform: translateX(-50%);
                    }
                }

                @media (max-width: 768px) {
                    .hero-title-3d {
                        text-shadow:
                            0 1px 0 rgba(255, 255, 255, 0.95),
                            0 2px 0 rgba(255, 255, 255, 0.88),
                            0 3px 0 rgba(194, 208, 240, 0.9),
                            0 10px 18px rgba(126, 145, 198, 0.28);
                    }

                    .pulse-tiles,
                    .pulse-terminal {
                        grid-template-columns: 1fr;
                    }

                    .ticker-track {
                        animation-duration: 16s;
                    }

                    .pulse-chart-grid {
                        height: 220px;
                    }
                }
            `}</style>
        </main>
    );
};

export default LandingPage;
