import type { VerificationStatus } from '../services/digitalMslService';

export type StatusTone = 'blue' | 'cyan' | 'amber' | 'rose' | 'slate';

export interface StatusInfo {
  label: string;
  tone: StatusTone;
  /** True only for VERIFIED: the doctor may use Digital MSL. */
  cleared: boolean;
}

const INFO: Record<VerificationStatus, StatusInfo> = {
  PENDING_CONTACT_VERIFICATION: { label: 'Verify email and phone', tone: 'amber', cleared: false },
  PENDING_REGISTRATION_VERIFICATION: { label: 'Registration under review', tone: 'cyan', cleared: false },
  VERIFIED: { label: 'Verified', tone: 'blue', cleared: true },
  REJECTED: { label: 'Rejected', tone: 'rose', cleared: false },
  NEEDS_INFO: { label: 'More information needed', tone: 'amber', cleared: false },
};

/** Unknown or missing values are treated as not cleared. */
export function verificationInfo(status: string | undefined | null): StatusInfo {
  return INFO[(status || '') as VerificationStatus] ?? { label: 'Not verified', tone: 'slate', cleared: false };
}

export function isVerified(status: string | undefined | null): boolean {
  return verificationInfo(status).cleared;
}
