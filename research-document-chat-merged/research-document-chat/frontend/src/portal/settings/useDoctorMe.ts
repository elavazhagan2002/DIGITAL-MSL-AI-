import { useCallback, useEffect, useState } from 'react';
import {
  fetchPortalMe,
  type ConsentsState,
  type DoctorProfile,
  type DoctorTherapeuticAreaPreference,
} from '../../services/digitalMslService';
import { useAuth } from '../AuthContext';

export interface DoctorMe {
  loading: boolean;
  error: string;
  profile: DoctorProfile | null;
  consents: ConsentsState | null;
  areas: DoctorTherapeuticAreaPreference[];
  reload: () => void;
  setProfile: (updater: (p: DoctorProfile | null) => DoctorProfile | null) => void;
  setConsents: (next: ConsentsState) => void;
}

/** GET /portal/me for the signed-in doctor: registration profile, consent records and therapeutic areas. */
export function useDoctorMe(enabled: boolean): DoctorMe {
  const { user, setUser } = useAuth();
  const [loading, setLoading] = useState(enabled);
  const [error, setError] = useState('');
  const [profile, setProfileState] = useState<DoctorProfile | null>(null);
  const [consents, setConsents] = useState<ConsentsState | null>(null);
  const [areas, setAreas] = useState<DoctorTherapeuticAreaPreference[]>([]);
  const [tick, setTick] = useState(0);
  const userId = user?.user_id;

  useEffect(() => {
    if (!enabled || !userId) return;
    let cancelled = false;
    setLoading(true);
    setError('');
    fetchPortalMe()
      .then((data) => {
        if (cancelled) return;
        setProfileState(data.profile ?? null);
        setConsents(data.consents ?? null);
        setAreas(data.therapeutic_areas || []);
        setUser({ ...data.user, verification_status: data.profile?.verification_status ?? data.user.verification_status });
      })
      .catch(() => {
        if (!cancelled) setError('We could not load your details. Please try again.');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, userId, tick]);

  return {
    loading,
    error,
    profile,
    consents,
    areas,
    reload: () => setTick((n) => n + 1),
    setProfile: (updater) => setProfileState(updater),
    setConsents,
  };
}
