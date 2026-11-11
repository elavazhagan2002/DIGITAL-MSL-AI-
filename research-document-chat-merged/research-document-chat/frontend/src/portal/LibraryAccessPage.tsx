import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';
import { assignDoctorRepositories, listPortalUsers, type PortalUser } from '../services/digitalMslService';
import { listLibrariesForAssignment } from '../services/repositoryService';
import { useToast } from '../contexts/ToastContext';
import { secondaryButton } from '../config/pageTheme';

export function LibraryAccessPage({ embedded = false }: { embedded?: boolean }) {
  const toast = useToast();
  const [doctors, setDoctors] = useState<PortalUser[]>([]);
  const [repos, setRepos] = useState<Array<{ repository_id: string; name: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const [usersResult, reposResult] = await Promise.allSettled([listPortalUsers(), listLibrariesForAssignment()]);
    if (usersResult.status === 'fulfilled') {
      setDoctors(usersResult.value.filter((u) => u.role === 'doctor'));
    } else {
      setDoctors([]);
      setError(usersResult.reason instanceof Error ? usersResult.reason.message : 'Could not load doctors.');
    }
    if (reposResult.status === 'fulfilled') setRepos(reposResult.value);
    else setRepos([]);
    setLoading(false);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = async (doctor: PortalUser, repoId: string) => {
    const current = doctor.assigned_repository_ids || [];
    if (!current.includes(repoId) && current.length >= 3) {
      toast.error('A doctor can select at most three therapeutic areas.');
      return;
    }
    const next = current.includes(repoId) ? current.filter((id) => id !== repoId) : [...current, repoId];
    try {
      const updated = await assignDoctorRepositories(doctor.user_id, next);
      setDoctors((prev) => prev.map((d) => (d.user_id === updated.user_id ? updated : d)));
      toast.success(`Updated ${doctor.display_name}.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not update access.');
    }
  };

  return (
    <div className="space-y-6">
      {!embedded && (
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-fg">Doctor repo access</h1>
            <p className="text-sm text-muted mt-1">
              Assign therapeutic areas and their document repos (up to three per doctor).
            </p>
          </div>
          <button type="button" className={secondaryButton} onClick={load}>
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      )}
      {embedded && (
        <div className="flex items-start justify-between gap-3">
          <p className="text-sm text-muted">Tick up to three therapeutic areas for each doctor.</p>
          <button type="button" className={secondaryButton} onClick={load}>
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-err-line bg-err-bg px-4 py-3 text-sm text-err-fg">
          <AlertCircle className="w-4 h-4 mt-0.5" />
          {error}
        </div>
      )}

      {loading && <div className="h-48 rounded-2xl bg-soft animate-pulse" />}

      {!loading && repos.length === 0 && (
        <div className="rounded-2xl border border-warn-line bg-warn-bg p-6 text-sm text-warn-fg">
          No therapeutic areas yet — create one under Therapeutic areas, then return here.
        </div>
      )}

      {!loading && doctors.length === 0 && (
        <div className="rounded-2xl border border-dashed border-line p-8 text-center text-sm text-muted">
          No doctors yet. Add one on the People tab.
        </div>
      )}

      {!loading && doctors.length > 0 && repos.length > 0 && (
        <div className="overflow-x-auto bg-card border border-line rounded-2xl">
          <table className="w-full text-sm min-w-[520px]">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wide text-muted border-b border-line">
                <th className="px-4 py-3 font-semibold">Doctor</th>
                {repos.map((r) => (
                  <th key={r.repository_id} className="px-3 py-3 font-semibold whitespace-nowrap">
                    {r.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {doctors.map((d) => (
                <tr key={d.user_id}>
                  <td className="px-4 py-3">
                    <p className="font-medium text-fg">{d.display_name}</p>
                    <p className="text-xs text-muted">{d.email}</p>
                  </td>
                  {repos.map((r) => {
                    const on = (d.assigned_repository_ids || []).includes(r.repository_id);
                    return (
                      <td key={r.repository_id} className="px-3 py-3">
                        <label className="inline-flex items-center gap-2 text-xs text-muted">
                          <input
                            type="checkbox"
                            checked={on}
                            onChange={() => toggle(d, r.repository_id)}
                            className="rounded border-line text-link"
                          />
                          {on ? 'Allowed' : 'Off'}
                        </label>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
