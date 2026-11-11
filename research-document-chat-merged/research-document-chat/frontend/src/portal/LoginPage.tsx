import { useState, type FormEvent, type ReactNode } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Eye,
  EyeOff,
  Loader2,
  MessageCircle,
  Moon,
  Shield,
  Stethoscope,
  Sun,
  UserRound,
} from 'lucide-react';
import { RegisterWizard } from './RegisterWizard';
import { ContactVerification } from './ContactVerification';
import { MfaChallenge, MfaEnroll, RecoveryCodes } from './MfaSteps';
import { glassInput } from './formStyles';
import { useAuth, ROLE_LABEL, type AuthOutcome } from './AuthContext';
import { useToast } from '../contexts/ToastContext';
import { useTheme } from '../contexts/ThemeContext';
import { ApiError, type PortalUser } from '../services/digitalMslService';

type Role = PortalUser['role'];

/** Demo / staging only. One flag; default off. The backend refuses to start with demo mode in production. */
const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true';

const ROLES: Array<{ id: Role; blurb: string; icon: ReactNode }> = [
  { id: 'doctor', blurb: 'Ask clinical questions and get cited answers.', icon: <Stethoscope className="w-[18px] h-[18px]" aria-hidden="true" /> },
  { id: 'msl', blurb: 'Review questions escalated by the AI.', icon: <UserRound className="w-[18px] h-[18px]" aria-hidden="true" /> },
  { id: 'admin', blurb: 'Manage users, sources and settings.', icon: <Shield className="w-[18px] h-[18px]" aria-hidden="true" /> },
];

/** The seeded dev accounts. Dead-code eliminated from production builds (DEMO_MODE is a build-time constant). */
const DEMOS: Record<Role, { name: string; email: string; password: string }> | null =
  import.meta.env.VITE_DEMO_MODE === 'true'
    ? {
        doctor: { name: 'Demo Doctor', email: 'doctor@demo.example', password: 'DemoMSL#2026' },
        msl: { name: 'Demo Physical MSL', email: 'msl@demo.example', password: 'DemoMSL#2026' },
        admin: { name: 'Demo Administrator', email: 'admin@demo.example', password: 'DemoMSL#2026' },
      }
    : null;

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1 flex items-center gap-1 text-xs text-[color:var(--err)]" role="alert">
      <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" /> {message}
    </p>
  );
}

type View =
  | { name: 'signin' }
  | { name: 'register' }
  | { name: 'verify'; userId: string }
  | { name: 'mfa'; token: string }
  | { name: 'mfa_enroll'; token: string }
  | { name: 'recovery'; codes: string[]; commit: () => void };

export function LoginPage() {
  const { login, completeMfa, completeMfaEnrollment, sessionNotice, clearSessionNotice } = useAuth();
  const toast = useToast();
  const [view, setView] = useState<View>({ name: 'signin' });
  const [role, setRole] = useState<Role>('doctor');
  const [name, setName] = useState(DEMOS ? DEMOS.doctor.name : '');
  const [email, setEmail] = useState(DEMOS ? DEMOS.doctor.email : '');
  const [password, setPassword] = useState(DEMOS ? DEMOS.doctor.password : '');
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [formError, setFormError] = useState('');
  const [stepError, setStepError] = useState('');
  const { theme, toggle: toggleTheme } = useTheme();

  const pickRole = (id: Role) => {
    setRole(id);
    setFormError('');
    if (DEMOS) {
      setName(DEMOS[id].name);
      setEmail(DEMOS[id].email);
      setPassword(DEMOS[id].password);
    }
  };

  const errors = {
    name: name.trim() ? '' : 'Enter your name.',
    email: /^\S+@\S+\.\S+$/.test(email.trim()) ? '' : 'Enter a valid work email.',
    password: password ? '' : 'Enter your password.',
  };
  const hasErrors = Object.values(errors).some(Boolean);

  const goSignin = () => {
    setView({ name: 'signin' });
    setStepError('');
    setFormError('');
  };

  /** Move to whatever the server asked for next. */
  const handleOutcome = (outcome: AuthOutcome) => {
    setStepError('');
    if (outcome.kind === 'mfa') setView({ name: 'mfa', token: outcome.mfaToken });
    else if (outcome.kind === 'mfa_enroll') setView({ name: 'mfa_enroll', token: outcome.mfaToken });
    else if (outcome.kind === 'recovery') setView({ name: 'recovery', codes: outcome.codes, commit: outcome.commit });
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    setFormError('');
    if (hasErrors) return;
    setBusy(true);
    try {
      handleOutcome(await login(email.trim(), password, role));
    } catch (err) {
      if (err instanceof ApiError && err.code === 'CONTACT_NOT_VERIFIED' && err.userId) {
        setView({ name: 'verify', userId: err.userId });
        return;
      }
      const message = err instanceof Error ? err.message : 'Could not sign in.';
      setFormError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  };

  const runStep = async (action: () => Promise<AuthOutcome>) => {
    setBusy(true);
    setStepError('');
    try {
      handleOutcome(await action());
    } catch (err) {
      setStepError(err instanceof Error ? err.message : 'That code was not accepted.');
    } finally {
      setBusy(false);
    }
  };

  const roleLabel = ROLE_LABEL[role];
  const showErr = (key: keyof typeof errors) => (submitted ? errors[key] : '');
  const label = 'block text-sm font-semibold text-[color:var(--fg)]';
  const fieldProps = (key: keyof typeof errors) => ({
    'aria-invalid': !!showErr(key),
    'aria-describedby': showErr(key) ? `login-${key}-error` : undefined,
  });

  const backLink = (
    <button type="button" onClick={goSignin} className="inline-flex items-center gap-1.5 text-sm font-semibold text-[color:var(--link)]">
      <ArrowLeft className="w-4 h-4" aria-hidden="true" /> Back to sign in
    </button>
  );

  const signIn = (
    <>
      <h2 className="pr-14 text-2xl font-bold leading-tight text-[color:var(--fg)]">Sign in</h2>
      <p className="mt-0.5 text-sm text-[color:var(--muted)]">Choose your role to continue.</p>

      <div className="mt-3 space-y-2" role="group" aria-label="Role">
        {ROLES.map((item) => {
          const active = role === item.id;
          return (
            <button
              key={item.id}
              type="button"
              aria-pressed={active}
              onClick={() => pickRole(item.id)}
              className={`flex h-[60px] w-full items-center gap-3 rounded-xl border-2 px-3 text-left transition ${
                active
                  ? 'border-[color:var(--selb)] bg-[color:var(--selbg)]'
                  : 'border-[color:var(--border)] bg-[color:var(--card)] hover:bg-[color:var(--soft)]'
              }`}
            >
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] ${
                  active ? 'bg-[color:var(--accent)] text-[color:var(--accentfg)]' : 'bg-[color:var(--soft)] text-[color:var(--muted)]'
                }`}
              >
                {item.icon}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-semibold leading-tight text-[color:var(--fg)]">{ROLE_LABEL[item.id]}</span>
                <span className="block text-xs leading-snug text-[color:var(--muted)]">{item.blurb}</span>
              </span>
            </button>
          );
        })}
      </div>

      <form onSubmit={submit} noValidate className="mt-3 space-y-3" autoComplete="on">
        {DEMOS && (
          <p className="inline-flex items-center rounded-full border border-[color:var(--selb)] bg-[color:var(--selbg)] px-2.5 py-0.5 text-xs font-semibold text-[color:var(--fg)]">
            Demo mode: sample credentials filled in
          </p>
        )}
        {sessionNotice && (
          <div className="rounded-lg border border-[color:var(--selb)] bg-[color:var(--selbg)] px-3 py-2 text-xs text-[color:var(--fg)]" role="status">
            {sessionNotice}
          </div>
        )}
        <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))' }}>
          <div>
            <label htmlFor="login-name" className={label}>Name</label>
            <input
              id="login-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              className={`${glassInput} mt-1 !min-h-[42px]`}
              {...fieldProps('name')}
            />
            <FieldError id="login-name-error" message={showErr('name')} />
          </div>
          <div>
            <label htmlFor="login-email" className={label}>Work email</label>
            <input
              id="login-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              className={`${glassInput} mt-1 !min-h-[42px]`}
              {...fieldProps('email')}
            />
            <FieldError id="login-email-error" message={showErr('email')} />
          </div>
        </div>
        <div>
          <label htmlFor="login-password" className={label}>Password</label>
          <div className="relative mt-1">
            <input
              id="login-password"
              type={showPassword ? 'text' : 'password'}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              className={`${glassInput} pr-12 !min-h-[42px]`}
              {...fieldProps('password')}
            />
            <button
              type="button"
              onClick={() => setShowPassword((s) => !s)}
              aria-label={showPassword ? 'Hide password' : 'Show password'}
              className="absolute right-0.5 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center text-[color:var(--muted)]"
            >
              {showPassword ? <EyeOff className="w-[18px] h-[18px]" aria-hidden="true" /> : <Eye className="w-[18px] h-[18px]" aria-hidden="true" />}
            </button>
          </div>
          <FieldError id="login-password-error" message={showErr('password')} />
        </div>

        {formError && (
          <div className="flex items-start gap-2 rounded-lg border-[1.5px] border-[color:var(--err)] px-3 py-2 text-sm text-[color:var(--err)]" role="alert">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" aria-hidden="true" /> {formError}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="flex h-[42px] w-full items-center justify-center gap-2 rounded-[10px] bg-[color:var(--accent)] text-[15px] font-semibold text-[color:var(--accentfg)] hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {busy ? (
            <>
              <Loader2 className="w-5 h-5 animate-spin" aria-hidden="true" /> Signing in…
            </>
          ) : (
            <>
              Continue as {roleLabel} <ArrowRight className="w-5 h-5" aria-hidden="true" />
            </>
          )}
        </button>
      </form>

      {role === 'doctor' && (
        <div className="mt-4 flex flex-col gap-2 rounded-xl border-[1.5px] border-[color:var(--selb)] bg-[color:var(--selbg)] px-3.5 py-3">
          <div>
            <p className="text-sm font-bold text-[color:var(--fg)]">New to Digital MSL?</p>
            <p className="text-xs text-[color:var(--muted)]">Doctors can create their own account and choose their therapeutic areas.</p>
          </div>
          <button
            type="button"
            onClick={() => {
              clearSessionNotice();
              setView({ name: 'register' });
            }}
            className="h-10 w-full rounded-[10px] border-[1.5px] border-[color:var(--accent)] bg-[color:var(--card)] text-sm font-semibold text-[color:var(--fg)] hover:bg-[color:var(--soft)]"
          >
            Doctor? Register here
          </button>
        </div>
      )}


      <p className="mt-3 text-xs leading-snug text-[color:var(--muted)]">
        Authorised users only; sign-in activity is recorded in the audit trail.
      </p>
    </>
  );

  return (
    <div className="flex min-h-screen w-full flex-wrap antialiased font-sans">
      {/* Left: story panel, identical in light and dark */}
      <section
        className="flex flex-[7_1_560px] flex-col gap-8 px-6 py-7 login-hero text-onnavy sm:px-10 md:px-[72px] md:py-10"
      >
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3.5">
            <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-brandcyan" aria-hidden="true">
              <MessageCircle className="w-7 h-7 text-brandnavy" />
            </span>
            <div>
              <p className="text-xl font-bold leading-tight">Digital MSL</p>
              <p className="text-sm text-onnavymuted">Therapeutic area science</p>
            </div>
          </div>
          
        </div>

        <div className="flex flex-1 flex-col justify-center">
          <div className="max-w-[600px]">
            <h1 className="text-4xl font-bold leading-[1.1] tracking-[-0.02em] md:text-[46px]">
              Digital MSL for therapeutic-area science, with sources you can check
            </h1>
            <p className="mt-6 text-lg leading-[1.6] text-onnavytext">
              Ask a clinical question and get an answer cited to approved repositories and scientific literature. Questions the AI is
              not confident about go to a Physical MSL.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-onnavymuted">
          <span>For authorised clinical users</span>
          
        </div>
      </section>

      {/* Right: card on themed surface */}
      <section
        className="flex flex-[5_1_420px] items-center justify-center bg-page px-4 py-5 sm:px-6"
      >
        <div className="relative w-full max-w-[460px] rounded-2xl border border-[color:var(--border)] bg-[color:var(--card)] p-4 shadow-[0_10px_30px_rgba(3,4,96,0.10)] sm:p-6">
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
            className="absolute right-4 top-4 flex h-11 w-11 items-center justify-center rounded-full border border-[color:var(--border)] text-[color:var(--fg)] hover:bg-[color:var(--soft)]"
          >
            {theme === 'dark' ? <Sun className="w-5 h-5" aria-hidden="true" /> : <Moon className="w-5 h-5" aria-hidden="true" />}
          </button>

          {view.name === 'signin' && signIn}

          {view.name === 'register' && (
            <>
              {backLink}
              <h2 className="mt-3 pr-14 text-2xl font-bold leading-tight text-[color:var(--fg)]">Register as a Doctor</h2>
              <div className="mt-3">
                <RegisterWizard onBackToSignIn={goSignin} />
              </div>
              <p className="mt-4 text-xs text-[color:var(--muted)]">Physical MSL and Administrator accounts are not created here.</p>
            </>
          )}

          {view.name === 'verify' && (
            <>
              <div className="pr-14">{backLink}</div>
              <div className="mt-3">
                <ContactVerification userId={view.userId} initialCooldown={0} onBackToSignIn={goSignin} />
              </div>
            </>
          )}

          {view.name === 'mfa' && (
            <div className="pt-1 pr-14 sm:pr-0">
              <div className="h-9" aria-hidden="true" />
              <MfaChallenge
                busy={busy}
                error={stepError}
                onCancel={goSignin}
                onSubmit={(code) => runStep(() => completeMfa(view.token, code, role))}
              />
            </div>
          )}

          {view.name === 'mfa_enroll' && (
            <div className="pt-1">
              <div className="h-9" aria-hidden="true" />
              <MfaEnroll
                mfaToken={view.token}
                busy={busy}
                error={stepError}
                onCancel={goSignin}
                onSubmit={(code) => runStep(() => completeMfaEnrollment(view.token, code, role))}
              />
            </div>
          )}

          {view.name === 'recovery' && (
            <div className="pt-1">
              <div className="h-9" aria-hidden="true" />
              <RecoveryCodes codes={view.codes} onDone={view.commit} />
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
