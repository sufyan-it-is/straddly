import React, { useMemo, useState } from 'react';
import { CheckCircle2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import '../../styles/nexus/NeoTheme.css';
import { usePortalLogo } from '../../hooks/usePortalLogo';

const REQUIRED_KEYS = [
  'first_name',
  'last_name',
  'phone',
  'otp',
  'email',
  'pan_number',
  'aadhar_number',
  'pan_upload',
  'aadhar_upload',
  'bank_account_number',
  'ifsc',
] as const;

type FormState = {
  first_name: string;
  middle_name: string;
  last_name: string;
  phone: string;
  otp: string;
  email_otp: string;
  email: string;
  address: string;
  city: string;
  state: string;
  country: string;
  pan_number: string;
  aadhar_number: string;
  pan_upload: string;
  pan_upload_name: string;
  aadhar_upload: string;
  aadhar_upload_name: string;
  bank_account_number: string;
  ifsc: string;
  upi_id: string;
};

const initialForm: FormState = {
  first_name: '',
  middle_name: '',
  last_name: '',
  phone: '',
  otp: '',
  email_otp: '',
  email: '',
  address: '',
  city: '',
  state: '',
  country: 'India',
  pan_number: '',
  aadhar_number: '',
  pan_upload: '',
  pan_upload_name: '',
  aadhar_upload: '',
  aadhar_upload_name: '',
  bank_account_number: '',
  ifsc: '',
  upi_id: '',
};

const AccountSignupPage: React.FC = () => {
  const logo = usePortalLogo();
  const [form, setForm] = useState<FormState>(initialForm);
  const [otpSending, setOtpSending] = useState(false);
  const [otpVerifying, setOtpVerifying] = useState(false);
  const [otpVerified, setOtpVerified] = useState(false);
  const [otpMessage, setOtpMessage] = useState('');
  const [emailOtpSending, setEmailOtpSending] = useState(false);
  const [emailOtpVerifying, setEmailOtpVerifying] = useState(false);
  const [emailOtpVerified, setEmailOtpVerified] = useState(false);
  const [emailOtpMessage, setEmailOtpMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');
  const [successMessage, setSuccessMessage] = useState('');

  const apiBaseRaw = (import.meta.env.VITE_API_URL as string | undefined) || '/api/v2';
  const apiBase = apiBaseRaw.endsWith('/') ? apiBaseRaw.slice(0, -1) : apiBaseRaw;

  const mandatoryComplete = useMemo(() => {
    return REQUIRED_KEYS.every((k) => {
      const value = form[k];
      return typeof value === 'string' ? value.trim().length > 0 : !!value;
    });
  }, [form]);

  const canSubmit = mandatoryComplete && otpVerified && emailOtpVerified && !submitting;

  const update = (key: keyof FormState, value: string) => {
    if (key === 'phone') {
      setOtpVerified(false);
      setOtpMessage('');
    }
    if (key === 'otp') {
      setOtpVerified(false);
      setOtpMessage('');
    }
    if (key === 'email') {
      setEmailOtpVerified(false);
      setEmailOtpMessage('');
    }
    if (key === 'email_otp') {
      setEmailOtpVerified(false);
      setEmailOtpMessage('');
    }
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const onImageFile = async (file: File, targetKey: 'pan_upload' | 'aadhar_upload', nameKey: 'pan_upload_name' | 'aadhar_upload_name') => {
    setErrorMessage('');
    if (!file.type.startsWith('image/')) {
      setErrorMessage('Please upload an image file only.');
      return;
    }
    if (file.size > 1_048_576) {
      setErrorMessage('Upload limit is 1 MB per file.');
      return;
    }

    const base64Data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ''));
      reader.onerror = () => reject(new Error('Could not read file'));
      reader.readAsDataURL(file);
    });

    setForm((prev) => ({
      ...prev,
      [targetKey]: base64Data,
      [nameKey]: file.name,
    }));
  };

  const sendOtp = async () => {
    setErrorMessage('');
    setSuccessMessage('');
    setOtpMessage('');
    setOtpVerified(false);

    if (!form.phone.trim()) {
      setErrorMessage('Please enter phone number first.');
      return;
    }

    setOtpSending(true);
    try {
      const res = await fetch(`${apiBase}/auth/otp/send-phone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: form.phone, purpose: 'signup' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErrorMessage(data?.detail || 'Could not send OTP. Please try again.');
      } else {
        setOtpMessage('OTP sent successfully.');
      }
    } catch {
      setErrorMessage('Network issue while sending OTP.');
    } finally {
      setOtpSending(false);
    }
  };

  const verifyOtp = async () => {
    setErrorMessage('');
    setSuccessMessage('');
    setOtpMessage('');

    if (!form.phone.trim() || !form.otp.trim()) {
      setErrorMessage('Please enter phone and OTP first.');
      return;
    }

    setOtpVerifying(true);
    try {
      const res = await fetch(`${apiBase}/auth/otp/verify-phone`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: form.phone, purpose: 'signup', otp: form.otp }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setOtpVerified(false);
        setErrorMessage(data?.detail || 'Invalid OTP.');
      } else {
        setOtpVerified(true);
        setOtpMessage('Phone verified successfully.');
      }
    } catch {
      setErrorMessage('Network issue while verifying OTP.');
    } finally {
      setOtpVerifying(false);
    }
  };

  const sendEmailOtp = async () => {
    setErrorMessage('');
    setSuccessMessage('');
    setEmailOtpMessage('');
    setEmailOtpVerified(false);

    if (!form.email.trim()) {
      setErrorMessage('Please enter email first.');
      return;
    }

    setEmailOtpSending(true);
    try {
      const res = await fetch(`${apiBase}/auth/otp/send-email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.email, purpose: 'signup' }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErrorMessage(data?.detail || 'Could not send email OTP. Please try again.');
      } else {
        setEmailOtpMessage('Email OTP sent successfully.');
      }
    } catch {
      setErrorMessage('Network issue while sending email OTP.');
    } finally {
      setEmailOtpSending(false);
    }
  };

  const verifyEmailOtp = async () => {
    setErrorMessage('');
    setSuccessMessage('');
    setEmailOtpMessage('');

    if (!form.email.trim() || !form.email_otp.trim()) {
      setErrorMessage('Please enter email and email OTP first.');
      return;
    }

    setEmailOtpVerifying(true);
    try {
      const res = await fetch(`${apiBase}/auth/otp/verify-email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: form.email, purpose: 'signup', otp: form.email_otp }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setEmailOtpVerified(false);
        setErrorMessage(data?.detail || 'Invalid email OTP.');
      } else {
        setEmailOtpVerified(true);
        setEmailOtpMessage('Email verified successfully.');
      }
    } catch {
      setErrorMessage('Network issue while verifying email OTP.');
    } finally {
      setEmailOtpVerifying(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage('');
    setSuccessMessage('');

    if (!canSubmit) {
      setErrorMessage('Fill all mandatory fields and verify both phone OTP and email OTP before submitting.');
      return;
    }

    setSubmitting(true);
    const payload = {
      first_name: form.first_name.trim(),
      middle_name: form.middle_name.trim(),
      last_name: form.last_name.trim(),
      phone: form.phone.trim(),
      email: form.email.trim(),
      address: form.address.trim(),
      city: form.city.trim(),
      state: form.state.trim(),
      country: form.country.trim() || 'India',
      pan_number: form.pan_number.trim(),
      aadhar_number: form.aadhar_number.trim(),
      pan_upload: form.pan_upload,
      aadhar_upload: form.aadhar_upload,
      bank_account_number: form.bank_account_number.trim(),
      ifsc: form.ifsc.trim(),
      upi_id: form.upi_id.trim(),
    };

    try {
      const res = await fetch(`${apiBase}/auth/portal/account-signup`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErrorMessage(data?.detail || 'Could not submit form.');
      } else {
        setSuccessMessage('Submitted successfully. Your application is pending admin approval.');
        setForm(initialForm);
        setOtpVerified(false);
        setOtpMessage('');
        setEmailOtpVerified(false);
        setEmailOtpMessage('');
      }
    } catch {
      setErrorMessage('Network issue while submitting form.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="rules-page signup-page">
      <div className="rules-container">
        <nav className="rules-nav">
          <div className="rules-logo-wrap">
            {logo ? <img src={logo} alt="straddly" className="rules-logo-img" /> : null}
            <div className="rules-logo">straddly</div>
          </div>
          <div className="rules-nav-links">
            <Link to="/">Home</Link>
            <Link to="/rules">Trading Rules</Link>
            <Link to="/login">Sign In</Link>
          </div>
        </nav>

        <section className="rules-hero signup-hero">
          <div className="signup-hero-title">
            <h1>Account <span className="rules-gradient">Signup</span></h1>
            {logo ? <img src={logo} alt="straddly logo" className="signup-hero-logo" /> : null}
          </div>
          <p>Fields marked with * are mandatory. Uploads must be image files up to 1 MB.</p>
        </section>

        <div className="rules-card signup-card">

        <form className="mt-8 space-y-5" onSubmit={submit}>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Field label="First Name*"><input className="input" value={form.first_name} onChange={(e) => update('first_name', e.target.value)} /></Field>
            <Field label="Middle Name"><input className="input" value={form.middle_name} onChange={(e) => update('middle_name', e.target.value)} /></Field>
            <Field label="Last Name*"><input className="input" value={form.last_name} onChange={(e) => update('last_name', e.target.value)} /></Field>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
            <Field label="Phone* (will be your login ID)">
              <div className="flex items-center gap-2">
                <input className="input" value={form.phone} onChange={(e) => update('phone', e.target.value)} />
                {otpVerified && <CheckCircle2 className="h-5 w-5 text-emerald-600" aria-label="Phone verified" />}
              </div>
            </Field>
            <div>
              <button type="button" onClick={sendOtp} disabled={otpSending} className="btn-secondary w-full">
                {otpSending ? 'Sending...' : 'Send OTP'}
              </button>
            </div>
            <div />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
            <Field label="OTP*"><input className="input" value={form.otp} onChange={(e) => update('otp', e.target.value)} /></Field>
            <div>
              <button type="button" onClick={verifyOtp} disabled={otpVerifying} className="btn-secondary w-full">
                {otpVerifying ? 'Verifying...' : 'Verify OTP'}
              </button>
            </div>
            <div className="text-xs text-slate-600">{otpMessage}</div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Email Id*">
              <div className="flex items-center gap-2">
                <input className="input" type="email" value={form.email} onChange={(e) => update('email', e.target.value)} />
                {emailOtpVerified && <CheckCircle2 className="h-5 w-5 text-emerald-600" aria-label="Email verified" />}
              </div>
            </Field>
            <Field label="Address"><input className="input" value={form.address} onChange={(e) => update('address', e.target.value)} /></Field>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
            <div>
              <button type="button" onClick={sendEmailOtp} disabled={emailOtpSending} className="btn-secondary w-full">
                {emailOtpSending ? 'Sending...' : 'Send Email OTP'}
              </button>
            </div>
            <Field label="Email OTP*"><input className="input" value={form.email_otp} onChange={(e) => update('email_otp', e.target.value)} /></Field>
            <div>
              <button type="button" onClick={verifyEmailOtp} disabled={emailOtpVerifying} className="btn-secondary w-full">
                {emailOtpVerifying ? 'Verifying...' : 'Verify Email OTP'}
              </button>
            </div>
          </div>
          <div className="text-xs text-slate-600">{emailOtpMessage}</div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Field label="City"><input className="input" value={form.city} onChange={(e) => update('city', e.target.value)} /></Field>
            <Field label="State"><input className="input" value={form.state} onChange={(e) => update('state', e.target.value)} /></Field>
            <Field label="Country"><input className="input" value={form.country} onChange={(e) => update('country', e.target.value)} /></Field>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="PAN Number*"><input className="input" value={form.pan_number} onChange={(e) => update('pan_number', e.target.value)} /></Field>
            <Field label="Aadhar Number*"><input className="input" value={form.aadhar_number} onChange={(e) => update('aadhar_number', e.target.value)} /></Field>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="PAN Upload*">
              <input
                className="input"
                type="file"
                accept="image/*"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void onImageFile(file, 'pan_upload', 'pan_upload_name');
                }}
              />
              {form.pan_upload_name && <p className="mt-1 text-xs text-slate-500">{form.pan_upload_name}</p>}
            </Field>
            <Field label="Aadhar Upload*">
              <input
                className="input"
                type="file"
                accept="image/*"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void onImageFile(file, 'aadhar_upload', 'aadhar_upload_name');
                }}
              />
              {form.aadhar_upload_name && <p className="mt-1 text-xs text-slate-500">{form.aadhar_upload_name}</p>}
            </Field>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <Field label="Bank A/c Number*"><input className="input" value={form.bank_account_number} onChange={(e) => update('bank_account_number', e.target.value)} /></Field>
            <Field label="IFSC*"><input className="input" value={form.ifsc} onChange={(e) => update('ifsc', e.target.value)} /></Field>
            <Field label="UPI ID"><input className="input" value={form.upi_id} onChange={(e) => update('upi_id', e.target.value)} /></Field>
          </div>

          {errorMessage && <p className="text-sm font-medium text-red-600">{errorMessage}</p>}
          {successMessage && <p className="text-sm font-medium text-emerald-600">{successMessage}</p>}

          <button type="submit" disabled={!canSubmit} className="btn-primary w-full">
            {submitting ? 'Submitting...' : 'Submit'}
          </button>
        </form>
        </div>

      <style>{`
        .signup-page .rules-container {
          max-width: 1080px;
        }
        .signup-hero {
          margin-top: 28px;
          padding: 42px 30px;
        }
        .signup-hero-title {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 12px;
        }
        .signup-hero-logo {
          height: 34px;
          width: auto;
          max-width: 150px;
          object-fit: contain;
        }
        .signup-card {
          margin-top: 24px;
        }
        .input {
          width: 100%;
          border: 1px solid #cbd5e1;
          border-radius: 10px;
          height: 42px;
          padding: 0 12px;
          font-size: 14px;
          color: #0f172a;
          background: #ffffff;
        }
        .btn-primary {
          border: none;
          border-radius: 12px;
          height: 46px;
          font-size: 15px;
          font-weight: 700;
          color: #ffffff;
          background: #0f766e;
        }
        .btn-primary:disabled {
          cursor: not-allowed;
          opacity: 0.5;
        }
        .btn-secondary {
          border: 1px solid #94a3b8;
          border-radius: 10px;
          height: 42px;
          font-size: 14px;
          font-weight: 600;
          color: #0f172a;
          background: #f8fafc;
        }
        .btn-secondary:disabled {
          cursor: not-allowed;
          opacity: 0.6;
        }
      `}</style>
      </div>
    </main>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <label className="block">
    <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-600">{label}</span>
    {children}
  </label>
);

export default AccountSignupPage;
