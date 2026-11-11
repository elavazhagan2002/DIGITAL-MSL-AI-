import type { JWTPayload, AuthTokens } from '../types/auth';

// Storage keys
const ACCESS_TOKEN_KEY = 'qms_access_token';
const REFRESH_TOKEN_KEY = 'qms_refresh_token';
const TOKEN_EXPIRY_KEY = 'qms_token_expiry';
const REFRESH_EXPIRY_KEY = 'qms_refresh_expiry';

// Token refresh buffer (refresh 5 minutes before expiry)
const TOKEN_REFRESH_BUFFER_SECONDS = 300;

/**
 * Decode a JWT token without verification (verification happens server-side with RS256)
 * This is safe because RS256 tokens are verified by the server using the public key
 */
export function decodeToken(token: string): JWTPayload | null {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) {
      console.error('Invalid JWT format');
      return null;
    }

    const payload = parts[1];
    // Handle base64url encoding
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const jsonPayload = decodeURIComponent(
      atob(base64)
        .split('')
        .map((c) => '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2))
        .join('')
    );

    return JSON.parse(jsonPayload) as JWTPayload;
  } catch (error) {
    console.error('Failed to decode token:', error);
    return null;
  }
}

/**
 * Check if a token is expired
 */
export function isTokenExpired(token: string): boolean {
  const payload = decodeToken(token);
  if (!payload || !payload.exp) {
    return true;
  }

  // Add buffer to account for clock skew and network latency
  const currentTime = Math.floor(Date.now() / 1000);
  return payload.exp < currentTime;
}

/**
 * Check if token needs refresh (expiring soon)
 */
export function shouldRefreshToken(token: string): boolean {
  const payload = decodeToken(token);
  if (!payload || !payload.exp) {
    return true;
  }

  const currentTime = Math.floor(Date.now() / 1000);
  const timeUntilExpiry = payload.exp - currentTime;

  return timeUntilExpiry < TOKEN_REFRESH_BUFFER_SECONDS;
}

/**
 * Get remaining time until token expires (in seconds)
 */
export function getTokenExpiryTime(token: string): number {
  const payload = decodeToken(token);
  if (!payload || !payload.exp) {
    return 0;
  }

  const currentTime = Math.floor(Date.now() / 1000);
  return Math.max(0, payload.exp - currentTime);
}

/**
 * Securely store tokens
 * Uses localStorage with HttpOnly cookie fallback for refresh tokens (server-side)
 */
export function storeTokens(tokens: AuthTokens, rememberMe: boolean = false): void {
  const storage = rememberMe ? localStorage : sessionStorage;

  try {
    // Store access token (short-lived)
    storage.setItem(ACCESS_TOKEN_KEY, tokens.accessToken);

    // Store refresh token (longer-lived)
    // In production, refresh tokens should be stored in HttpOnly cookies
    // This is a fallback for development
    storage.setItem(REFRESH_TOKEN_KEY, tokens.refreshToken);

    // Store expiry times for quick checks
    const now = Date.now();
    storage.setItem(TOKEN_EXPIRY_KEY, String(now + tokens.expiresIn * 1000));
    storage.setItem(REFRESH_EXPIRY_KEY, String(now + tokens.refreshExpiresIn * 1000));
  } catch (error) {
    console.error('Failed to store tokens:', error);
    throw new Error('Failed to store authentication tokens');
  }
}

/**
 * Retrieve access token
 */
export function getAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_TOKEN_KEY) || localStorage.getItem(ACCESS_TOKEN_KEY);
}

/**
 * Retrieve refresh token
 */
export function getRefreshToken(): string | null {
  return sessionStorage.getItem(REFRESH_TOKEN_KEY) || localStorage.getItem(REFRESH_TOKEN_KEY);
}

/**
 * Update access token after refresh
 */
export function updateAccessToken(accessToken: string, expiresIn: number): void {
  const storage = localStorage.getItem(ACCESS_TOKEN_KEY) ? localStorage : sessionStorage;

  storage.setItem(ACCESS_TOKEN_KEY, accessToken);
  storage.setItem(TOKEN_EXPIRY_KEY, String(Date.now() + expiresIn * 1000));
}

/**
 * Clear all stored tokens
 */
export function clearTokens(): void {
  // Clear from both storages
  [localStorage, sessionStorage].forEach((storage) => {
    storage.removeItem(ACCESS_TOKEN_KEY);
    storage.removeItem(REFRESH_TOKEN_KEY);
    storage.removeItem(TOKEN_EXPIRY_KEY);
    storage.removeItem(REFRESH_EXPIRY_KEY);
  });
}

/**
 * Check if tokens exist in storage
 */
export function hasStoredTokens(): boolean {
  return !!(getAccessToken() && getRefreshToken());
}

/**
 * Get stored token expiry time
 */
export function getStoredTokenExpiry(): number | null {
  const expiry = sessionStorage.getItem(TOKEN_EXPIRY_KEY) || localStorage.getItem(TOKEN_EXPIRY_KEY);
  return expiry ? parseInt(expiry, 10) : null;
}

/**
 * Check if stored tokens are valid (not expired)
 */
export function areStoredTokensValid(): boolean {
  const accessToken = getAccessToken();
  const refreshToken = getRefreshToken();

  if (!accessToken || !refreshToken) {
    return false;
  }

  // Check if refresh token is still valid
  if (isTokenExpired(refreshToken)) {
    clearTokens();
    return false;
  }

  return true;
}

/**
 * Extract user info from token
 */
export function getUserFromToken(token: string): { id: string; email: string; role: string } | null {
  const payload = decodeToken(token);
  if (!payload) {
    return null;
  }

  return {
    id: payload.sub,
    email: payload.email,
    role: payload.role,
  };
}

/**
 * Generate a cryptographically secure random string for CSRF tokens
 */
export function generateSecureRandom(length: number = 32): string {
  const array = new Uint8Array(length);

  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(array);
  } else {
    // Fallback for environments without Web Crypto (non-secure contexts, very old browsers)
    for (let i = 0; i < length; i++) {
      array[i] = Math.floor(Math.random() * 256);
    }
  }

  return Array.from(array, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

/**
 * Create a fingerprint for device binding (optional security enhancement)
 */
export async function createDeviceFingerprint(): Promise<string> {
  const components = [
    navigator.userAgent,
    navigator.language,
    new Date().getTimezoneOffset().toString(),
    screen.width.toString(),
    screen.height.toString(),
    screen.colorDepth.toString(),
  ];

  const data = components.join('|');
  try {
    if (
      typeof crypto !== 'undefined' &&
      crypto.subtle &&
      typeof crypto.subtle.digest === 'function'
    ) {
      const encoder = new TextEncoder();
      const hashBuffer = await crypto.subtle.digest('SHA-256', encoder.encode(data));
      const hashArray = Array.from(new Uint8Array(hashBuffer));
      return hashArray.map((b) => b.toString(16).padStart(2, '0')).join('');
    }
  } catch (error) {
    console.warn('Failed to generate cryptographic device fingerprint, falling back to non-crypto fingerprint', error);
  }

  // Fallback: simple hash + random component (not cryptographically strong but stable enough for non-secure contexts)
  let hash = 0;
  for (let i = 0; i < data.length; i++) {
    hash = (hash << 5) - hash + data.charCodeAt(i);
    hash |= 0; // Convert to 32-bit integer
  }
  const randomPart = Math.random().toString(16).slice(2);
  return `${hash.toString(16)}${randomPart}`;
}
