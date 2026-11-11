import { useEffect, useRef, useState } from 'react';
import { Lock } from 'lucide-react';
import { updatePortalProfile, type DoctorProfile, type PortalUser } from '../../services/digitalMslService';
import { useAuth, ROLE_LABEL } from '../AuthContext';
import { useToast } from '../../contexts/ToastContext';
import { inputClass } from '../../config/pageTheme';
import { FormField } from '../../components/ui/FormField';
import { SaveBar } from '../../components/ui/SaveBar';
import { SaveButton } from '../../components/ui/SaveButton';
import { SettingsCard } from './SettingsCard';

export function ProfileSection({ user, doctor }: { user: PortalUser; doctor: DoctorProfile | null }) {
  const { setUser } = useAuth();
  const toast = useToast();
  const [name, setName] = useState(user.display_name);
  const [saving, setSaving] = useState(false);
  const [touched, setTouched] = useState(false);

  // Follow the signed-in name (for example after a refresh), but never overwrite text the user is editing.
  const synced = useRef(user.display_name);
  useEffect(() => {
    setName((current) => (current === synced.current ? user.display_name : current));
    synced.current = user.display_name;
  }, [user.display_name]);

  const trimmed = name.trim();
  const error = trimmed.length < 2 ? 'Enter your full name (at least 2 characters).' : trimmed.length > 120 ? 'Use 120 characters or fewer.' : '';
  const dirty = trimmed !== user.display_name;

  const save = async () => {
    setTouched(true);
    if (error || !dirty) return;
    setSaving(true);
    try {
      const updated = await updatePortalProfile(user.user_id, trimmed);
      setUser({ ...user, display_name: updated.display_name ?? trimmed });
      toast.success('Profile saved.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save your profile.');
    } finally {
      setSaving(false);
    }
  };

  const readOnly = `${inputClass} cursor-not-allowed bg-soft text-muted pr-10`;
  const device = (() => {
    try {
      return {
        zone: Intl.DateTimeFormat().resolvedOptions().timeZone || '',
        language: typeof navigator !== 'undefined' ? navigator.language : '',
      };
    } catch {
      return { zone: '', language: '' };
    }
  })();
  const locked = (id: string, label: string, value: string, helper?: string) => (
    <FormField id={id} label={label} helper={helper}>
      <div className="relative">
        <input id={id} value={value} readOnly aria-readonly="true" className={readOnly} aria-describedby={helper ? `${id}-help` : undefined} />
        <Lock className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden="true" />
      </div>
    </FormField>
  );

  return (
    <>
      <SettingsCard
        title="Profile"
        description="How you appear to doctors and colleagues. Only your name can be changed here."
        testId="card-profile"
        footer={<SaveButton onClick={save} disabled={!dirty || !!error} saving={saving} testId="profile-save" />}
      >
        <div className="settings-fields">
          <FormField id="profile-name" label="Full name" error={touched || dirty ? error : ''} helper="Shown in replies and notifications.">
            <input
              id="profile-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => setTouched(true)}
              autoComplete="name"
              className={inputClass}
              aria-invalid={!!error && (touched || dirty)}
              aria-describedby={error ? 'profile-name-error' : 'profile-name-help'}
            />
          </FormField>
          {locked('profile-email', 'Email', user.email, 'Contact your administrator to change your email.')}
          {locked('profile-role', 'Role', ROLE_LABEL[user.role])}
          {locked('profile-status', 'Account status', (user.status || 'active').replace(/^./, (c) => c.toUpperCase()))}
          {user.role === 'doctor' && doctor?.mobile
            ? locked('profile-mobile', 'Mobile', doctor.mobile, 'Contact your Physical MSL to change your mobile number.')
            : null}
          {user.role === 'doctor' && doctor?.institution ? locked('profile-institution', 'Institution', doctor.institution) : null}
          {device.zone ? locked('profile-zone', 'Time zone', device.zone, 'Taken from this device.') : null}
          {device.language ? locked('profile-language', 'Language', device.language, 'Taken from this browser.') : null}
        </div>
      </SettingsCard>
      <SaveBar
        dirty={dirty}
        saving={saving}
        canSave={!error}
        onSave={save}
        onDiscard={() => {
          setName(user.display_name);
          setTouched(false);
        }}
      />
    </>
  );
}
