import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { AlertCircle, Building2, RefreshCw, Users } from 'lucide-react';
import {
  createGroup,
  createPortalUser,
  listGroups,
  listPortalUsers,
  listTherapeuticAreas,
  setGroupMembers,
  type MslGroup,
  type PortalUser,
} from '../services/digitalMslService';
import { ROLE_LABEL } from './AuthContext';
import { useToast } from '../contexts/ToastContext';
import { inputClass, labelClass, primaryButton, secondaryButton } from '../config/pageTheme';

export function PeoplePage({ embedded = false, view = 'all' }: { embedded?: boolean; view?: 'all' | 'teams' }) {
  const toast = useToast();
  const [users, setUsers] = useState<PortalUser[]>([]);
  const [groups, setGroups] = useState<MslGroup[]>([]);
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [role, setRole] = useState<PortalUser['role']>('doctor');
  const [saving, setSaving] = useState(false);
  const [groupName, setGroupName] = useState('');
  const [repos, setRepos] = useState<Array<{ repository_id: string; name: string }>>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [tempPassword, setTempPassword] = useState<{ email: string; password: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const [usersResult, groupsResult, areasResult] = await Promise.allSettled([
      listPortalUsers(),
      listGroups(),
      listTherapeuticAreas(),
    ]);
    if (usersResult.status === 'fulfilled') setUsers(usersResult.value);
    else {
      setUsers([]);
      const msg = usersResult.reason instanceof Error ? usersResult.reason.message : 'Could not load people.';
      setError(msg);
      toast.error(msg);
    }
    let nextGroups = groupsResult.status === 'fulfilled' ? groupsResult.value : [];
    const loadedUsers = usersResult.status === 'fulfilled' ? usersResult.value : [];
    if (groupsResult.status === 'fulfilled' && nextGroups.length === 0) {
      try {
        const created = await createGroup('Physical MSL team');
        const mslIds = loadedUsers.filter((u) => u.role === 'msl').map((u) => u.user_id);
        if (mslIds.length) await setGroupMembers(created.group_id, mslIds);
        nextGroups = await listGroups();
      } catch {
        /* keep empty if team APIs are unavailable */
      }
    }
    setGroups(nextGroups);
    if (areasResult.status === 'fulfilled') {
      setRepos(areasResult.value.map((a) => ({ repository_id: a.repository_id, name: a.name })));
    } else setRepos([]);
    setLoading(false);
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const msls = users.filter((u) => u.role === 'msl');
  const doctors = users.filter((u) => u.role === 'doctor');
  const repoName = (id: string) => repos.find((r) => r.repository_id === id)?.name || id;

  const addUser = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      const created = await createPortalUser({ email, display_name: name, role });
      setTempPassword(created.temporary_password ? { email: created.email, password: created.temporary_password } : null);
      setEmail('');
      setName('');
      await load();
      toast.success('Person saved.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save.');
    } finally {
      setSaving(false);
    }
  };

  const addGroup = async (e: FormEvent) => {
    e.preventDefault();
    if (groupName.trim().length < 2) return;
    try {
      await createGroup(groupName.trim());
      setGroupName('');
      await load();
      toast.success('Team created.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not create team.');
    }
  };

  return (
    <div className="space-y-6">
      {!embedded && (
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold text-fg">People</h1>
            <p className="text-sm text-muted mt-1">
              Add doctors and Physical MSLs. Doctors show interest in up to three therapeutic areas.
            </p>
          </div>
          <button type="button" className={secondaryButton} onClick={load}>
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      )}
      {embedded && (
        <div className="flex justify-end">
          <button type="button" className={secondaryButton} onClick={load}>
            <RefreshCw className="w-4 h-4" /> Refresh
          </button>
        </div>
      )}

      {error && (
        <div className="flex items-start gap-3 rounded-xl border border-err-line bg-err-bg px-4 py-3 text-sm text-err-fg">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <div className="flex-1">
            <p>{error}</p>
            <button type="button" className="mt-2 text-xs font-semibold underline" onClick={load}>
              Retry
            </button>
          </div>
        </div>
      )}

      {tempPassword && (
        <div className="rounded-xl border border-warn-line bg-warn-bg px-4 py-3 text-sm text-warn-fg">
          <p className="font-semibold">Temporary password for {tempPassword.email}</p>
          <p className="mt-1 font-mono text-base select-all">{tempPassword.password}</p>
          <p className="mt-1 text-xs">Shown once. Share it securely; they should change it in Settings after signing in.</p>
          <button type="button" className="mt-2 text-xs font-semibold underline" onClick={() => setTempPassword(null)}>Dismiss</button>
        </div>
      )}

      {view === 'all' && (
      <form onSubmit={addUser} className="bg-card border border-line rounded-2xl p-5 grid sm:grid-cols-4 gap-4 items-end">
        <label className={labelClass}>
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} className={`mt-1 ${inputClass}`} placeholder="Dr. Mehta" />
        </label>
        <label className={labelClass}>
          Email
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} className={`mt-1 ${inputClass}`} placeholder="name@hospital.org" />
        </label>
        <label className={labelClass}>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as PortalUser['role'])} className={`mt-1 ${inputClass}`}>
            <option value="admin">Administrator</option>
            <option value="doctor">Doctor</option>
            <option value="msl">Physical MSL</option>
          </select>
        </label>
        <button disabled={saving || !email || name.trim().length < 2} className={`${primaryButton} h-10`}>
          Save person
        </button>
      </form>
      )}

      <form onSubmit={addGroup} className="bg-card border border-line rounded-2xl p-5 flex flex-col sm:flex-row gap-3 items-end">
        <label className={`${labelClass} flex-1 w-full`}>
          New MSL team
          <input value={groupName} onChange={(e) => setGroupName(e.target.value)} className={`mt-1 ${inputClass}`} placeholder="e.g. East team" />
        </label>
        <button className={primaryButton}>Create team</button>
      </form>

      {groups.map((g) => (
        <section key={g.group_id} className="bg-card border border-line rounded-2xl p-5 space-y-3">
          <div className="flex items-center gap-2">
            <Building2 className="w-4 h-4 text-link" />
            <h2 className="text-sm font-semibold text-fg">{g.name}</h2>
          </div>
          <p className="text-xs text-muted">MSL members — any member can answer doctors in this team</p>
          <div className="flex flex-wrap gap-2">
            {msls.length === 0 && <span className="text-xs text-muted">No MSL accounts yet.</span>}
            {msls.map((m) => {
              const on = g.msl_ids.includes(m.user_id);
              return (
                <button
                  key={m.user_id}
                  type="button"
                  className={`text-xs px-3 py-1.5 rounded-full border ${on ? 'bg-accent text-accentfg border-selb' : 'border-line text-muted'}`}
                  onClick={async () => {
                    const next = on ? g.msl_ids.filter((id) => id !== m.user_id) : [...g.msl_ids, m.user_id];
                    await setGroupMembers(g.group_id, next);
                    await load();
                  }}
                >
                  {m.display_name}
                </button>
              );
            })}
          </div>
        </section>
      ))}

      {loading && (
        <div className="grid md:grid-cols-2 gap-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-40 rounded-2xl border border-line bg-soft animate-pulse" />
          ))}
        </div>
      )}

      {!loading && users.length === 0 && !error && (
        <div className="rounded-2xl border border-dashed border-line p-10 text-center text-sm text-muted">
          <Users className="w-8 h-8 mx-auto mb-2 opacity-40" />
          No people yet. Add a doctor or MSL above.
        </div>
      )}

      <div className={view === 'all' ? 'grid md:grid-cols-2 gap-4' : 'hidden'}>
        {users.map((u) => (
          <article key={u.user_id} className="bg-card border border-line rounded-2xl p-5 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-semibold text-fg">{u.display_name}</p>
                <p className="text-xs text-muted mt-0.5">{u.email}</p>
              </div>
              <span className="text-[11px] font-semibold uppercase tracking-wide px-2 py-1 rounded-full bg-soft text-muted">
                {ROLE_LABEL[u.role]}
              </span>
            </div>

            {u.role === 'doctor' ? (
              <div>
                <p className={labelClass}>Interested therapeutic areas</p>
                <p className="text-[11px] text-muted mb-2">
                  Set at doctor registration (up to three). Physical MSL coverage follows the MSLs assigned to each area.
                </p>
                <ul className="text-sm text-fg space-y-1">
                  {(u.assigned_repository_ids || []).length ? (
                    (u.assigned_repository_ids || []).map((id) => <li key={id}>{repoName(id)}</li>)
                  ) : (
                    <li className="text-muted">None selected yet</li>
                  )}
                </ul>
              </div>
            ) : (
              <p className="text-xs text-muted">Administrators see every therapeutic area. Physical MSLs see areas they own or join.</p>
            )}
          </article>
        ))}
      </div>

      {doctors.length > 0 && !embedded && (
        <p className="text-xs text-muted">Use Doctor interest to see every doctor and therapeutic area in one grid.</p>
      )}
    </div>
  );
}
