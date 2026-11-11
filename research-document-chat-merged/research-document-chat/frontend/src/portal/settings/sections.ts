import type { PortalUser } from '../../services/digitalMslService';

export type SectionId =
  | 'profile'
  | 'security'
  | 'notifications'
  | 'preferences'
  | 'activity'
  | 'wording'
  | 'myareas'
  | 'areas'
  | 'consent'
  | 'registration'
  | 'users'
  | 'adminareas'
  | 'ikm'
  | 'notice'
  | 'scope'
  | 'integrations'
  | 'audit'
  | 'environment';

export interface SectionDef {
  id: SectionId;
  label: string;
  /** management tables use the whole main column instead of the 880px content width */
  wide?: boolean;
}

const COMMON: SectionDef[] = [
  { id: 'profile', label: 'Profile' },
  { id: 'security', label: 'Security' },
  { id: 'notifications', label: 'Notifications' },
  { id: 'preferences', label: 'Preferences' },
  { id: 'activity', label: 'Activity' },
];

export function sectionsFor(role: PortalUser['role']): SectionDef[] {
  const pick = (...ids: SectionId[]) => ids.map((id) => COMMON.find((c) => c.id === id)!);
  if (role === 'doctor') {
    return [
      ...pick('profile', 'security'),
      { id: 'areas', label: 'Therapeutic areas' },
      { id: 'consent', label: 'Consent' },
      { id: 'registration', label: 'Registration' },
      ...pick('notifications', 'preferences', 'activity'),
    ];
  }
  if (role === 'msl') {
    return [
      ...pick('profile', 'security'),
      { id: 'wording', label: 'Reply wording' },
      { id: 'myareas', label: 'My areas' },
      ...pick('notifications', 'preferences', 'activity'),
    ];
  }
  return [
    { id: 'users', label: 'Users and roles', wide: true },
    { id: 'adminareas', label: 'Therapeutic areas', wide: true },
    { id: 'ikm', label: 'Repositories and IKM' },
    { id: 'notice', label: 'Compliance notice' },
    { id: 'scope', label: 'Scope policy' },
    { id: 'integrations', label: 'Integrations' },
    { id: 'audit', label: 'Audit log', wide: true },
    { id: 'environment', label: 'Environment' },
    ...pick('profile', 'security', 'notifications', 'preferences', 'activity'),
  ];
}

export const DESCRIPTION: Record<PortalUser['role'], string> = {
  doctor: 'Your profile, therapeutic areas, consent and security.',
  msl: 'Your account, security and the wording used when you reply to doctors.',
  admin: 'Users, therapeutic areas, IKM, notice, integrations, audit and your own account.',
};
