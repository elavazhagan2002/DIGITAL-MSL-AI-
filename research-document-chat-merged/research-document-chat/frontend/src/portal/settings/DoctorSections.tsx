import { useRef, useState, type ReactNode } from 'react';
import { FileUp, Loader2, Mail, MessageCircle, Star } from 'lucide-react';
import {
  setDoctorDefaultTa,
  updateConsents,
  uploadCertificate,
  validateCertificateFile,
  type PortalUser,
} from '../../services/digitalMslService';
import { useAuth } from '../AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { secondaryButton, statusBadge } from '../../config/pageTheme';
import { verificationInfo } from '../../utils/verificationStatus';
import { formatDateTimeIST } from '../../utils/formatDateTimeIST';
import { Modal } from '../../components/ui/Modal';
import { Toggle } from '../../components/ui/Toggle';
import { SettingsCard } from './SettingsCard';
import type { DoctorMe } from './useDoctorMe';

function Loading({ label }: { label: string }) {
  return (
    <div className="space-y-2" role="status" aria-busy="true" aria-label={label}>
      {[0, 1].map((i) => <div key={i} className="h-14 animate-pulse rounded-lg bg-soft" />)}
    </div>
  );
}

function LoadError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-err-b bg-err-bg px-4 py-3 text-sm text-err-fg" role="alert">
      <span>{message}</span>
      <button type="button" className={secondaryButton} onClick={onRetry}>Retry</button>
    </div>
  );
}

// ------------------------------------------------------------------ therapeutic areas (Make default)
export function DoctorAreasSection({ user, me }: { user: PortalUser; me: DoctorMe }) {
  const { setUser } = useAuth();
  const toast = useToast();
  const [busy, setBusy] = useState('');

  const makeDefault = async (repositoryId: string) => {
    setBusy(repositoryId);
    try {
      const updated = await setDoctorDefaultTa(user.user_id, repositoryId);
      setUser({ ...user, ...updated, verification_status: user.verification_status });
      me.reload();
      toast.success('Default therapeutic area updated.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not change the default area.');
    } finally {
      setBusy('');
    }
  };

  return (
    <SettingsCard
      title="Therapeutic areas"
      description="Areas you chose at registration. Your default area is used first when you ask a question."
      testId="card-areas"
    >
      {me.loading && me.areas.length === 0 ? <Loading label="Loading areas" /> : null}
      {me.error ? <LoadError message={me.error} onRetry={me.reload} /> : null}
      {!me.loading && !me.error && me.areas.length === 0 ? (
        <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">
          You have not chosen a therapeutic area yet. Contact your administrator.
        </p>
      ) : null}
      {me.areas.length > 0 ? (
        <ul className="space-y-3">
          {me.areas.map((area) => (
            <li key={area.repository_id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-soft px-4 py-3">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-semibold text-fg">
                  {area.is_default ? <Star className="h-4 w-4 fill-current text-link" aria-hidden="true" /> : null}
                  {area.name}
                </p>
                {area.description ? <p className="mt-0.5 line-clamp-2 text-xs text-muted">{area.description}</p> : null}
              </div>
              {area.is_default ? (
                <span className={statusBadge('blue')}>Default</span>
              ) : (
                <button
                  type="button"
                  className={secondaryButton}
                  onClick={() => makeDefault(area.repository_id)}
                  disabled={busy !== ''}
                  aria-label={`Make ${area.name} your default area`}
                >
                  {busy === area.repository_id ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null} Make default
                </button>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </SettingsCard>
  );
}

// ------------------------------------------------------------------ consent and communication
type Channel = 'whatsapp_messaging' | 'email_messaging';

export function ConsentSection({ me }: { me: DoctorMe }) {
  const toast = useToast();
  const { user, setUser } = useAuth();
  const [saving, setSaving] = useState<Channel | 'both' | null>(null);
  const [confirm, setConfirm] = useState(false);
  const consents = me.consents;

  const apply = async (changes: Partial<Record<Channel, boolean>>, key: Channel | 'both', message: string) => {
    setSaving(key);
    try {
      const next = await updateConsents(changes);
      me.setConsents(next);
      if (user) setUser({ ...user, consents: { whatsapp_messaging: !!next.whatsapp_messaging?.granted, email_messaging: !!next.email_messaging?.granted } });
      toast.success(message);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not update consent.');
    } finally {
      setSaving(null);
      setConfirm(false);
    }
  };

  const row = (type: Channel, label: string, icon: ReactNode) => {
    const state = consents?.[type];
    const granted = !!state?.granted;
    return (
      <div className="flex items-center justify-between gap-3 py-3" data-testid={`consent-${type}`}>
        <div className="flex min-w-0 items-start gap-3">
          <span className="rounded-lg bg-selbg p-2 text-link">{icon}</span>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-fg">{label}</p>
            <p className="text-xs text-muted">
              {granted ? 'Granted' : state?.updated_at ? 'Withdrawn' : 'Not given'}
              {state?.updated_at ? ` on ${formatDateTimeIST(state.updated_at)}` : ''}
              {state?.version ? ` · notice version ${state.version}` : ''}
            </p>
          </div>
        </div>
        <Toggle
          checked={granted}
          label={`${label} consent`}
          disabled={!consents || saving !== null}
          onChange={(next) => apply({ [type]: next }, type, `${label} ${next ? 'enabled' : 'withdrawn'}.`)}
        />
      </div>
    );
  };

  const anyGranted = !!consents?.whatsapp_messaging?.granted || !!consents?.email_messaging?.granted;

  return (
    <>
      <SettingsCard
        title="Consent and communication"
        description="Choose how your Physical MSL and Digital MSL may contact you. Changes apply immediately."
        testId="card-consent"
        footer={
          <button type="button" className={secondaryButton} onClick={() => setConfirm(true)} disabled={!consents || !anyGranted || saving !== null} data-testid="withdraw-consent">
            Withdraw consent
          </button>
        }
      >
        {me.loading && !consents ? <Loading label="Loading consent" /> : null}
        {me.error ? <LoadError message={me.error} onRetry={me.reload} /> : null}
        {consents ? (
          <div className="divide-y divide-line">
            {row('whatsapp_messaging', 'WhatsApp messages', <MessageCircle className="h-4 w-4" aria-hidden="true" />)}
            {row('email_messaging', 'Email', <Mail className="h-4 w-4" aria-hidden="true" />)}
            {(
              [
                ['Terms of use and Privacy policy', consents.terms_privacy ?? consents.data_processing],
                ['Healthcare professional declaration', consents.hcp_declaration],
              ] as const
            ).map(([label, state]) => (
              <div key={label} className="flex items-center justify-between gap-3 py-3">
                <div>
                  <p className="text-sm font-semibold text-fg">{label}</p>
                  <p className="text-xs text-muted">
                    Required to use Digital MSL{state?.updated_at ? ` · ${formatDateTimeIST(state.updated_at)}` : ''}
                    {state?.version ? ` · notice version ${state.version}` : ''}
                  </p>
                </div>
                <span className={statusBadge(state?.granted ? 'blue' : 'slate')}>{state?.granted ? 'Accepted' : 'Not on file'}</span>
              </div>
            ))}
          </div>
        ) : null}
      </SettingsCard>
      <Modal open={confirm} onClose={() => setConfirm(false)} title="Withdraw messaging consent?">
        <p className="text-sm text-fg">
          Digital MSL and your Physical MSL will stop sending you messages on WhatsApp and by email. You can switch either channel back on at any time.
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={secondaryButton} onClick={() => setConfirm(false)}>Cancel</button>
          <button
            type="button"
            className={secondaryButton}
            disabled={saving !== null}
            onClick={() => apply({ whatsapp_messaging: false, email_messaging: false }, 'both', 'Messaging consent withdrawn.')}
          >
            {saving === 'both' ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null} Withdraw
          </button>
        </div>
      </Modal>
    </>
  );
}

// ------------------------------------------------------------------ registration details (read-only)
function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 py-3 sm:grid-cols-3 sm:gap-4">
      <dt className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</dt>
      <dd className="break-words text-sm text-fg sm:col-span-2">{value || 'Not provided'}</dd>
    </div>
  );
}

export function RegistrationSection({ me, user }: { me: DoctorMe; user: PortalUser }) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [certError, setCertError] = useState('');
  const doctor = me.profile;
  const info = verificationInfo(doctor?.verification_status ?? user.verification_status);

  const onCertificate = async (file: File | undefined) => {
    if (!file) return;
    const problem = validateCertificateFile(file);
    if (problem) {
      setCertError(problem);
      return;
    }
    setCertError('');
    setUploading(true);
    try {
      const certificate = await uploadCertificate(file);
      me.setProfile((d) => (d ? { ...d, certificate } : d));
      toast.success('Certificate uploaded. It is scanned before an administrator can open it.');
    } catch (err) {
      setCertError(err instanceof Error ? err.message : 'Could not upload the certificate.');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <>
      <SettingsCard
        title="Registration details"
        description="Checked against the National Medical Register by the verification team. Read only."
        testId="card-registration"
        badge={<span className={statusBadge(info.tone)}>{info.label}</span>}
      >
        {me.loading && !doctor ? <Loading label="Loading registration" /> : null}
        {me.error ? <LoadError message={me.error} onRetry={me.reload} /> : null}
        {doctor ? (
          <>
            <dl className="divide-y divide-line">
              <Row label="Registration no." value={doctor.registration_number} />
              <Row label="Council" value={doctor.registration_council} />
              <Row label="Year" value={doctor.registration_year ? String(doctor.registration_year) : ''} />
              <Row label="NMC Unique ID" value={doctor.nmr_id || ''} />
              <Row label="Qualification" value={doctor.highest_qualification || doctor.qualification} />
              <Row label="Speciality" value={doctor.speciality} />
              <Row label="Institution" value={doctor.institution} />
              <Row label="Location" value={[doctor.city, doctor.state].filter(Boolean).join(', ')} />
            </dl>
            {doctor.rejection_reason || doctor.verification_notes ? (
              <p className="rounded-lg bg-soft px-3 py-2 text-sm text-muted">
                Note from the verification team: {doctor.rejection_reason || doctor.verification_notes}
              </p>
            ) : null}
          </>
        ) : null}
        {!me.loading && !me.error && !doctor ? <p className="text-sm text-muted">No registration details on file.</p> : null}
      </SettingsCard>

      {doctor ? (
        <SettingsCard
          title="Registration certificate"
          description={
            doctor.certificate?.present
              ? `Uploaded${doctor.certificate.scan_status ? ` (scan: ${doctor.certificate.scan_status})` : ''}. Upload again to replace it.`
              : 'Optional. PDF, PNG or JPEG up to 5 MB. Helps the team verify you faster.'
          }
          testId="card-certificate"
        >
          <input
            ref={fileRef}
            id="certificate-file"
            type="file"
            accept="application/pdf,image/png,image/jpeg"
            className="sr-only"
            onChange={(e) => onCertificate(e.target.files?.[0])}
          />
          <label
            htmlFor="certificate-file"
            className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-[10px] border-[1.5px] border-line px-4 text-sm font-semibold text-fg hover:bg-soft focus-within:ring-2 focus-within:ring-selb"
          >
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <FileUp className="h-4 w-4" aria-hidden="true" />}
            {uploading ? 'Uploading…' : 'Upload certificate'}
          </label>
          {certError ? <p className="text-xs text-err-fg" role="alert">{certError}</p> : null}
        </SettingsCard>
      ) : null}
    </>
  );
}
