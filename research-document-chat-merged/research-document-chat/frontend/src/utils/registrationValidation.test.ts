import { describe, expect, it } from 'vitest';
import {
  checkPassword,
  normalizeMobile,
  normalizeRegistrationNumber,
  pickAutosaveAccount,
  resolveOption,
  validateAccount,
  validateAreas,
  validateConsents,
  validateProfessional,
} from './registrationValidation';
import { isVerified, verificationInfo } from './verificationStatus';
import { readCookie } from '../services/apiTransport';

describe('checkPassword (12+ characters)', () => {
  it('requires length, upper, lower, digit and symbol', () => {
    expect(checkPassword('Short1A!').valid).toBe(false);
    expect(checkPassword('Str0ng&Pass1').valid).toBe(true); // exactly 12
    expect(checkPassword('Str0ng&Pass').valid).toBe(false); // 11
    expect(checkPassword('alllowercase123!').valid).toBe(false);
    expect(checkPassword('NoDigitsHere!!!').valid).toBe(false);
    expect(checkPassword('Str0ng&LongPass').label).toBe('Strong');
  });
  it('rejects passwords over 128 characters', () => {
    expect(checkPassword(`Aa1!${'x'.repeat(130)}`).valid).toBe(false);
  });
});

describe('normalizeMobile', () => {
  it('joins country code and national digits', () => {
    expect(normalizeMobile('+91', '97010 38776')).toBe('+919701038776');
  });
  it('drops a leading trunk zero', () => {
    expect(normalizeMobile('+91', '09701038776')).toBe('+919701038776');
  });
  it('respects a number typed with its own +', () => {
    expect(normalizeMobile('+91', '+44 7700 900123')).toBe('+447700900123');
  });
  it('returns empty for no digits', () => {
    expect(normalizeMobile('+91', '')).toBe('');
  });
});

describe('normalizeRegistrationNumber', () => {
  it('trims, strips inner spaces and upper-cases', () => {
    expect(normalizeRegistrationNumber('  kmc/ 12345 ')).toBe('KMC/12345');
  });
});

describe('resolveOption', () => {
  it('uses the free text only for Other', () => {
    expect(resolveOption('MBBS', 'ignored')).toBe('MBBS');
    expect(resolveOption('Other', '  Fellowship in VR ')).toBe('Fellowship in VR');
  });
});

describe('validateAccount', () => {
  const ok = {
    fullName: 'Arjun Mehta',
    email: 'arjun@hospital.org',
    mobile: '+919701038776',
    password: 'Str0ng&LongPass',
    confirmPassword: 'Str0ng&LongPass',
  };
  it('accepts valid input', () => expect(validateAccount(ok)).toEqual({}));
  it('flags mismatch and bad mobile', () => {
    const errors = validateAccount({ ...ok, mobile: '12345', confirmPassword: 'nope' });
    expect(errors.mobile).toBeTruthy();
    expect(errors.confirmPassword).toBeTruthy();
  });
  it('limits the name to 100 characters and rejects a 10-char password', () => {
    expect(validateAccount({ ...ok, fullName: 'x'.repeat(101) }).fullName).toBeTruthy();
    expect(validateAccount({ ...ok, fullName: 'A' }).fullName).toBeTruthy();
    expect(validateAccount({ ...ok, password: 'Str0ng&Pass', confirmPassword: 'Str0ng&Pass' }).password).toBeTruthy();
  });
  it('rejects a bad email', () => {
    expect(validateAccount({ ...ok, email: 'not-an-email' }).email).toBeTruthy();
  });
});

describe('validateProfessional', () => {
  const ok = {
    registrationNumber: 'KMC/12345',
    council: 'karnataka',
    registrationYear: '2010',
    nmrId: '',
    qualification: 'MBBS',
    qualificationOther: '',
    highestQualification: 'MS',
    highestQualificationOther: '',
    speciality: 'Ophthalmology',
    institution: 'City Eye Hospital',
    city: 'Bengaluru',
    state: 'Karnataka',
    pincode: '560001',
  };
  it('accepts valid input', () => expect(validateProfessional(ok, 2026)).toEqual({}));
  it('requires every field', () => {
    const errors = validateProfessional(
      { ...ok, registrationNumber: '', council: '', registrationYear: '', qualification: '', highestQualification: '', speciality: '', institution: '', city: '', state: '', pincode: '' },
      2026,
    );
    for (const key of ['registrationNumber', 'council', 'registrationYear', 'qualification', 'highestQualification', 'speciality', 'institution', 'city', 'state', 'pincode']) {
      expect(errors[key as keyof typeof errors], key).toBeTruthy();
    }
  });
  it('checks the registration number characters and length', () => {
    expect(validateProfessional({ ...ok, registrationNumber: 'AB' }, 2026).registrationNumber).toBeTruthy();
    expect(validateProfessional({ ...ok, registrationNumber: 'AB#12345' }, 2026).registrationNumber).toBeTruthy();
    expect(validateProfessional({ ...ok, registrationNumber: ' kmc 12/345-a ' }, 2026).registrationNumber).toBeUndefined();
  });
  it('bounds the registration year', () => {
    expect(validateProfessional({ ...ok, registrationYear: '1949' }, 2026).registrationYear).toBeTruthy();
    expect(validateProfessional({ ...ok, registrationYear: '1950' }, 2026).registrationYear).toBeUndefined();
    expect(validateProfessional({ ...ok, registrationYear: '2027' }, 2026).registrationYear).toBeTruthy();
    expect(validateProfessional({ ...ok, registrationYear: '20x0' }, 2026).registrationYear).toBeTruthy();
  });
  it('validates an optional NMR id only when present', () => {
    expect(validateProfessional({ ...ok, nmrId: 'ab' }, 2026).nmrId).toBeTruthy();
    expect(validateProfessional({ ...ok, nmrId: 'NMR-123456' }, 2026).nmrId).toBeUndefined();
  });
  it('requires free text when Other is chosen', () => {
    expect(validateProfessional({ ...ok, qualification: 'Other' }, 2026).qualificationOther).toBeTruthy();
    expect(validateProfessional({ ...ok, highestQualification: 'Other', highestQualificationOther: 'Fellowship' }, 2026)).toEqual({});
  });
});

describe('validateConsents', () => {
  const all = { terms_privacy: true, hcp_declaration: true, whatsapp_messaging: true, email_messaging: true };
  it('needs all four boxes', () => {
    expect(validateConsents(all)).toBeNull();
    for (const key of Object.keys(all) as Array<keyof typeof all>) {
      expect(validateConsents({ ...all, [key]: false }), key).toBeTruthy();
    }
  });
});

describe('validateAreas', () => {
  it('needs 1 to 3', () => {
    expect(validateAreas([])).toBeTruthy();
    expect(validateAreas(['a'])).toBeNull();
    expect(validateAreas(['a', 'b', 'c'])).toBeNull();
    expect(validateAreas(['a', 'b', 'c', 'd'])).toBeTruthy();
  });
});

describe('autosave', () => {
  it('never includes the password', () => {
    const saved = pickAutosaveAccount({ fullName: 'A B', email: 'a@b.co', mobile: '+911234567890', password: 'secret', confirmPassword: 'secret' });
    expect(JSON.stringify(saved)).not.toContain('secret');
    expect(saved.fullName).toBe('A B');
  });
});

describe('verification status mapping', () => {
  it('only VERIFIED is cleared', () => {
    expect(isVerified('VERIFIED')).toBe(true);
    for (const s of ['PENDING_CONTACT_VERIFICATION', 'PENDING_REGISTRATION_VERIFICATION', 'REJECTED', 'NEEDS_INFO', 'verified', '', undefined]) {
      expect(isVerified(s as string | undefined), String(s)).toBe(false);
    }
  });
  it('labels the enum values', () => {
    expect(verificationInfo('NEEDS_INFO').label).toBe('More information needed');
    expect(verificationInfo('REJECTED').tone).toBe('rose');
    expect(verificationInfo('whatever').label).toBe('Not verified');
  });
});

describe('readCookie', () => {
  it('reads the CSRF cookie out of a cookie string', () => {
    expect(readCookie('dmsl_csrf', 'a=1; dmsl_csrf=tok%3D1; b=2')).toBe('tok=1');
    expect(readCookie('missing', 'a=1')).toBe('');
  });
});
