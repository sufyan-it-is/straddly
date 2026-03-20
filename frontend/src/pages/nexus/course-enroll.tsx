import React, { useState } from 'react';
import { ArrowLeft, CheckCircle2, Clock, Sparkles, ShieldCheck, Send, MessageSquare, MapPin, Mail, Phone } from 'lucide-react';
import { Link } from 'react-router-dom';
import '../../styles/nexus/NeoTheme.css';
import NexusAssetImage from '../../components/nexus/NexusAssetImage';
import { usePortalLogo } from '../../hooks/usePortalLogo';

const SignupPage: React.FC = () => {
    const logo = usePortalLogo();

    const [formData, setFormData] = useState({
        fullName: '',
        email: '',
        mobile: '',
        city: '',
        experience: '0',
        interest: 'options',
        learningGoal: '',
    });

    const [isSubmitted, setIsSubmitted] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [errorMsg, setErrorMsg] = useState('');

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setIsLoading(true);
        setErrorMsg('');

        const apiBaseRaw = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v2';
        const apiBase = apiBaseRaw.endsWith('/') ? apiBaseRaw.slice(0, -1) : apiBaseRaw;
        const apiUrl = `${apiBase}/auth/portal/signup`;

        const experienceMap: Record<string, string> = {
            '0': 'Beginner',
            '2': 'Intermediate',
            '5': 'Advanced',
        };

        const payload = {
            name: formData.fullName,
            email: formData.email,
            experience_level: experienceMap[formData.experience] || 'Beginner',
            mobile: formData.mobile,
            city: formData.city,
            interest: formData.interest,
            learning_goal: formData.learningGoal,
        };

        try {
            const response = await fetch(apiUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });

            if (response.ok) {
                setIsLoading(false);
                setIsSubmitted(true);
            } else {
                const err = await response.json().catch(() => ({}));
                setErrorMsg(err?.detail || 'Could not complete registration. Please try again.');
                setIsLoading(false);
            }
        } catch {
            setErrorMsg('Network issue while submitting. Please try again.');
            setIsLoading(false);
        }
    };

    if (isSubmitted) {
        return (
            <main className="neo-page flex min-h-screen items-center justify-center p-4">
                <div className="neo-card max-w-lg p-12 text-center">
                    <div className="mb-8 flex justify-center">
                        <div className="neo-btn rounded-full border border-emerald-500/30 p-6">
                            <CheckCircle2 className="h-16 w-16 text-emerald-500" />
                        </div>
                    </div>
                    <h2 className="mb-4 text-3xl font-black text-[var(--neo-text-main)]">Registration Locked In!</h2>
                    <p className="mb-10 text-lg font-medium text-[var(--neo-text-muted)]">
                        Welcome to the elite cohort, <span className="font-bold text-emerald-600">{formData.fullName}</span>.
                        Someone from our team will be in touch with you via email or WhatsApp.
                    </p>
                    <div className="space-y-6">
                        <div className="neo-dug flex items-center gap-4 rounded-2xl border-l-4 border-l-[var(--neo-color-blue)] p-6 text-left">
                            <Clock className="h-8 w-8 shrink-0 text-[var(--neo-color-blue)]" />
                            <p className="text-sm font-bold text-[var(--neo-text-main)]">You&apos;ll hear from us shortly with details about the next steps.</p>
                        </div>
                        <Link to="/" className="neo-btn-solid-blue mt-6 block w-full scale-105 py-4">
                            Return to Dashboard
                        </Link>
                    </div>
                </div>
            </main>
        );
    }

    return (
        <main className="neo-page relative flex min-h-screen flex-col items-center justify-center px-4 py-20">
            <Link to="/" className="neo-btn fixed left-8 top-8 z-50 flex items-center gap-2 rounded-full px-5 py-3 text-sm font-bold hover:text-[var(--neo-text-accent)]">
                <ArrowLeft className="h-4 w-4" />
                Back to Home
            </Link>

            <div className="fixed right-8 top-8 z-50 hidden md:block">
                {logo ? (
                    <img src={logo} alt="Straddly" className="h-[120px] w-auto max-w-[420px] object-contain drop-shadow-[0_10px_10px_var(--neo-shadow-dark)]" />
                ) : (
                    <span className="text-xl font-black tracking-wide text-[var(--neo-text-main)]">Straddly</span>
                )}
            </div>

            <div className="mt-[6rem] w-full max-w-2xl md:mt-[4rem]">
                <div className="relative mb-16 text-center">
                    <div className="absolute left-1/2 top-[-8rem] -translate-x-1/2 transform">
                        <NexusAssetImage src="/vector_rocket.png" alt="Rocket" className="h-[120px] w-auto animate-float-slow drop-shadow-xl" />
                    </div>
                    <div className="neo-badge mb-8 mt-12 border border-indigo-200 shadow-md shadow-indigo-500/10">
                        <Sparkles className="h-4 w-4 text-[var(--neo-color-purple)]" />
                        <span className="font-black uppercase tracking-wide text-[var(--neo-color-purple)]">Limited seats available for the next cohort</span>
                    </div>
                    <h1 className="text-gradient mb-6 text-4xl font-black text-[var(--neo-text-main)]">Pre-Register Your Spot</h1>
                    <p className="text-lg font-medium text-[var(--neo-text-muted)]">Join the waitlist for the upcoming Live Trading Crash Course.</p>
                </div>

                <div className="neo-card p-8 md:p-14">
                    <form onSubmit={handleSubmit} className="space-y-10">
                        <div className="grid gap-8 md:grid-cols-2">
                            <div className="space-y-3">
                                <label className="px-2 text-xs font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Full Name</label>
                                <input required type="text" placeholder="John Doe" className="neo-input" value={formData.fullName} onChange={(e) => setFormData({ ...formData, fullName: e.target.value })} />
                            </div>
                            <div className="space-y-3">
                                <label className="px-2 text-xs font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Email Address</label>
                                <input required type="email" placeholder="john@example.com" className="neo-input" value={formData.email} onChange={(e) => setFormData({ ...formData, email: e.target.value })} />
                            </div>
                        </div>

                        <div className="grid gap-8 md:grid-cols-2">
                            <div className="space-y-3">
                                <label className="px-2 text-xs font-black uppercase tracking-widest text-[var(--neo-text-muted)]">WhatsApp Number</label>
                                <input required type="tel" placeholder="+91 00000 00000" className="neo-input" value={formData.mobile} onChange={(e) => setFormData({ ...formData, mobile: e.target.value })} />
                            </div>
                            <div className="space-y-3">
                                <label className="px-2 text-xs font-black uppercase tracking-widest text-[var(--neo-text-muted)]">City</label>
                                <div className="relative">
                                    <MapPin className="absolute left-5 top-1/2 h-5 w-5 -translate-y-1/2 text-[var(--neo-text-muted)]" />
                                    <input required type="text" placeholder="e.g. Mumbai" className="neo-input !pl-14" value={formData.city} onChange={(e) => setFormData({ ...formData, city: e.target.value })} />
                                </div>
                            </div>
                        </div>

                        <div className="grid gap-8 md:grid-cols-2">
                            <div className="space-y-3">
                                <label className="px-2 text-xs font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Experience (Years)</label>
                                <select className="neo-input appearance-none font-medium" value={formData.experience} onChange={(e) => setFormData({ ...formData, experience: e.target.value })}>
                                    <option value="0">Beginner (0-1 years)</option>
                                    <option value="2">Intermediate (2-5 years)</option>
                                    <option value="5">Advanced (5+ years)</option>
                                </select>
                            </div>
                            <div className="space-y-3">
                                <label className="px-2 text-xs font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Primary Interest</label>
                                <select className="neo-input appearance-none font-medium" value={formData.interest} onChange={(e) => setFormData({ ...formData, interest: e.target.value })}>
                                    <option value="stocks">Cash / Equity Stocks</option>
                                    <option value="options">Options &amp; Derivatives</option>
                                    <option value="commodity">Commodity &amp; Forex</option>
                                </select>
                            </div>
                        </div>

                        <div className="space-y-3">
                            <label className="px-2 text-xs font-black uppercase tracking-widest text-[var(--neo-text-muted)]">What is your primary goal for this course?</label>
                            <textarea placeholder="e.g. Setting up a side income, mastering risk management, etc." className="neo-input min-h-[120px] py-4" value={formData.learningGoal} onChange={(e) => setFormData({ ...formData, learningGoal: e.target.value })}></textarea>
                        </div>

                        <div className="pt-8">
                            {errorMsg && (
                                <p className="text-center text-sm font-bold text-red-500">{errorMsg}</p>
                            )}
                            <button type="submit" disabled={isLoading} className="neo-btn-solid-blue group w-full rounded-[2rem] py-6 text-lg tracking-wide disabled:cursor-not-allowed disabled:opacity-70">
                                {isLoading ? 'Submitting...' : 'Send Pre-Registration Request'}
                                {!isLoading && <Send className="ml-2 h-5 w-5 transition-transform group-hover:-translate-y-2 group-hover:translate-x-2" />}
                            </button>
                        </div>

                        <div className="flex flex-wrap items-center justify-center gap-8 pt-4 text-[10px] font-black uppercase tracking-[0.2em] text-[var(--neo-text-muted)]">
                            <span className="flex items-center gap-2">
                                <ShieldCheck className="h-4 w-4 text-emerald-500" />
                                Data Protected
                            </span>
                            <span className="h-2 w-2 rounded-full bg-[var(--neo-shadow-dark)]"></span>
                            <span className="flex items-center gap-2">
                                <MessageSquare className="h-4 w-4 text-emerald-500" />
                                24/7 Priority Support
                            </span>
                        </div>
                    </form>
                </div>

                <div className="neo-card mt-16 p-10">
                    <div className="grid gap-8 md:grid-cols-3">
                        <div className="flex items-center gap-5">
                            <div className="neo-dug flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white">
                                <MapPin className="h-5 w-5 text-indigo-500" />
                            </div>
                            <div>
                                <p className="mb-1 text-[10px] font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Our Office</p>
                                <p className="text-xs font-bold leading-relaxed text-[var(--neo-text-main)]">
                                    13th Floor, Ozone Biz Centre,<br />
                                    Mumbai Central, MH 400008
                                </p>
                            </div>
                        </div>

                        <div className="flex items-center gap-5">
                            <div className="neo-dug flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white">
                                <Mail className="h-5 w-5 text-blue-500" />
                            </div>
                            <div>
                                <p className="mb-1 text-[10px] font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Email Us</p>
                                <a href="mailto:info@straddly.pro" className="text-xs font-bold text-[var(--neo-text-main)] hover:text-blue-500">
                                    info@straddly.pro
                                </a>
                            </div>
                        </div>

                        <div className="flex items-center gap-5">
                            <div className="neo-dug flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-white">
                                <Phone className="h-5 w-5 text-purple-500" />
                            </div>
                            <div>
                                <p className="mb-1 text-[10px] font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Call / WhatsApp</p>
                                <a href="tel:+918928940525" className="text-xs font-bold text-[var(--neo-text-main)] hover:text-purple-500">
                                    +91 89289 40525
                                </a>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </main>
    );
};

export default SignupPage;
