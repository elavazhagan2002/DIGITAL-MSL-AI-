import { useEffect, useState, type FormEvent } from 'react';
import { AlertCircle, CheckCircle2, Copy, KeyRound, Loader2 } from 'lucide-react';
import { mfaSetup, type MfaSetup } from '../services/digitalMslService';
import { glassInput } from './formStyles';

const primaryButton =
  'flex h-[44px] w-full items-center justify-center gap-2 rounded-[10px] bg-[color:var(--accent)] text-sm font-semibold text-[color:var(--accentfg)] hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed';

function ErrorLine({ id, message }: { id: string; message: string }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1.5 flex items-center gap-1 text-xs text-[color:var(--err)]" role="alert">
      <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" /> {message}
    </p>
  );
}

interface CodeFormProps {
  title: string;
  intro: string;
  label: string;
  submitLabel: string;
  busy: boolean;
  error: string;
  allowRecovery?: boolean;
  onSubmit: (code: string) => void;
  onCancel: () => void;
  children?: React.ReactNode;
}

function CodeForm({ title, intro, label, submitLabel, busy, error, allowRecovery, onSubmit, onCancel, children }: CodeFormProps) {
  const [code, setCode] = useState('');
  const [local, setLocal] = useState('');
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const value = code.trim();
    const ok = allowRecovery ? value.length >= 6 : /^\d{6}$/.test(value);
    if (!ok) {
      setLocal(allowRecovery ? 'Enter the 6-digit code, or a recovery code.' : 'Enter the 6-digit code.');
      return;
    }
    setLocal('');
    onSubmit(value);
  };
  const message = local || error;
  return (
    <form onSubmit={submit} noValidate className="space-y-3">
      <div>
        <h3 className="flex items-center gap-2 text-lg font-bold text-[color:var(--fg)]">
          <KeyRound className="w-5 h-5 text-[color:var(--link)]" aria-hidden="true" /> {title}
        </h3>
        <p className="mt-1 text-sm text-[color:var(--muted)]">{intro}</p>
      </div>
      {children}
      <div>
        <label htmlFor="mfa-code" className="block text-sm font-semibold text-[color:var(--fg)]">
          {label}
        </label>
        <input
          id="mfa-code"
          value={code}
          onChange={(e) => setCode(allowRecovery ? e.target.value : e.target.value.replace(/\D/g, '').slice(0, 6))}
          inputMode={allowRecovery ? 'text' : 'numeric'}
          autoComplete="one-time-code"
          autoFocus
          maxLength={allowRecovery ? 24 : 6}
          placeholder={allowRecovery ? '000000' : '000000'}
          aria-invalid={!!message}
          aria-describedby={message ? 'mfa-code-error' : undefined}
          className={`${glassInput} mt-1 tracking-[0.3em] text-center font-mono`}
        />
        <ErrorLine id="mfa-code-error" message={message} />
      </div>
      <button type="submit" disabled={busy} className={primaryButton}>
        {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : null}
        {busy ? 'Checking…' : submitLabel}
      </button>
      <button type="button" onClick={onCancel} className="w-full text-sm font-semibold text-[color:var(--link)] underline">
        Cancel and go back
      </button>
    </form>
  );
}

export function MfaChallenge(props: { busy: boolean; error: string; onSubmit: (code: string) => void; onCancel: () => void }) {
  return (
    <CodeForm
      {...props}
      title="Two-step verification"
      intro="Enter the 6-digit code from your authenticator app, or one of your recovery codes."
      label="Authentication code"
      submitLabel="Verify and sign in"
      allowRecovery
    />
  );
}

export function MfaEnroll({
  mfaToken,
  ...props
}: {
  mfaToken: string;
  busy: boolean;
  error: string;
  onSubmit: (code: string) => void;
  onCancel: () => void;
}) {
  const [setup, setSetup] = useState<MfaSetup | null>(null);
  const [setupError, setSetupError] = useState('');
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let live = true;
    mfaSetup(mfaToken)
      .then((s) => live && setSetup(s))
      .catch((err) => live && setSetupError(err instanceof Error ? err.message : 'Could not start set-up.'));
    return () => {
      live = false;
    };
  }, [mfaToken]);

  const copy = async () => {
    if (!setup) return;
    try {
      await navigator.clipboard.writeText(setup.secret);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable: the secret is visible to copy by hand */
    }
  };

  return (
    <CodeForm
      {...props}
      error={setupError || props.error}
      title="Set up two-step verification"
      intro="Your role requires an authenticator app. Add this account to the app, then enter the 6-digit code it shows."
      label="Code from your app"
      submitLabel="Turn on and sign in"
    >
      {!setup && !setupError ? (
        <p className="flex items-center gap-2 text-sm text-[color:var(--muted)]" role="status">
          <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> Preparing your key…
        </p>
      ) : setup ? (
        <div className="rounded-xl border-[1.5px] border-[color:var(--border)] bg-[color:var(--soft)] p-3 text-sm">
          <p className="text-xs font-semibold uppercase tracking-wide text-[color:var(--muted)]">Secret key</p>
          <div className="mt-1 flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-md bg-[color:var(--field)] px-2 py-1.5 font-mono text-sm text-[color:var(--fg)] select-all">
              {setup.secret}
            </code>
            <button
              type="button"
              onClick={copy}
              className="inline-flex h-9 shrink-0 items-center gap-1 rounded-lg border border-[color:var(--border)] px-2.5 text-xs font-semibold text-[color:var(--fg)] hover:bg-[color:var(--card)]"
            >
              <Copy className="w-3.5 h-3.5" aria-hidden="true" /> {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
          <a href={setup.otpauth_uri} className="mt-2 inline-block text-xs font-semibold text-[color:var(--link)] underline">
            Open in authenticator app
          </a>
        </div>
      ) : null}
    </CodeForm>
  );
}

export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  return (
    <div className="space-y-3">
      <div>
        <h3 className="flex items-center gap-2 text-lg font-bold text-[color:var(--fg)]">
          <CheckCircle2 className="w-5 h-5 text-[color:var(--link)]" aria-hidden="true" /> Save your recovery codes
        </h3>
        <p className="mt-1 text-sm text-[color:var(--muted)]">
          Each code works once if you lose your authenticator app. They are shown only now. Store them somewhere safe.
        </p>
      </div>
      <ul className="grid grid-cols-2 gap-2 rounded-xl border-[1.5px] border-[color:var(--border)] bg-[color:var(--soft)] p-3 font-mono text-sm text-[color:var(--fg)]">
        {codes.map((code) => (
          <li key={code} className="select-all">
            {code}
          </li>
        ))}
      </ul>
      <label className="flex cursor-pointer items-center gap-2 text-sm text-[color:var(--fg)]">
        <input
          type="checkbox"
          checked={saved}
          onChange={(e) => setSaved(e.target.checked)}
          className="h-4 w-4 accent-[color:var(--accent)]"
        />
        I have saved these codes
      </label>
      <button type="button" onClick={onDone} disabled={!saved} className={primaryButton}>
        I saved them, continue
      </button>
    </div>
  );
}
