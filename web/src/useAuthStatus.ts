import { useEffect, useState } from 'react';
import type { AuthClient } from './auth/types';

export type AuthStatus = 'loading' | 'signed-out' | 'signed-in';

export function useAuthStatus(auth: AuthClient): AuthStatus {
  const [status, setStatus] = useState<AuthStatus>('loading');
  useEffect(() => auth.watch((signedIn) => setStatus(signedIn ? 'signed-in' : 'signed-out')), [auth]);
  return status;
}
