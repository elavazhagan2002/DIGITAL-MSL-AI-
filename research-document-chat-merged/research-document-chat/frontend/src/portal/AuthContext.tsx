import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  portalLogin,
  portalLoginMfa,
  mfaEnable,
  portalLogout,
  logoutAllDevices,
  SESSION_EXPIRED_EVENT,
  type LoginResult,
  type PortalSession,
  type PortalUser,
} from '../services/digitalMslService';
import { setMemoryToken } from '../services/apiTransport';

/**
 * The session itself lives in an HttpOnly cookie. Only the non-sensitive user object is kept in
 * localStorage, so the UI can render before GET /portal/me confirms the session.
 */
const STORAGE_KEY = 'dmsl_portal_session';

type PortalRole = PortalUser['role'];

/** What the login page should do next. */
export type AuthOutcome =
  | { kind: 'signed_in' }
  | { kind: 'mfa'; mfaToken: string }
  | { kind: 'mfa_enroll'; mfaToken: string }
  /** MFA was just enabled: show these codes once, then call `commit()` to enter the app. */
  | { kind: 'recovery'; codes: string[]; commit: () => void };

interface AuthContextValue {
  user: PortalUser | null;
  /** Session id from the sign-in response (display / correlation only; the cookie authenticates). */
  sessionId: string;
  /** expectedRole: the role card the user picked; a mismatch ends the new session and throws. */
  login: (email: string, password: string, expectedRole?: PortalRole) => Promise<AuthOutcome>;
  completeMfa: (mfaToken: string, code: string, expectedRole?: PortalRole) => Promise<AuthOutcome>;
  completeMfaEnrollment: (mfaToken: string, code: string, expectedRole?: PortalRole) => Promise<AuthOutcome>;
  setUser: (user: PortalUser) => void;
  logout: () => void;
  logoutEverywhere: () => Promise<void>;
  /** Set when the server ended the session (expiry / revocation) so the login page can say why. */
  sessionNotice: string;
  clearSessionNotice: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

function readSession(): PortalUser | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PortalUser;
    if (!parsed?.user_id || !parsed?.role) return null;
    return parsed;
  } catch {
    return null;
  }
}

function persistUser(user: PortalUser) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
  } catch {
    /* storage unavailable: the cookie still carries the session */
  }
}

function clearStoredUser() {
  try {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem('dmsl_login_session_id'); // left over from older builds
  } catch {
    /* ignore */
  }
}

export const ROLE_LABEL: Record<PortalRole, string> = {
  admin: 'Administrator',
  doctor: 'Doctor',
  msl: 'Physical MSL',
};

function roleMismatchMessage(expected: PortalRole): string {
  const label = ROLE_LABEL[expected];
  return `This account is not ${/^[aeiou]/i.test(label) ? 'an' : 'a'} ${label} account.`;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<PortalUser | null>(() => readSession());
  const [sessionId, setSessionId] = useState('');
  const [sessionNotice, setSessionNotice] = useState('');

  useEffect(() => {
    const onExpired = () => {
      setMemoryToken('');
      setUserState(null);
      setSessionId('');
      setSessionNotice('Your session has ended. Please sign in again.');
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, []);

  /** Turn a sign-in step result into the next action; sessions are role-checked before they are kept. */
  const accept = useCallback(
    async (result: LoginResult, expectedRole?: PortalRole, recoveryCodes?: string[]): Promise<AuthOutcome> => {
      if (result.kind === 'mfa') return { kind: 'mfa', mfaToken: result.mfa_token };
      if (result.kind === 'mfa_enroll') return { kind: 'mfa_enroll', mfaToken: result.mfa_token };
      const token = result.access_token || result.session_id || '';
      if (expectedRole && result.user.role !== expectedRole) {
        // Valid credentials for another role: close that session, never keep it.
        setMemoryToken(token);
        await portalLogout().catch(() => undefined);
        setMemoryToken('');
        throw new Error(roleMismatchMessage(expectedRole));
      }
      const commit = () => {
        setMemoryToken(token);
        persistUser(result.user);
        setUserState(result.user);
        setSessionId(result.session_id || '');
        setSessionNotice('');
      };
      const codes = recoveryCodes ?? result.recovery_codes;
      if (codes && codes.length) {
        setMemoryToken(token);
        return { kind: 'recovery', codes, commit };
      }
      commit();
      return { kind: 'signed_in' };
    },
    [],
  );

  const login = useCallback(
    async (email: string, password: string, expectedRole?: PortalRole) =>
      accept(await portalLogin(email, password), expectedRole),
    [accept],
  );

  const completeMfa = useCallback(
    async (mfaToken: string, code: string, expectedRole?: PortalRole) =>
      accept(await portalLoginMfa(mfaToken, code), expectedRole),
    [accept],
  );

  const completeMfaEnrollment = useCallback(
    async (mfaToken: string, code: string, expectedRole?: PortalRole) => {
      const result = await mfaEnable(code, mfaToken);
      if ('kind' in result) return accept(result, expectedRole);
      return { kind: 'signed_in' } as AuthOutcome;
    },
    [accept],
  );

  const setUser = useCallback((next: PortalUser) => {
    persistUser(next);
    setUserState(next);
  }, []);

  const logout = useCallback(() => {
    portalLogout().catch(() => undefined);
    clearStoredUser();
    setMemoryToken('');
    setUserState(null);
    setSessionId('');
  }, []);

  const logoutEverywhere = useCallback(async () => {
    await logoutAllDevices();
    clearStoredUser();
    setMemoryToken('');
    setUserState(null);
    setSessionId('');
  }, []);

  const clearSessionNotice = useCallback(() => setSessionNotice(''), []);

  const value = useMemo(
    () => ({
      user,
      sessionId,
      login,
      completeMfa,
      completeMfaEnrollment,
      setUser,
      logout,
      logoutEverywhere,
      sessionNotice,
      clearSessionNotice,
    }),
    [user, sessionId, login, completeMfa, completeMfaEnrollment, setUser, logout, logoutEverywhere, sessionNotice, clearSessionNotice],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

export type { PortalSession };
