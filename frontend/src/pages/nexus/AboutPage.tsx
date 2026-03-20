import { ArrowLeft, ShieldCheck, Users, Briefcase, Target, BadgeCheck, Globe, Star, CheckCircle2, MapPin, Mail, Phone } from 'lucide-react';
import { Link } from 'react-router-dom';
import '../../styles/nexus/NeoTheme.css';
import NexusAssetImage from '../../components/nexus/NexusAssetImage';
import { usePortalLogo } from '../../hooks/usePortalLogo';

const AboutPage: React.FC = () => {
  const logo = usePortalLogo();

  return (
    <main className="neo-page relative mx-auto flex min-h-screen max-w-6xl flex-col px-4 pb-20 pt-8 md:px-8">
      <nav className="neo-card mb-16 flex items-center justify-between px-6 py-4">
        <Link to="/" className="group transition-transform hover:-translate-y-1">
          {logo ? (
            <img src={logo} alt="Straddly" className="h-[120px] w-auto max-w-[420px] animate-float-slow object-contain drop-shadow-[0_15px_15px_var(--neo-shadow-dark)] md:h-[120px]" />
          ) : (
            <span className="text-2xl font-black tracking-wide text-[var(--neo-text-main)]">Straddly</span>
          )}
        </Link>
        <Link to="/" className="flex items-center gap-2 text-sm font-bold text-[var(--neo-text-muted)] transition-colors hover:text-[var(--neo-text-main)]">
          <ArrowLeft className="h-4 w-4" />
          Back to Home
        </Link>
      </nav>

      <div className="mx-auto max-w-5xl space-y-24">
        <section className="space-y-8 pt-8 text-center">
          <div className="neo-badge mx-auto">
            <Globe className="h-4 w-4" />
            <span>Redefining Market Education</span>
          </div>
          <h1 className="text-4xl font-extrabold text-[var(--neo-text-main)] sm:text-6xl">
            Learn from someone who <br />
            <span className="text-gradient">actually trades</span> for a living.
          </h1>
          <p className="mx-auto max-w-3xl text-xl font-medium leading-relaxed text-[var(--neo-text-muted)]">
            Straddly was founded to strip away the noise and focus on what matters:
            professional-grade frameworks designed for real market survival.
          </p>
          <div className="flex items-center justify-center pt-8">
            <NexusAssetImage src="/vector_target.png" alt="Target" className="h-40 w-auto animate-float-slow drop-shadow-xl" />
          </div>
        </section>

        <section className="neo-card relative overflow-hidden p-10">
          <div className="relative z-10 grid items-start gap-10 md:grid-cols-[280px_1fr]">
            <div className="flex flex-col gap-8">
              <div className="neo-dug relative flex min-h-[220px] flex-col items-center justify-center rounded-3xl p-8">
                <Briefcase className="mb-6 h-16 w-16 text-[var(--neo-text-accent)]" />
                <p className="text-2xl font-black tracking-tight text-[var(--neo-text-main)]">THE MENTOR</p>
                <p className="mt-2 text-xs font-bold uppercase tracking-widest text-[var(--neo-text-muted)]">Certified Prop Trader</p>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="neo-dug p-4 text-center">
                  <p className="mb-1 text-[10px] font-black uppercase tracking-[0.1em] text-[var(--neo-text-muted)]">Accredited</p>
                  <p className="text-sm font-bold text-[var(--neo-text-main)]">NSE Academy</p>
                </div>
                <div className="neo-dug p-4 text-center">
                  <p className="mb-1 text-[10px] font-black uppercase tracking-[0.1em] text-[var(--neo-text-muted)]">Regulatory</p>
                  <p className="text-sm font-bold text-[var(--neo-text-main)]">SEBI Certified</p>
                </div>
              </div>

              <div className="neo-floating-cube rounded-[2rem] border-2 border-[var(--neo-bg)] bg-white p-6 text-center">
                <p className="mb-2 text-5xl font-black text-[var(--neo-color-green)]">15+</p>
                <p className="mt-2 text-xs font-black uppercase tracking-[0.1em] text-[var(--neo-text-muted)]">Years Market Experience</p>
              </div>
            </div>

            <div className="space-y-10 pl-2">
              <div className="space-y-4">
                <h2 className="text-3xl font-bold text-[var(--neo-text-main)]">Market-Hardened Experience</h2>
                <p className="text-base font-medium leading-relaxed text-[var(--neo-text-muted)]">
                  With over <strong className="text-[var(--neo-text-main)]">15 years of live market experience</strong> as a proprietary desk trader actively managing capital in the Indian markets, our mentor brings real institutional frameworks to the classroom. Unlike theoretical courses, you won&apos;t find complex technical jargon here - only the precise risk-first thinking that professional desks use every single trading day.
                </p>
              </div>

              <div className="neo-divider"></div>

              <div className="space-y-4">
                <h3 className="text-xl font-bold text-[var(--neo-text-main)]">Certifications &amp; Credentials</h3>
                <p className="text-base font-medium leading-relaxed text-[var(--neo-text-muted)]">
                  Certified by the National Stock Exchange (NSE) and SEBI in Equity, Derivatives, and Technical Analysis. Every framework taught in our sessions is directly backed by these professional accreditations and tested through years of live capital deployment.
                </p>
              </div>

              <div className="grid grid-cols-2 gap-5 pt-4">
                {[
                  { icon: ShieldCheck, text: 'Risk-First Architecture' },
                  { icon: Target, text: 'Price Action Systems' },
                  { icon: Users, text: '1,200+ Students Mentored' },
                  { icon: Star, text: '15+ Years Desk Experience' }
                ].map((item, idx) => (
                  <div key={idx} className="flex items-center gap-4">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl neo-btn">
                      <item.icon className="h-5 w-5 text-[var(--neo-text-accent)]" />
                    </div>
                    <span className="text-sm font-bold text-[var(--neo-text-main)]">{item.text}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>

        <section className="grid items-start gap-16 md:grid-cols-2">
          <div className="space-y-8">
            <h2 className="text-3xl font-bold text-[var(--neo-text-main)]">Why is this course 100% free?</h2>
            <p className="text-base font-medium leading-relaxed text-[var(--neo-text-muted)]">
              Straddly runs on a simple belief: the best traders are made, not bought. Our sessions are fully
              free to ensure no financial barrier stands between good education and aspiring traders.
            </p>
            <div className="space-y-4">
              {[
                'No extra charges - same brokerage as opening directly.',
                'You receive a manual + full hand-holding + priority support.',
                'You are free to only learn and never open an account with us.'
              ].map((text, i) => (
                <div key={i} className="neo-dug flex items-center gap-4 p-5">
                  <CheckCircle2 className="h-5 w-5 shrink-0 text-indigo-500" />
                  <p className="text-base font-bold text-[var(--neo-text-main)]">{text}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="neo-card mt-[4rem] space-y-8 p-12 text-center">
            <div className="neo-btn mx-auto mt-[-6rem] flex h-20 w-20 items-center justify-center rounded-2xl">
              <BadgeCheck className="h-10 w-10 text-purple-500" />
            </div>
            <p className="text-lg font-bold leading-relaxed text-[var(--neo-text-main)]">
              Opening your demat account is <span className="text-[var(--neo-text-accent)]">completely optional.</span> <br />We prioritize genuine education first.
            </p>
            <p className="text-sm font-medium italic text-[var(--neo-text-muted)]">
              *Referral partners receive dedicated trading setup help &amp; lifetime community access.
            </p>
            <Link to="/enroll" className="neo-btn-solid-purple mt-4 inline-flex w-full justify-center px-8 py-5 text-lg">
              Pre-Register for Next Batch
            </Link>
          </div>
        </section>

        <section className="neo-card overflow-hidden p-12">
          <div className="grid gap-12 md:grid-cols-2">
            <div className="space-y-8">
              <h2 className="text-2xl font-bold text-[var(--neo-text-main)]">Contact Us</h2>
              <p className="text-base font-medium text-[var(--neo-text-muted)]">Reach out to us for any queries, course registrations, or mentor access.</p>

              <div className="space-y-6 pt-2">
                <div className="flex items-center gap-5">
                  <div className="neo-dug flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl">
                    <Mail className="h-5 w-5 text-blue-500" />
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Email</p>
                    <a href="mailto:info@straddly.pro" className="text-base font-bold text-[var(--neo-text-main)] transition-colors hover:text-blue-500">
                      info@straddly.pro
                    </a>
                  </div>
                </div>

                <div className="flex items-center gap-5">
                  <div className="neo-dug flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl">
                    <Phone className="h-5 w-5 text-purple-500" />
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Call / WhatsApp</p>
                    <a href="tel:+918928940525" className="text-base font-bold text-[var(--neo-text-main)] transition-colors hover:text-purple-500">
                      +91 89289 40525
                    </a>
                  </div>
                </div>
              </div>
            </div>

            <div className="space-y-8">
              <h2 className="text-2xl font-bold text-[var(--neo-text-main)]">Our Office</h2>
              <div className="flex items-start gap-5">
                <div className="neo-dug flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl">
                  <MapPin className="h-5 w-5 text-indigo-500" />
                </div>
                <div>
                  <p className="mb-2 text-[10px] font-black uppercase tracking-widest text-[var(--neo-text-muted)]">Address</p>
                  <p className="text-base font-bold leading-relaxed text-[var(--neo-text-main)]">
                    13th Floor, Ozone Biz Centre,<br />
                    1307, Bellasis Rd, Mumbai Central,<br />
                    Mumbai, Maharashtra 400008
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <footer className="space-y-6 pb-6 pt-12 text-center">
          <div className="neo-divider mx-auto mb-8 w-32"></div>
          <p className="text-xs font-bold uppercase tracking-widest text-[var(--neo-text-muted)]">
            NSE &amp; SEBI Certification Credentials are available upon request.
          </p>
          <p className="mx-auto max-w-3xl text-xs font-medium leading-relaxed text-[var(--neo-text-muted)]">
            Important Disclaimer: All content, frameworks, and examples discussed are for educational and informational purposes only and should not be construed as investment advice or recommendations to buy or sell any securities. Trading and investing in the financial markets involve substantial risk of loss and are not suitable for every individual. We do not encourage, solicit, or offer advisory services.
          </p>
        </footer>
      </div>
    </main>
  );
};

export default AboutPage;
