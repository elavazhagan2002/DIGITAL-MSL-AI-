import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { AlertCircle, ArrowLeft, ArrowRight, Check, Eye, EyeOff, Loader2, ShieldCheck } from 'lucide-react';
import { ContactVerification } from './ContactVerification';
import { glassInput } from './formStyles';
import {
  fetchRegisterOptions,
  portalRegister,
  type ConsentType,
  type CouncilOption,
  type RegisterOptions,
  type RegisterResult,
} from '../services/digitalMslService';
import {
  CONSENT_KEYS,
  OTHER_OPTION,
  checkPassword,
  normalizeMobile,
  normalizeRegistrationNumber,
  resolveOption,
  validateAccount,
  validateAreas,
  validateConsents,
  validateProfessional,
  type AccountFields,
  type ConsentChoices,
  type FieldErrors,
  type ProfessionalFields,
} from '../utils/registrationValidation';

const STEPS = ['Account', 'Professional', 'Therapeutic areas', 'Consent'] as const;

const DRAFT_KEY = 'dmsl_register_draft';

const COUNTRY_CODES = [
  { code: '+91', label: 'IN +91' },
  { code: '+1', label: 'US/CA +1' },
  { code: '+44', label: 'UK +44' },
  { code: '+971', label: 'AE +971' },
  { code: '+65', label: 'SG +65' },
  { code: '+61', label: 'AU +61' },
  { code: '+49', label: 'DE +49' },
  { code: '+33', label: 'FR +33' },
  { code: '+94', label: 'LK +94' },
  { code: '+880', label: 'BD +880' },
  { code: '+977', label: 'NP +977' },
  { code: '+92', label: 'PK +92' },
];

const CONSENT_FALLBACK: Record<ConsentType, { title: string; text: string }> = {
  terms_privacy: { title: 'Terms of use and Privacy policy', text: 'I have read and accept the Terms of use and the Privacy policy. [LEGAL TO APPROVE]' },
  hcp_declaration: { title: 'Healthcare professional declaration', text: 'I declare that I am a registered medical practitioner and that the details I provided are true. [LEGAL TO APPROVE]' },
  whatsapp_messaging: { title: 'I agree to receive messages from Digital MSL on WhatsApp', text: 'You can withdraw this at any time in Settings, or by replying STOP. [LEGAL TO APPROVE]' },
  email_messaging: { title: 'I agree to receive messages from Digital MSL by email', text: 'You can withdraw this at any time in Settings, or with the unsubscribe link. [LEGAL TO APPROVE]' },
};

interface FieldProps {
  id: string;
  label: string;
  error?: string;
  hint?: string;
  required?: boolean;
  children: (aria: { id: string; 'aria-invalid': boolean; 'aria-describedby'?: string }) => ReactNode;
}

function Field({ id, label, error, hint, required = true, children }: FieldProps) {
  const describedBy = error ? `${id}-error` : hint ? `${id}-hint` : undefined;
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-semibold text-[color:var(--fg)]">
        {label}
        {required ? (
          <span className="text-[color:var(--err)]" aria-hidden="true"> *</span>
        ) : (
          <span className="font-normal text-[color:var(--muted)]"> (optional)</span>
        )}
      </label>
      <div className="mt-1">{children({ id, 'aria-invalid': !!error, 'aria-describedby': describedBy })}</div>
      {error ? (
        <p id={`${id}-error`} className="mt-1 flex items-center gap-1 text-xs text-[color:var(--err)]" role="alert">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" /> {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="mt-1 text-xs text-[color:var(--muted)]">{hint}</p>
      ) : null}
    </div>
  );
}

const emptyAccount: AccountFields = { fullName: '', email: '', mobile: '', password: '', confirmPassword: '' };
const emptyProfessional: ProfessionalFields = {
  registrationNumber: '',
  council: '',
  registrationYear: '',
  nmrId: '',
  qualification: '',
  qualificationOther: '',
  highestQualification: '',
  highestQualificationOther: '',
  speciality: 'Ophthalmology',
  institution: '',
  city: '',
  state: '',
  pincode: '',
};
const emptyConsents: ConsentChoices = {
  terms_privacy: false,
  hcp_declaration: false,
  whatsapp_messaging: false,
  email_messaging: false,
};

/** Only non-sensitive fields survive a reload, and only for this browser tab. */
interface Draft {
  step: number;
  fullName: string;
  email: string;
  countryCode: string;
  council: string;
  registrationYear: string;
  qualification: string;
  highestQualification: string;
  speciality: string;
  institution: string;
  city: string;
  state: string;
  pincode: string;
  picked: string[];
  defaultTa: string;
}

function readDraft(): Partial<Draft> {
  try {
    return JSON.parse(sessionStorage.getItem(DRAFT_KEY) || '{}') as Partial<Draft>;
  } catch {
    return {};
  }
}

function councilList(options: RegisterOptions): CouncilOption[] {
  return options.councils.map((c) => (typeof c === 'string' ? { id: c, name: c } : c));
}

export function RegisterWizard({ onBackToSignIn }: { onBackToSignIn: () => void }) {
  const draft = useMemo(readDraft, []);
  const [options, setOptions] = useState<RegisterOptions | null>(null);
  const [loadError, setLoadError] = useState('');
  const [step, setStep] = useState(Math.min(draft.step ?? 0, 3));
  const [showErrors, setShowErrors] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState('');
  const [registered, setRegistered] = useState<RegisterResult | null>(null);

  const [account, setAccount] = useState<AccountFields>({ ...emptyAccount, fullName: draft.fullName ?? '', email: draft.email ?? '' });
  const [countryCode, setCountryCode] = useState(draft.countryCode ?? '+91');
  const [nationalMobile, setNationalMobile] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [prof, setProf] = useState<ProfessionalFields>({
    ...emptyProfessional,
    council: draft.council ?? '',
    registrationYear: draft.registrationYear ?? '',
    qualification: draft.qualification ?? '',
    highestQualification: draft.highestQualification ?? '',
    speciality: draft.speciality ?? 'Ophthalmology',
    institution: draft.institution ?? '',
    city: draft.city ?? '',
    state: draft.state ?? '',
    pincode: draft.pincode ?? '',
  });
  const [picked, setPicked] = useState<string[]>(draft.picked ?? []);
  const [defaultTa, setDefaultTa] = useState(draft.defaultTa ?? '');
  const [consents, setConsents] = useState<ConsentChoices>(emptyConsents);
  const [termsOpen, setTermsOpen] = useState(false);

  const loadOptions = () => {
    setLoadError('');
    fetchRegisterOptions()
      .then(setOptions)
      .catch((err) => setLoadError(err instanceof Error ? err.message : 'Could not load registration options.'));
  };

  useEffect(loadOptions, []);

  // Session-only autosave of non-sensitive fields. Never the password, mobile, registration number or any code.
  useEffect(() => {
    const snapshot: Draft = {
      step,
      fullName: account.fullName,
      email: account.email,
      countryCode,
      council: prof.council,
      registrationYear: prof.registrationYear,
      qualification: prof.qualification,
      highestQualification: prof.highestQualification,
      speciality: prof.speciality,
      institution: prof.institution,
      city: prof.city,
      state: prof.state,
      pincode: prof.pincode,
      picked,
      defaultTa,
    };
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(snapshot));
    } catch {
      /* storage unavailable */
    }
  }, [step, account.fullName, account.email, countryCode, prof, picked, defaultTa]);

  const mobile = normalizeMobile(countryCode, nationalMobile);
  const accountErrors = useMemo(() => validateAccount({ ...account, mobile }), [account, mobile]);
  const profErrors = useMemo(() => validateProfessional(prof), [prof]);
  const areaError = validateAreas(picked);
  const consentError = validateConsents(consents);
  const pw = checkPassword(account.password);

  const stepValid = [
    Object.keys(accountErrors).length === 0,
    Object.keys(profErrors).length === 0,
    !areaError,
    !consentError,
  ];

  const err = <T,>(errors: FieldErrors<T>, key: keyof T) => (showErrors ? errors[key] : undefined);

  const next = () => {
    if (!stepValid[step]) {
      setShowErrors(true);
      return;
    }
    setShowErrors(false);
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  };

  const back = () => {
    setShowErrors(false);
    setServerError('');
    setStep((s) => Math.max(s - 1, 0));
  };

  const toggleArea = (id: string) => {
    setPicked((prev) => {
      if (prev.includes(id)) {
        const rest = prev.filter((x) => x !== id);
        if (defaultTa === id) setDefaultTa(rest[0] || '');
        return rest;
      }
      if (prev.length >= 3) return prev;
      if (!defaultTa) setDefaultTa(id);
      return [...prev, id];
    });
  };

  const submit = async () => {
    if (!stepValid.every(Boolean) || !options) {
      setShowErrors(true);
      return;
    }
    const council = councilList(options).find((c) => c.id === prof.council);
    setBusy(true);
    setServerError('');
    try {
      const result = await portalRegister({
        email: account.email.trim().toLowerCase(),
        display_name: account.fullName.trim(),
        password: account.password,
        mobile,
        registration_number: normalizeRegistrationNumber(prof.registrationNumber),
        registration_council: council?.name ?? prof.council,
        council_id: council?.id,
        registration_year: Number(prof.registrationYear),
        qualification: resolveOption(prof.qualification, prof.qualificationOther),
        highest_qualification: resolveOption(prof.highestQualification, prof.highestQualificationOther),
        speciality: prof.speciality,
        institution: prof.institution.trim(),
        city: prof.city.trim(),
        state: prof.state.trim(),
        pincode: prof.pincode.trim(),
        nmr_id: prof.nmrId.trim() || undefined,
        repository_ids: picked,
        default_repository_id: defaultTa || picked[0],
        consents,
        consent_version: options.consent_documents.version,
      });
      try {
        sessionStorage.removeItem(DRAFT_KEY);
      } catch {
        /* ignore */
      }
      // Drop the password from memory as soon as it has been sent.
      setAccount((a) => ({ ...a, password: '', confirmPassword: '' }));
      setRegistered(result);
    } catch (e) {
      setServerError(e instanceof Error ? e.message : 'Could not create your account.');
    } finally {
      setBusy(false);
    }
  };

  if (registered) {
    return (
      <ContactVerification
        userId={registered.user_id}
        emailMasked={registered.email_masked}
        mobileMasked={registered.mobile_masked}
        devCodes={registered.dev_codes}
        onBackToSignIn={onBackToSignIn}
      />
    );
  }

  if (loadError) {
    return (
      <div className="rounded-xl border-[1.5px] border-[color:var(--err)] p-4 text-sm text-[color:var(--err)]" role="alert">
        <p className="flex items-center gap-1.5">
          <AlertCircle className="w-4 h-4" aria-hidden="true" /> {loadError}
        </p>
        <button type="button" className="mt-2 text-xs font-semibold underline" onClick={loadOptions}>
          Retry
        </button>
      </div>
    );
  }
  if (!options) {
    return (
      <div className="flex items-center gap-2 py-10 justify-center text-sm text-[color:var(--muted)]" role="status">
        <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> Loading registration form…
      </div>
    );
  }

  const councils = councilList(options);
  const setP = (key: keyof ProfessionalFields) => (v: string) => setProf((p) => ({ ...p, [key]: v }));
  const text = (
    aria: Record<string, unknown>,
    value: string,
    onChange: (v: string) => void,
    extra: Record<string, string> = {},
  ) => <input {...aria} value={value} onChange={(e) => onChange(e.target.value)} className={glassInput} {...extra} />;
  const select = (aria: Record<string, unknown>, value: string, onChange: (v: string) => void, items: string[], placeholder: string) => (
    <select {...aria} value={value} onChange={(e) => onChange(e.target.value)} className={glassInput}>
      <option value="">{placeholder}</option>
      {items.map((item) => (
        <option key={item} value={item}>
          {item}
        </option>
      ))}
    </select>
  );
  const qualificationItems = [...options.qualifications.filter((q) => q !== OTHER_OPTION), OTHER_OPTION];

  const consentItems = CONSENT_KEYS.map((key) => {
    const server = options.consent_documents.items.find((i) => i.type === key);
    return { type: key as ConsentType, title: server?.title || CONSENT_FALLBACK[key].title, text: server?.text || CONSENT_FALLBACK[key].text };
  });

  return (
    <div>
      <ol className="flex items-center gap-1.5 mb-4" aria-label="Registration progress">
        {STEPS.map((label, i) => (
          <li key={label} className="flex-1" aria-current={i === step ? 'step' : undefined}>
            <div className={`h-1 rounded-full ${i <= step ? 'bg-[color:var(--accent)]' : 'bg-[color:var(--border)]'}`} />
            <p className={`mt-1 text-[10px] font-semibold uppercase tracking-wider ${i === step ? 'text-[color:var(--fg)]' : 'text-[color:var(--muted)]'}`}>
              {i < step ? <Check className="inline w-3 h-3 mr-0.5" aria-hidden="true" /> : null}
              {i + 1}. {label}
            </p>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <div className="space-y-3">
          <Field id="reg-name" label="Full name (as on your registration certificate)" error={err(accountErrors, 'fullName')}>
            {(a) => text(a, account.fullName, (v) => setAccount((s) => ({ ...s, fullName: v })), { autoComplete: 'name', placeholder: 'Dr. Arjun Mehta' })}
          </Field>
          <Field id="reg-email" label="Work email" error={err(accountErrors, 'email')} hint="We will send a verification code to this address.">
            {(a) => text(a, account.email, (v) => setAccount((s) => ({ ...s, email: v })), { type: 'email', autoComplete: 'email', placeholder: 'name@hospital.org' })}
          </Field>
          <Field id="reg-mobile" label="Mobile number (WhatsApp enabled)" error={err(accountErrors, 'mobile')} hint={mobile ? `Will be saved as ${mobile}. We will send an OTP.` : 'We will send an OTP to verify it.'}>
            {(a) => (
              <div className="flex gap-2">
                <select value={countryCode} onChange={(e) => setCountryCode(e.target.value)} className={`${glassInput} !w-28 shrink-0`} aria-label="Country code">
                  {COUNTRY_CODES.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.label}
                    </option>
                  ))}
                </select>
                <input
                  {...a}
                  value={nationalMobile}
                  onChange={(e) => setNationalMobile(e.target.value)}
                  inputMode="tel"
                  autoComplete="tel-national"
                  placeholder="98765 43210"
                  className={glassInput}
                />
              </div>
            )}
          </Field>
          <Field id="reg-password" label="Password" error={err(accountErrors, 'password')}>
            {(a) => (
              <>
                <div className="relative">
                  <input
                    {...a}
                    type={showPassword ? 'text' : 'password'}
                    value={account.password}
                    onChange={(e) => setAccount((s) => ({ ...s, password: e.target.value }))}
                    autoComplete="new-password"
                    placeholder="At least 12 characters"
                    className={`${glassInput} pr-11`}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((s) => !s)}
                    className="absolute right-1 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center text-[color:var(--muted)]"
                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                  >
                    {showPassword ? <EyeOff className="w-4 h-4" aria-hidden="true" /> : <Eye className="w-4 h-4" aria-hidden="true" />}
                  </button>
                </div>
                <div className="mt-2 flex gap-1" aria-hidden="true">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <span
                      key={n}
                      className={`h-1 flex-1 rounded-full ${
                        n <= pw.score ? (pw.score >= 5 ? 'bg-[color:var(--accent)]' : pw.score >= 3 ? 'bg-[color:var(--link)]' : 'bg-[color:var(--err)]') : 'bg-[color:var(--border)]'
                      }`}
                    />
                  ))}
                </div>
                <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]" aria-label={`Password strength: ${account.password ? pw.label : 'not entered'}`}>
                  {(
                    [
                      ['12+ characters', pw.length],
                      ['A-Z', pw.upper],
                      ['a-z', pw.lower],
                      ['0-9', pw.digit],
                      ['symbol', pw.symbol],
                    ] as Array<[string, boolean]>
                  ).map(([label, ok]) => (
                    <li key={label} className={ok ? 'text-[color:var(--link)] font-semibold' : 'text-[color:var(--muted)]'}>
                      {ok ? '✓' : '○'} {label}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Field>
          <Field id="reg-confirm" label="Confirm password" error={err(accountErrors, 'confirmPassword')}>
            {(a) => (
              <input
                {...a}
                type={showPassword ? 'text' : 'password'}
                value={account.confirmPassword}
                onChange={(e) => setAccount((s) => ({ ...s, confirmPassword: e.target.value }))}
                autoComplete="new-password"
                className={glassInput}
              />
            )}
          </Field>
        </div>
      )}

      {step === 1 && (
        <div className="space-y-3">
          <div className="flex gap-2.5 rounded-lg border-[1.5px] border-[color:var(--selb)] bg-[color:var(--selbg)] p-2.5 text-xs text-[color:var(--fg)]">
            <ShieldCheck className="w-4 h-4 shrink-0 mt-0.5 text-[color:var(--link)]" aria-hidden="true" />
            <p>
              These details are checked against the National Medical Register (NMR). Your account stays in verification until they are
              confirmed. You can upload your registration certificate in Settings after signing in.
            </p>
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field id="reg-number" label="Medical registration number" error={err(profErrors, 'registrationNumber')}>
              {(a) => text(a, prof.registrationNumber, setP('registrationNumber'), { autoCapitalize: 'characters', placeholder: 'e.g. KMC/12345' })}
            </Field>
            <Field id="reg-year" label="Year of registration" error={err(profErrors, 'registrationYear')}>
              {(a) => text(a, prof.registrationYear, setP('registrationYear'), { inputMode: 'numeric', maxLength: '4', placeholder: 'YYYY' })}
            </Field>
          </div>
          <Field id="reg-council" label="Registering council" error={err(profErrors, 'council')}>
            {(a) => (
              <select {...a} value={prof.council} onChange={(e) => setP('council')(e.target.value)} className={glassInput}>
                <option value="">Select council</option>
                {councils.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </select>
            )}
          </Field>
          <Field id="reg-nmr" label="NMC Unique ID (NMR ID)" required={false} error={err(profErrors, 'nmrId')} hint="Speeds up verification if you have one.">
            {(a) => text(a, prof.nmrId, setP('nmrId'))}
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field id="reg-qual" label="Basic qualification" error={err(profErrors, 'qualification')}>
              {(a) => select(a, prof.qualification, setP('qualification'), qualificationItems, 'Select')}
            </Field>
            <Field id="reg-hqual" label="Highest qualification" error={err(profErrors, 'highestQualification')}>
              {(a) => select(a, prof.highestQualification, setP('highestQualification'), qualificationItems, 'Select')}
            </Field>
            {prof.qualification === OTHER_OPTION && (
              <Field id="reg-qual-other" label="Basic qualification (other)" error={err(profErrors, 'qualificationOther')}>
                {(a) => text(a, prof.qualificationOther, setP('qualificationOther'))}
              </Field>
            )}
            {prof.highestQualification === OTHER_OPTION && (
              <Field id="reg-hqual-other" label="Highest qualification (other)" error={err(profErrors, 'highestQualificationOther')}>
                {(a) => text(a, prof.highestQualificationOther, setP('highestQualificationOther'))}
              </Field>
            )}
          </div>
          <Field id="reg-speciality" label="Primary speciality" error={err(profErrors, 'speciality')}>
            {(a) => select(a, prof.speciality, setP('speciality'), options.specialities, 'Select speciality')}
          </Field>
          <Field id="reg-institution" label="Hospital or clinic" error={err(profErrors, 'institution')}>
            {(a) => text(a, prof.institution, setP('institution'), { autoComplete: 'organization' })}
          </Field>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Field id="reg-city" label="City" error={err(profErrors, 'city')}>
              {(a) => text(a, prof.city, setP('city'), { autoComplete: 'address-level2' })}
            </Field>
            <Field id="reg-state" label="State" error={err(profErrors, 'state')}>
              {(a) => text(a, prof.state, setP('state'), { autoComplete: 'address-level1' })}
            </Field>
            <Field id="reg-pin" label="PIN code" error={err(profErrors, 'pincode')}>
              {(a) => text(a, prof.pincode, setP('pincode'), { autoComplete: 'postal-code', inputMode: 'numeric' })}
            </Field>
          </div>
        </div>
      )}

      {step === 2 && (
        <fieldset className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <legend className="text-sm font-semibold text-[color:var(--fg)]">Therapeutic areas</legend>
            <span className="text-xs text-[color:var(--muted)]" aria-live="polite">
              {picked.length} of 3 selected
            </span>
          </div>
          <p className="text-xs text-[color:var(--muted)]">Pick up to 3. Your default area is used first when you ask a question.</p>
          <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
            {options.therapeutic_areas.map((area) => {
              const soon = area.coming_soon === true || area.enabled === false;
              const on = picked.includes(area.repository_id);
              const blocked = soon || (!on && picked.length >= 3);
              const isDefault = defaultTa === area.repository_id;
              return (
                <div
                  key={area.repository_id}
                  className={`flex items-center gap-3 rounded-[10px] border-[1.5px] px-3 py-2 transition ${
                    on ? 'border-[color:var(--selb)] bg-[color:var(--selbg)]' : 'border-[color:var(--border)] bg-[color:var(--card)]'
                  } ${blocked ? 'opacity-60' : ''}`}
                >
                  <label className={`flex flex-1 min-w-0 items-center gap-3 ${blocked ? 'cursor-not-allowed' : 'cursor-pointer'}`}>
                    <input
                      type="checkbox"
                      checked={on}
                      disabled={blocked}
                      onChange={() => toggleArea(area.repository_id)}
                      className="h-4 w-4 shrink-0 accent-[color:var(--accent)]"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-semibold text-[color:var(--fg)]">{area.name}</span>
                      {area.description ? <span className="block truncate text-[11px] text-[color:var(--muted)]">{area.description}</span> : null}
                    </span>
                  </label>
                  {soon && (
                    <span className="shrink-0 rounded-full border border-[color:var(--border)] bg-[color:var(--soft)] px-2.5 py-0.5 text-[11px] font-semibold text-[color:var(--muted)]">
                      Coming soon
                    </span>
                  )}
                  {on &&
                    (isDefault ? (
                      <span className="shrink-0 rounded-full bg-[color:var(--accent)] px-2.5 py-0.5 text-[11px] font-semibold text-[color:var(--accentfg)]">Default</span>
                    ) : (
                      <button type="button" onClick={() => setDefaultTa(area.repository_id)} className="shrink-0 text-xs font-semibold text-[color:var(--link)] underline">
                        Make default
                      </button>
                    ))}
                </div>
              );
            })}
            {!options.therapeutic_areas.length && (
              <p className="text-xs text-[color:var(--muted)] py-6 text-center">No therapeutic areas are available yet. Ask an administrator to create one.</p>
            )}
          </div>
          {showErrors && areaError && (
            <p className="flex items-center gap-1 text-xs text-[color:var(--err)]" role="alert">
              <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" /> {areaError}
            </p>
          )}
        </fieldset>
      )}

      {step === 3 && (
        <fieldset className="space-y-2.5">
          <legend className="text-sm font-semibold text-[color:var(--fg)]">Consent</legend>
          <p className="text-xs text-[color:var(--muted)]">
            Consent text version {options.consent_documents.version}. All four are required and none is pre-selected.
          </p>
          {consentItems.map((item) => {
            const isTerms = item.type === 'terms_privacy';
            const expanded = !isTerms || termsOpen;
            return (
              <div key={item.type} className="rounded-xl border-[1.5px] border-[color:var(--border)] bg-[color:var(--card)] p-3">
                <label className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={consents[item.type]}
                    onChange={(e) => setConsents((c) => ({ ...c, [item.type]: e.target.checked }))}
                    aria-describedby={`consent-${item.type}`}
                    className="mt-0.5 h-4 w-4 shrink-0 accent-[color:var(--accent)]"
                  />
                  <span className="text-sm font-semibold text-[color:var(--fg)]">
                    {item.title}
                    <span className="text-[color:var(--err)]" aria-hidden="true"> *</span>
                  </span>
                </label>
                {isTerms && (
                  <button
                    type="button"
                    onClick={() => setTermsOpen((o) => !o)}
                    aria-expanded={termsOpen}
                    className="ml-7 mt-1 text-xs font-semibold text-[color:var(--link)] underline"
                  >
                    {termsOpen ? 'Hide the full text' : 'Read the full text'}
                  </button>
                )}
                <p id={`consent-${item.type}`} className={`ml-7 mt-1 text-xs leading-relaxed text-[color:var(--muted)] ${expanded ? '' : 'sr-only'}`}>
                  {item.text}
                </p>
              </div>
            );
          })}
          {showErrors && consentError && (
            <p className="flex items-center gap-1 text-xs text-[color:var(--err)]" role="alert">
              <AlertCircle className="w-3.5 h-3.5" aria-hidden="true" /> {consentError}
            </p>
          )}
          {serverError && (
            <div className="flex items-start gap-2 rounded-lg border-[1.5px] border-[color:var(--err)] px-3 py-2 text-xs text-[color:var(--err)]" role="alert">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden="true" /> {serverError}
            </div>
          )}
        </fieldset>
      )}

      <div className="mt-5 flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={back}
          disabled={step === 0 || busy}
          className="inline-flex h-[42px] items-center gap-1.5 text-sm font-semibold text-[color:var(--muted)] hover:text-[color:var(--fg)] disabled:opacity-0"
        >
          <ArrowLeft className="w-4 h-4" aria-hidden="true" /> Back
        </button>
        {step < STEPS.length - 1 ? (
          <button
            type="button"
            onClick={next}
            className="inline-flex h-[42px] items-center gap-2 rounded-[10px] bg-[color:var(--accent)] px-6 text-sm font-semibold text-[color:var(--accentfg)] hover:opacity-90"
          >
            Continue <ArrowRight className="w-4 h-4" aria-hidden="true" />
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={busy || !stepValid.every(Boolean)}
            className="inline-flex h-[42px] items-center gap-2 rounded-[10px] bg-[color:var(--accent)] px-6 text-sm font-semibold text-[color:var(--accentfg)] hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : null}
            {busy ? 'Creating account…' : 'Create Doctor account'}
          </button>
        )}
      </div>
    </div>
  );
}
