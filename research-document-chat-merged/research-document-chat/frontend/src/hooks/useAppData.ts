import { useQuery } from '../lib/queryCache';
import { fetchMeContext, listTherapeuticAreas, type MeContext, type TherapeuticArea } from '../services/digitalMslService';
import { listRepositories, type Repository } from '../services/repositoryService';

/** One request per key, shared by every component that asks (see lib/queryCache). */
export const QUERY_KEYS = {
  meContext: (userId: string) => `me-context:${userId}`,
  repositories: 'repositories',
  areas: 'therapeutic-areas',
} as const;

export function useMeContext(userId: string | null) {
  return useQuery<MeContext>(userId ? QUERY_KEYS.meContext(userId) : null, fetchMeContext, { staleMs: 15_000 });
}

/** Administrator and Physical MSL only. A doctor's sources come from useMeContext. */
export function useRepositories(enabled: boolean) {
  return useQuery<Repository[]>(enabled ? QUERY_KEYS.repositories : null, listRepositories, { staleMs: 60_000 });
}

export function useTherapeuticAreaList(enabled: boolean) {
  return useQuery<TherapeuticArea[]>(enabled ? QUERY_KEYS.areas : null, listTherapeuticAreas, { staleMs: 30_000 });
}
