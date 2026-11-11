// Authentication Types for RS256 JWT-based authentication

export interface UserWorkspace {
  id: string;
  name: string;
}

export interface User {
  id: string;
  email: string;
  username: string;
  firstName?: string;
  lastName?: string;
  role: UserRole;
  scope?: string;
  permissions: string[];
  workspace?: UserWorkspace;
  workspaceId?: string;
  workspaceName?: string;
  isActive: boolean;
  isVerified: boolean;
  lastLogin?: string;
  lastLoginAt?: string;
  createdAt: string;
  updatedAt?: string;
}

// Role hierarchy (highest to lowest privilege):
// superadmin > workspace_admin > contributor > consumer
export type UserRole = 'superadmin' | 'workspace_admin' | 'contributor' | 'consumer';

// Role-based page access configuration
export const ROLE_PAGE_ACCESS: Record<UserRole, string[]> = {
  superadmin: ['home', 'upload', 'veeva', 'chat', 'presets', 'users', 'workspace', 'documents', 'document-chunks', 'rag-query', 'repositories', 'document-optimizer', 'document-harmonization', 'publication-generator', 'usecase', 'settings'],
  workspace_admin: ['home', 'upload', 'veeva', 'chat', 'presets', 'users', 'workspace', 'documents', 'document-chunks', 'rag-query', 'repositories', 'document-optimizer', 'document-harmonization', 'publication-generator', 'usecase', 'settings'],
  contributor: ['home', 'upload', 'veeva', 'chat', 'presets', 'documents', 'document-chunks', 'rag-query', 'repositories', 'document-optimizer', 'document-harmonization', 'publication-generator', 'usecase'],
  consumer: ['home', 'chat', 'documents', 'document-chunks', 'rag-query', 'repositories', 'presets', 'document-optimizer', 'document-harmonization', 'publication-generator', 'usecase'],
};

// Check if a role can access a specific page
export function canAccessPage(role: UserRole | undefined | null, page: string): boolean {
  const effectiveRole = normalizeRole(role);
  return ROLE_PAGE_ACCESS[effectiveRole]?.includes(page) ?? false;
}

// Normalize role from backend to our expected format
export function normalizeRole(role: string | undefined | null): UserRole {
  if (!role) return 'consumer';

  const lowerRole = role.toLowerCase().replace(/[-\s]/g, '_');

  // Map various backend role formats to our roles
  const roleMap: Record<string, UserRole> = {
    'superadmin': 'superadmin',
    'super_admin': 'superadmin',
    'admin': 'superadmin',
    'workspace_admin': 'workspace_admin',
    'workspaceadmin': 'workspace_admin',
    'workspace-admin': 'workspace_admin',
    'contributor': 'contributor',
    'editor': 'contributor',
    'consumer': 'consumer',
    'user': 'consumer',
    'viewer': 'consumer',
    'reader': 'consumer',
  };

  return roleMap[lowerRole] || 'consumer';
}

/** Map frontend UserRole to backend API / database role strings */
export function roleToApi(role: UserRole): string {
  if (role === 'superadmin') return 'super_admin';
  return role;
}

// Get display name for role
export function getRoleDisplayName(role: UserRole): string {
  const displayNames: Record<UserRole, string> = {
    superadmin: 'Super Admin',
    workspace_admin: 'Workspace Admin',
    contributor: 'Contributor',
    consumer: 'Consumer',
  };
  return displayNames[role] || role;
}

export interface LoginCredentials {
  email: string;
  password: string;
  rememberMe?: boolean;
}

export interface RegisterCredentials {
  email: string;
  password: string;
  confirmPassword: string;
  firstName?: string;
  lastName?: string;
  acceptTerms: boolean;
}

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
  tokenType: 'Bearer';
  expiresIn: number; // seconds until access token expires
  refreshExpiresIn: number; // seconds until refresh token expires
}

export interface AuthResponse {
  user: User;
  tokens: AuthTokens;
}

export interface RefreshTokenResponse {
  accessToken: string;
  expiresIn: number;
}

export interface JWTPayload {
  sub: string; // subject (user id)
  email: string;
  role: UserRole;
  permissions: string[];
  iat: number; // issued at
  exp: number; // expiration
  jti: string; // JWT ID (for revocation)
  iss: string; // issuer
  aud: string; // audience
}

export interface PasswordResetRequest {
  email: string;
}

export interface PasswordResetConfirm {
  token: string;
  password: string;
  confirmPassword: string;
}

export interface ChangePasswordRequest {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

export interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  isInitialized: boolean;
  error: AuthError | null;
}

export interface AuthError {
  code: AuthErrorCode;
  message: string;
  field?: string;
  details?: Record<string, string>;
}

export type AuthErrorCode =
  | 'INVALID_CREDENTIALS'
  | 'USER_NOT_FOUND'
  | 'USER_DISABLED'
  | 'EMAIL_NOT_VERIFIED'
  | 'TOKEN_EXPIRED'
  | 'TOKEN_INVALID'
  | 'TOKEN_REVOKED'
  | 'REFRESH_TOKEN_EXPIRED'
  | 'SESSION_EXPIRED'
  | 'RATE_LIMITED'
  | 'ACCOUNT_LOCKED'
  | 'PASSWORD_TOO_WEAK'
  | 'EMAIL_ALREADY_EXISTS'
  | 'VALIDATION_ERROR'
  | 'NETWORK_ERROR'
  | 'SERVER_ERROR'
  | 'UNKNOWN_ERROR';

export interface PasswordValidation {
  isValid: boolean;
  errors: string[];
  strength: 'weak' | 'fair' | 'good' | 'strong';
  score: number; // 0-100
}

export interface LoginAttempt {
  timestamp: number;
  success: boolean;
  ipAddress?: string;
  userAgent?: string;
}

export interface SecurityEvent {
  type: 'login' | 'logout' | 'password_change' | 'token_refresh' | 'failed_login';
  timestamp: string;
  ipAddress?: string;
  userAgent?: string;
  details?: Record<string, any>;
}
