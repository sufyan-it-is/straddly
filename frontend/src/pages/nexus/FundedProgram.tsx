import React from 'react';
import '../../styles/nexus/NeoTheme.css';
import { usePortalLogo } from '../../hooks/usePortalLogo';

const ENROLL_URL = '/enroll';
const SIGNUP_URL = '/sign-up';

type ProcessGraphic = 'register' | 'training' | 'evaluation' | 'funding';

const processSteps: Array<{
  step: string;
  eyebrow: string;
  title: string;
  text: string;
  graphic: ProcessGraphic;
}> = [
  {
    step: 'Step 1',
    eyebrow: 'Free Enrollment',
    title: 'Register For The Crash Course',
    text: 'Reserve your seat in the free derivatives crash course and enter the selection pipeline.',
    graphic: 'register',
  },
  {
    step: 'Step 2',
    eyebrow: 'Live Training',
    title: 'Attend The 2-Day Program',
    text: 'Go through the structured training sessions covering the core concepts and live market thinking.',
    graphic: 'training',
  },
  {
    step: 'Step 3',
    eyebrow: 'Evaluation',
    title: 'Complete The Assessment',
    text: 'Take the evaluation during the program so your discipline, understanding, and decision-making can be measured.',
    graphic: 'evaluation',
  },
  {
    step: 'Step 4',
    eyebrow: 'Funding Decision',
    title: 'Get Shortlisted For Funding',
    text: 'Top candidates move forward to the straddly funded trader shortlist.',
    graphic: 'funding',
  },
];

function renderProcessGraphic(graphic: ProcessGraphic) {
  if (graphic === 'register') {
    return (
      <div className="funded-mini-scene funded-mini-scene-register" aria-hidden="true">
        <div className="funded-mini-window">
          <span className="funded-mini-chip">Free</span>
          <div className="funded-mini-line funded-mini-line-wide" />
          <div className="funded-mini-line" />
          <div className="funded-mini-button">Join</div>
        </div>
      </div>
    );
  }

  if (graphic === 'training') {
    return (
      <div className="funded-mini-scene funded-mini-scene-training" aria-hidden="true">
        <div className="funded-mini-training-stack">
          <div className="funded-mini-training-card funded-mini-training-card-cap">
            <span className="funded-mini-training-icon">🎓</span>
            <span className="funded-mini-training-label">Live Training</span>
          </div>

          <div className="funded-mini-training-card funded-mini-training-card-cert">
            <span className="funded-mini-training-icon">📜</span>
            <span className="funded-mini-training-label">Certificate</span>
          </div>

          <div className="funded-mini-tag">2 Days</div>
        </div>
      </div>
    );
  }

  if (graphic === 'evaluation') {
    return (
      <div className="funded-mini-scene funded-mini-scene-evaluation" aria-hidden="true">
        <div className="funded-mini-checklist">
          <div className="funded-mini-check funded-mini-check-on" />
          <div className="funded-mini-check funded-mini-check-on" />
          <div className="funded-mini-check" />
        </div>

        <div className="funded-mini-score-wrap">
          <div className="funded-mini-score">82%</div>
        </div>
      </div>
    );
  }

  return (
    <div className="funded-mini-scene funded-mini-scene-funding" aria-hidden="true">
      <div className="funded-mini-wallet">
        <div className="funded-mini-wallet-top">
          <div className="funded-mini-wallet-band" />
          <div className="funded-mini-badge">Selected</div>
        </div>
        <div className="funded-mini-wallet-text">4X Capital</div>
      </div>
    </div>
  );
}

const FundedProgram: React.FC = () => {
  const logo = usePortalLogo();

  return (
    <div className="funded-page">
      <div className="funded-container">
        <nav className="funded-nav">
          <div className="funded-logo-wrap">
            {logo ? <img src={logo} alt="straddly" className="funded-logo-img" /> : null}
            <div className="funded-logo">straddly</div>
          </div>
          <a href={SIGNUP_URL}>Sign Up</a>
        </nav>

        <section className="funded-hero">
          <h1>
            Become a <span className="funded-gradient">Funded Trader</span>
          </h1>

          <p>
            straddly is launching a unique opportunity for derivatives traders across India.
            Participate in our <span className="funded-gradient">Free 2-Day Derivatives Crash Course</span> where your trading
            knowledge and discipline will be evaluated. Top performers may receive the opportunity
            to trade with <span className="funded-gradient">400% capital provided by straddly.</span>
          </p>

          <a className="funded-cta" href={ENROLL_URL}>
            Sign Up Now
          </a>
        </section>

        <section className="funded-grid">
          <div className="funded-card">
            <div className="funded-icon">💰</div>
            <h3>400% Capital Funding</h3>
            <p>Selected traders receive access to trading capital provided by straddly.</p>
          </div>

          <div className="funded-card">
            <div className="funded-icon">📊</div>
            <h3>Derivatives Crash Course</h3>
            <p>A structured 2-day program covering essential futures and options concepts.</p>
          </div>

          <div className="funded-card">
            <div className="funded-icon">🧠</div>
            <h3>Skill Evaluation</h3>
            <p>Participants will be evaluated through discussions and a short test.</p>
          </div>

          <div className="funded-card">
            <div className="funded-icon">🎓</div>
            <h3>Certification</h3>
            <p>Participants completing the test will receive straddly certification.</p>
          </div>
        </section>

        <section className="funded-process">
          <div className="funded-process-heading">
            <span className="funded-process-kicker">Selection Process</span>
            <h2>
              The Path To <span className="funded-gradient">straddly Funding</span>
            </h2>
            <p>
              A clearer visual journey from free course registration to final funding shortlist.
            </p>
          </div>

          <div className="funded-process-flow">
            {processSteps.map((step, index) => (
              <React.Fragment key={step.step}>
                <article className="funded-step-card">
                  <div className="funded-step-card-top">
                    <span className="funded-step-pill">{step.step}</span>
                    <span className="funded-step-eyebrow">{step.eyebrow}</span>
                  </div>

                  <div className="funded-step-visual">
                    {renderProcessGraphic(step.graphic)}
                  </div>

                  <h3>{step.title}</h3>
                  <p>{step.text}</p>
                </article>

                {index < processSteps.length - 1 ? (
                  <div className="funded-step-connector" aria-hidden="true">
                    <span className="funded-step-connector-line" />
                    <span className="funded-step-connector-dot" />
                  </div>
                ) : null}
              </React.Fragment>
            ))}
          </div>

          <div className="funded-skip-course">
            <div className="funded-skip-course-visual" aria-hidden="true">
              <div className="funded-skip-bolt" />
              <div className="funded-skip-card">
                <span className="funded-skip-card-title">Instant Funding</span>
                <span className="funded-skip-card-value">Fast Track</span>
              </div>
            </div>

            <div className="funded-skip-course-copy">
              <span className="funded-process-kicker">Alternate Route</span>
              <h3>
                Or skip the course and get <span className="funded-highlight-cool">instant funding</span>.
              </h3>
              <p>
                If you want a direct route, choose the instant-funding path and move straight into a faster capital access journey.
              </p>
            </div>

            <a className="funded-cta funded-cta-secondary" href={SIGNUP_URL}>
              Explore Instant Funding
            </a>
          </div>
        </section>

        <section className="funded-final">
          <h2 className="funded-gradient">Limited Seats Available</h2>
          <p>
            Join the upcoming batch of the Free Derivatives Crash Course and get the
            opportunity to become a funded trader.
          </p>
          <a className="funded-cta" href={ENROLL_URL}>
            Secure Your Seat
          </a>
        </section>

        <footer className="funded-footer">straddly © 2026</footer>
      </div>
    </div>
  );
};

export default FundedProgram;
