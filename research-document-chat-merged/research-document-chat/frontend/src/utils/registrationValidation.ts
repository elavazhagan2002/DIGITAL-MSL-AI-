/** Client-side mirrors of the server's doctor-registration rules. The backend remains the authority. */

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
export const MOBILE_RE = /^\+[1-9]\d{9,14}$/;
export const REG_NUMBER_RE = /^[A-Z0-9/-]+$/;
export const PINCODE_RE = /^[A-Za-z0-9 -]{3,10}$/;
export const NMR_ID_RE = /^[A-Za-z0-9-]{3,40}$/;
export const MIN_REGISTRATION_YEAR = 1950;
export const MIN_PASSWORD_LENGTH = 12;
export const MAX_PASSWORD_LENGTH = 128;
export const OTHER_OPTION = 'Other';

export interface PasswordCheck {
  length: boolean;
  upper: boolean;
  lower: boolean;
  digit: boolean;
  symbol: boolean;
  /** 0-5: number of satisfied rules */
  score: number;
  valid: boolean;
  label: 'Too weak' | 'Weak' | 'Fair' | 'Good' | 'Strong';
}

export function checkPassword(password: string): PasswordCheck {
  const length = password.length >= MIN_PASSWORD_LENGTH && password.length <= MAX_PASSWORD_LENGTH;
  const upper = /[A-Z]/.test(password);
  const lower = /[a-z]/.test(password);
  const digit = /\d/.test(password);
  const symbol = /[^A-Za-z0-9\s]/.test(password);
  const score = [length, upper, lower, digit, symbol].filter(Boolean).length;
  const labels: PasswordCheck['label'][] = ['Too weak', 'Too weak', 'Weak', 'Fair', 'Good', 'Strong'];
  return { length, upper, lower, digit, symbol, score, valid: score === 5, label: labels[score] };
}

/** Digits of the national number are kept; the result is `+<countrycode><digits>`. */
export function normalizeMobile(countryCode: string, national: string): string {
  const cc = countryCode.replace(/\D/g, '');
  let digits = national.replace(/\D/g, '');
  if (national.trim().startsWith('+')) return `+${digits}`;
  if (digits.startsWith('00')) return `+${digits.slice(2)}`;
  digits = digits.replace(/^0+/, '');
  return cc && digits ? `+${cc}${digits}` : '';
}

/** Trim, collapse inner spaces and upper-case, as the server stores it. */
export function normalizeRegistrationNumber(value: string): string {
  return value.replace(/\s+/g, '').toUpperCase();
}

/** A select value, or the free text when 'Other' was chosen. */
export function resolveOption(value: string, other: string): string {
  return value === OTHER_OPTION ? other.trim() : value;
}

export interface AccountFields {
  fullName: string;
  email: string;
  mobile: string;
  password: string;
  confirmPassword: string;
}

export interface ProfessionalFields {
  registrationNumber: string;
  /** Council id (or name when the server lists plain names). */
  council: string;
  registrationYear: string;
  nmrId: string;
  qualification: string;
  qualificationOther: string;
  highestQualification: string;
  highestQualificationOther: string;
  speciality: string;
  institution: string;
  city: string;
  state: string;
  pincode: string;
}

export type FieldErrors<T> = Partial<Record<keyof T, string>>;

export function validateAccount(f: AccountFields): FieldErrors<AccountFields> {
  const errors: FieldErrors<AccountFields> = {};
  const name = f.fullName.trim();
  if (name.length < 2 || name.length > 100) errors.fullName = 'Enter your full name as on your registration certificate (2–100 characters).';
  if (!EMAIL_RE.test(f.email.trim())) errors.email = 'Enter a valid email address.';
  if (!MOBILE_RE.test(f.mobile)) errors.mobile = 'Enter a valid mobile number with country code.';
  const pw = checkPassword(f.password);
  if (!pw.valid) errors.password = `Use at least ${MIN_PASSWORD_LENGTH} characters with upper and lower case, a digit and a symbol.`;
  if (f.confirmPassword !== f.password) errors.confirmPassword = 'Passwords do not match.';
  return errors;
}

export function validateProfessional(
  f: ProfessionalFields,
  currentYear = new Date().getFullYear(),
): FieldErrors<ProfessionalFields> {
  const errors: FieldErrors<ProfessionalFields> = {};
  const reg = normalizeRegistrationNumber(f.registrationNumber);
  if (reg.length < 3 || reg.length > 30) errors.registrationNumber = 'Registration number must be 3–30 characters.';
  else if (!REG_NUMBER_RE.test(reg)) errors.registrationNumber = 'Use letters, digits, / and - only.';
  if (!f.council) errors.council = 'Select the council that issued your registration.';
  const year = Number(f.registrationYear);
  if (!/^\d{4}$/.test(f.registrationYear.trim()) || year < MIN_REGISTRATION_YEAR || year > currentYear) {
    errors.registrationYear = `Enter a year between ${MIN_REGISTRATION_YEAR} and ${currentYear}.`;
  }
  if (f.nmrId.trim() && !NMR_ID_RE.test(f.nmrId.trim())) errors.nmrId = 'Use 3–40 letters, digits or hyphens.';
  if (!f.qualification) errors.qualification = 'Select your basic qualification.';
  else if (f.qualification === OTHER_OPTION && f.qualificationOther.trim().length < 2) {
    errors.qualificationOther = 'Enter your basic qualification.';
  }
  if (!f.highestQualification) errors.highestQualification = 'Select your highest qualification.';
  else if (f.highestQualification === OTHER_OPTION && f.highestQualificationOther.trim().length < 2) {
    errors.highestQualificationOther = 'Enter your highest qualification.';
  }
  if (!f.speciality) errors.speciality = 'Select your primary speciality.';
  if (f.institution.trim().length < 2) errors.institution = 'Enter your hospital or clinic.';
  if (f.city.trim().length < 2) errors.city = 'Enter your city.';
  if (f.state.trim().length < 2) errors.state = 'Enter your state.';
  if (!PINCODE_RE.test(f.pincode.trim())) errors.pincode = 'Enter your PIN code (3–10 letters or digits).';
  return errors;
}

export type ConsentKey = 'terms_privacy' | 'hcp_declaration' | 'whatsapp_messaging' | 'email_messaging';

export type ConsentChoices = Record<ConsentKey, boolean>;

export const CONSENT_KEYS: ConsentKey[] = ['terms_privacy', 'hcp_declaration', 'whatsapp_messaging', 'email_messaging'];

/** All four boxes are required, and none may be pre-ticked. */
export function validateConsents(c: ConsentChoices): string | null {
  if (!c.terms_privacy) return 'Accept the Terms of use and Privacy policy to continue.';
  if (!c.hcp_declaration) return 'The healthcare professional declaration is required.';
  if (!c.whatsapp_messaging) return 'Consent to WhatsApp messages is required.';
  if (!c.email_messaging) return 'Consent to email messages is required.';
  return null;
}

export function validateAreas(ids: string[]): string | null {
  if (ids.length < 1) return 'Select at least one therapeutic area.';
  if (ids.length > 3) return 'Select at most three therapeutic areas.';
  return null;
}

/** Fields that are safe to keep in sessionStorage between steps. Never password or codes. */
export const AUTOSAVE_ACCOUNT_KEYS: Array<keyof AccountFields> = ['fullName', 'email'];

export function pickAutosaveAccount(account: AccountFields): Partial<AccountFields> {
  const out: Partial<AccountFields> = {};
  for (const key of AUTOSAVE_ACCOUNT_KEYS) out[key] = account[key];
  return out;
}
