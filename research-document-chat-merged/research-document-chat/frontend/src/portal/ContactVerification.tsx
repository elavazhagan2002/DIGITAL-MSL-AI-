import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { AlertCircle, CheckCircle2, Loader2, Mail, Smartphone } from 'lucide-react';
import { resendContactCode, verifyContact } from '../services/digitalMslService';
import { glassInput } from './formStyles';

type Channel = 'email' | 'mobile';

interface Props {
  userId: string;
  emailMasked?: string;
  mobileMasked?: string;
  devCodes?: { email?: string; mobile?: string };
  /** Seconds before "Resend code" can be used. 60 right after registration, 0 when arriving from sign-in. */
  initialCooldown?: number;
  onBackToSignIn: () => void;
}

const COOLDOWN_SECONDS = 60;

export function ContactVerification({
  userId,
  emailMasked,
  mobileMasked,
  devCodes: initialDevCodes,
  initialCooldown = COOLDOWN_SECONDS,
  onBackToSignIn,
}: Props) {
  const [verified, setVerified] = useState<Record<Channel, boolean>>({ email: false, mobile: false });
  const [codes, setCodes] = useState<Record<Channel, string>>({ email: '', mobile: '' });
  const [busy, setBusy] = useState<Channel | ''>('');
  const [errors, setErrors] = useState<Record<Channel, string>>({ email: '', mobile: '' });
  const [notice, setNotice] = useState<Record<Channel, string>>({ email: '', mobile: '' });
  const [cooldown, setCooldown] = useState<Record<Channel, number>>({ email: initialCooldown, mobile: initialCooldown });
  const [devCodes, setDevCodes] = useState(initialDevCodes);

  useEffect(() => {
    const timer = setInterval(() => {
      setCooldown((c) => ({ email: Math.max(0, c.email - 1), mobile: Math.max(0, c.mobile - 1) }));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const done = verified.email && verified.mobile;

  const verify = useCallback(
    async (channel: Channel) => {
      const code = codes[channel].trim();
      if (!/^\d{6}$/.test(code)) {
        setErrors((e) => ({ ...e, [channel]: 'Enter the 6-digit code.' }));
        return;
      }
      setBusy(channel);
      setErrors((e) => ({ ...e, [channel]: '' }));
      try {
        const result = await verifyContact(userId, channel, code);
        setVerified({ email: !!result.email_verified, mobile: !!result.mobile_verified });
        setCodes((c) => ({ ...c, [channel]: '' }));
      } catch (err) {
        setErrors((e) => ({ ...e, [channel]: err instanceof Error ? err.message : 'Could not verify the code.' }));
      } finally {
        setBusy('');
      }
    },
    [codes, userId],
  );

  const resend = async (channel: Channel) => {
    setBusy(channel);
    setErrors((e) => ({ ...e, [channel]: '' }));
    try {
      const result = await resendContactCode(userId, channel);
      if (result.dev_codes) setDevCodes((d) => ({ ...d, ...result.dev_codes }));
      setCooldown((c) => ({ ...c, [channel]: COOLDOWN_SECONDS }));
      setNotice((n) => ({ ...n, [channel]: 'A new code has been sent.' }));
    } catch (err) {
      setErrors((e) => ({ ...e, [channel]: err instanceof Error ? err.message : 'Could not send a new code.' }));
    } finally {
      setBusy('');
    }
  };

  const panel = (channel: Channel, title: string, target: string | undefined, icon: ReactNode) => {
    const ok = verified[channel];
    const errId = `verify-${channel}-error`;
    return (
      <div className="rounded-xl border-[1.5px] border-[color:var(--border)] bg-[color:var(--card)] p-3.5">
        <div className="flex items-center gap-2 text-sm font-semibold text-[color:var(--fg)]">
          <span className="text-[color:var(--link)]" aria-hidden="true">{icon}</span>
          {title}
          {ok && (
            <span className="ml-auto inline-flex items-center gap-1 text-xs font-semibold text-[color:var(--link)]">
              <CheckCircle2 className="w-4 h-4" aria-hidden="true" /> Verified
            </span>
          )}
        </div>
        {target && <p className="mt-0.5 text-xs text-[color:var(--muted)]">Code sent to {target}</p>}
        {!ok && (
          <>
            <div className="mt-2.5 flex gap-2">
              <label htmlFor={`verify-${channel}-code`} className="sr-only">
                {title} code
              </label>
              <input
                id={`verify-${channel}-code`}
                value={codes[channel]}
                onChange={(e) => setCodes((c) => ({ ...c, [channel]: e.target.value.replace(/\D/g, '').slice(0, 6) }))}
                onKeyDown={(e) => e.key === 'Enter' && verify(channel)}
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="000000"
                aria-invalid={!!errors[channel]}
                aria-describedby={errors[channel] ? errId : undefined}
                className={`${glassInput} tracking-[0.35em] text-center font-mono`}
              />
              <button
                type="button"
                onClick={() => verify(channel)}
                disabled={busy !== ''}
                className="shrink-0 rounded-[10px] bg-[color:var(--accent)] px-4 text-sm font-semibold text-[color:var(--accentfg)] hover:opacity-90 disabled:opacity-60"
              >
                {busy === channel ? <Loader2 className="w-4 h-4 animate-spin" aria-label="Verifying" /> : 'Verify'}
              </button>
            </div>
            {errors[channel] && (
              <p id={errId} className="mt-1.5 flex items-center gap-1 text-xs text-[color:var(--err)]" role="alert">
                <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" /> {errors[channel]}
              </p>
            )}
            <div className="mt-1.5 flex items-center justify-between text-xs">
              <span className="text-[color:var(--muted)]" role="status">{notice[channel]}</span>
              <button
                type="button"
                onClick={() => resend(channel)}
                disabled={cooldown[channel] > 0 || busy !== ''}
                className="font-semibold text-[color:var(--link)] underline disabled:no-underline disabled:opacity-60"
              >
                {cooldown[channel] > 0 ? `Resend code in ${cooldown[channel]}s` : initialCooldown ? 'Resend code' : 'Send code'}
              </button>
            </div>
          </>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-3.5">
      <div>
        <h3 className="text-lg font-bold text-[color:var(--fg)]">Check your email and phone to verify</h3>
        <p className="mt-1 text-sm text-[color:var(--muted)]">
          Enter the 6-digit codes we sent. They expire in a few minutes. Your account is activated once both are verified.
        </p>
      </div>

      {(devCodes?.email || devCodes?.mobile) && (
        <div className="rounded-lg border-[1.5px] border-dashed border-[color:var(--selb)] bg-[color:var(--selbg)] px-3 py-2 text-xs text-[color:var(--fg)]" role="note">
          <p className="font-semibold uppercase tracking-wide">Development only</p>
          <p className="mt-0.5">
            Email code <span className="font-mono font-semibold">{devCodes.email || '—'}</span> · Mobile code{' '}
            <span className="font-mono font-semibold">{devCodes.mobile || '—'}</span>
          </p>
        </div>
      )}

      {panel('email', 'Email', emailMasked, <Mail className="w-4 h-4" />)}
      {panel('mobile', 'Mobile (WhatsApp)', mobileMasked, <Smartphone className="w-4 h-4" />)}

      {done ? (
        <div className="rounded-xl border-[1.5px] border-[color:var(--selb)] bg-[color:var(--selbg)] p-3.5" role="status">
          <p className="flex items-center gap-2 text-sm font-semibold text-[color:var(--fg)]">
            <CheckCircle2 className="w-4 h-4 text-[color:var(--link)]" aria-hidden="true" />
            Contact verified. Your registration is now with our team for verification.
          </p>
          <p className="mt-1 text-xs text-[color:var(--muted)]">
            You can sign in now, but clinical answers unlock once your registration is verified. You can upload your registration
            certificate in Settings after signing in to speed this up.
          </p>
        </div>
      ) : null}

      <button
        type="button"
        onClick={onBackToSignIn}
        className={`h-[42px] w-full rounded-[10px] text-sm font-semibold ${
          done
            ? 'bg-[color:var(--accent)] text-[color:var(--accentfg)] hover:opacity-90'
            : 'border-[1.5px] border-[color:var(--accent)] bg-[color:var(--card)] text-[color:var(--fg)] hover:bg-[color:var(--soft)]'
        }`}
      >
        Back to sign in
      </button>
    </div>
  );
}
