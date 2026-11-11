import { useState, type FormEvent } from 'react';
import { AlertCircle, Check, Circle, Eye, EyeOff, KeyRound, Loader2 } from 'lucide-react';
import { mfaEnable, mfaSetup, updatePortalProfile, type MfaSetup, type PortalUser } from '../../services/digitalMslService';
import { useAuth } from '../AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { inputClass, primaryButton, secondaryButton, statusBadge } from '../../config/pageTheme';
import { checkPassword, MIN_PASSWORD_LENGTH } from '../../utils/registrationValidation';
import { FormField } from '../../components/ui/FormField';
import { SaveBar } from '../../components/ui/SaveBar';
import { SaveButton } from '../../components/ui/SaveButton';
import { SettingsCard } from './SettingsCard';

function PasswordCard() {
  const { user } = useAuth();
  const toast = useToast();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [touched, setTouched] = useState(false);
  const [serverError, setServerError] = useState('');

  if (!user) return null;
  const pw = checkPassword(next);
  const dirty = !!(current || next || confirm);
  const errors = {
    current: !current ? 'Enter your current password.' : serverError,
    next: !next ? 'Enter a new password.' : !pw.valid ? `Use at least ${MIN_PASSWORD_LENGTH} characters with upper and lower case, a digit and a symbol.` : '',
    confirm: confirm !== next ? 'Passwords do not match.' : '',
  };
  const valid = !errors.current && !errors.next && !errors.confirm;
  const reset = () => {
    setCurrent('');
    setNext('');
    setConfirm('');
    setTouched(false);
    setServerError('');
  };

  const save = async () => {
    setTouched(true);
    if (!valid) return;
    setSaving(true);
    setServerError('');
    try {
      await updatePortalProfile(user.user_id, undefined, next, current);
      toast.success('Password changed.');
      reset();
    } catch (err) {
      setServerError(err instanceof Error ? err.message : 'Could not change your password.');
    } finally {
      setSaving(false);
    }
  };

  const type = show ? 'text' : 'password';
  const rules: Array<[string, boolean]> = [
    [`${MIN_PASSWORD_LENGTH}+ characters`, pw.length],
    ['Upper case', pw.upper],
    ['Lower case', pw.lower],
    ['A digit', pw.digit],
    ['A symbol', pw.symbol],
  ];
  const meter = pw.score >= 5 ? 'bg-link' : pw.score >= 3 ? 'bg-warn-fg' : 'bg-err-fg';

  return (
    <>
      <SettingsCard
        title="Change password"
        description="Use a password you do not use anywhere else."
        icon={<KeyRound className="h-4 w-4" aria-hidden="true" />}
        testId="card-password"
        footer={<SaveButton onClick={save} disabled={!dirty} saving={saving} testId="password-save" />}
      >
        <FormField id="pw-current" label="Current password" error={touched ? errors.current : ''}>
          <input id="pw-current" type={type} value={current} onChange={(e) => { setCurrent(e.target.value); setServerError(''); }} autoComplete="current-password" className={inputClass} aria-invalid={touched && !!errors.current} aria-describedby={touched && errors.current ? 'pw-current-error' : undefined} />
        </FormField>
        <FormField id="pw-new" label="New password" error={touched ? errors.next : ''}>
          <div className="relative">
            <input id="pw-new" type={type} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" className={`${inputClass} pr-12`} aria-invalid={touched && !!errors.next} aria-describedby="pw-rules" />
            <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? 'Hide passwords' : 'Show passwords'} className="absolute right-0.5 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center text-muted">
              {show ? <EyeOff className="h-5 w-5" aria-hidden="true" /> : <Eye className="h-5 w-5" aria-hidden="true" />}
            </button>
          </div>
        </FormField>
        <div id="pw-rules" className="space-y-2">
          <div className="flex gap-1" aria-hidden="true">
            {[1, 2, 3, 4, 5].map((n) => (
              <span key={n} className={`h-1.5 flex-1 rounded-full ${n <= pw.score ? meter : 'bg-line'}`} />
            ))}
          </div>
          <p className="text-xs text-muted">Strength: {next ? pw.label : 'Enter a new password'}</p>
          <ul className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs sm:grid-cols-3">
            {rules.map(([label, ok]) => (
              <li key={label} className={`flex items-center gap-1.5 ${ok ? 'text-ok-fg' : 'text-muted'}`}>
                {ok ? <Check className="h-3.5 w-3.5" aria-hidden="true" /> : <Circle className="h-3.5 w-3.5" aria-hidden="true" />}
                <span>{label}<span className="sr-only">{ok ? ' (met)' : ' (not met)'}</span></span>
              </li>
            ))}
          </ul>
        </div>
        <FormField id="pw-confirm" label="Confirm new password" error={touched ? errors.confirm : ''}>
          <input id="pw-confirm" type={type} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" className={inputClass} aria-invalid={touched && !!errors.confirm} aria-describedby={touched && errors.confirm ? 'pw-confirm-error' : undefined} />
        </FormField>
      </SettingsCard>
      <SaveBar dirty={dirty} saving={saving} canSave={valid} onSave={save} onDiscard={reset} />
    </>
  );
}

function TwoStepCard({ user }: { user: PortalUser }) {
  const { setUser } = useAuth();
  const toast = useToast();
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [recovery, setRecovery] = useState<string[]>([]);
  const required = user.role === 'admin';

  const startSetup = async () => {
    setBusy(true);
    setError('');
    try {
      setSetup(await mfaSetup());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start set-up.');
    } finally {
      setBusy(false);
    }
  };

  const enable = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^\d{6}$/.test(code.trim())) {
      setError('Enter the 6-digit code from your app.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const result = await mfaEnable(code.trim());
      setRecovery('recovery_codes' in result ? result.recovery_codes ?? [] : []);
      setUser({ ...user, mfa_enabled: true });
      setSetup(null);
      setCode('');
      toast.success('Two-step verification is on.');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'That code was not accepted.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <SettingsCard
      title="Two-step verification"
      description={required ? 'Required for administrators. Use an authenticator app.' : 'Optional for Physical MSL accounts. Use an authenticator app.'}
      testId="card-two-step"
      badge={<span className={statusBadge(user.mfa_enabled ? 'blue' : required ? 'amber' : 'slate')}>{user.mfa_enabled ? 'On' : required ? 'Required' : 'Off'}</span>}
    >
      {recovery.length > 0 && (
        <div className="rounded-xl border border-selb bg-selbg p-3">
          <p className="text-sm font-semibold text-fg">Save your recovery codes</p>
          <p className="text-xs text-muted">Each works once if you lose your app. They are shown only now.</p>
          <ul className="mt-2 grid grid-cols-2 gap-1 font-mono text-sm text-fg">
            {recovery.map((c) => (
              <li key={c} className="select-all">{c}</li>
            ))}
          </ul>
          <button type="button" className={`${secondaryButton} mt-2`} onClick={() => setRecovery([])}>I saved them</button>
        </div>
      )}
      {!user.mfa_enabled && !setup && (
        <button type="button" className={primaryButton} onClick={startSetup} disabled={busy} data-testid="mfa-start">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />}
          Turn on two-step verification
        </button>
      )}
      {setup && (
        <form onSubmit={enable} className="space-y-3" noValidate>
          <p className="text-sm text-muted">Add this key to your authenticator app, then enter the 6-digit code it shows.</p>
          <code className="block break-all rounded-lg bg-soft px-3 py-2 font-mono text-sm text-fg select-all">{setup.secret}</code>
          <a href={setup.otpauth_uri} className="text-sm font-semibold text-link underline">Open in authenticator app</a>
          <FormField id="settings-mfa-code" label="Code from your app" error={error}>
            <input id="settings-mfa-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" aria-invalid={!!error} className={`${inputClass} max-w-[12rem] font-mono tracking-[0.3em]`} />
          </FormField>
          <button type="submit" className={primaryButton} disabled={busy}>
            {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null} Turn on
          </button>
        </form>
      )}
      {error && !setup && (
        <p className="flex items-center gap-1.5 text-xs text-err-fg" role="alert">
          <AlertCircle className="h-3.5 w-3.5" aria-hidden="true" /> {error}
        </p>
      )}
    </SettingsCard>
  );
}

export function SecuritySection({ user }: { user: PortalUser }) {
  return (
    <>
      <PasswordCard />
      {user.role === 'msl' || user.role === 'admin' ? <TwoStepCard user={user} /> : null}
    </>
  );
}
