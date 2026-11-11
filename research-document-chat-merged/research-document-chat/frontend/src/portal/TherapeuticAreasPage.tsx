import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  Layers,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  UserPlus,
  UserRound,
  X,
} from 'lucide-react';
import {
  createTherapeuticArea,
  deleteTherapeuticArea,
  listPortalUsers,
  listTherapeuticAreas,
  seedDefaultTherapeuticAreas,
  setTherapeuticAreaActive,
  setTherapeuticAreaMembers,
  updateTherapeuticAreaSettings,
  type PortalUser,
  type SeedAreasResult,
  type TherapeuticArea,
} from '../services/digitalMslService';
import { listRepositories, type Repository } from '../services/repositoryService';
import { fetchQuery } from '../lib/queryCache';
import { QUERY_KEYS } from '../hooks/useAppData';
import { useToast } from '../contexts/ToastContext';
import {
  filterInputClass,
  inputClass,
  labelClass,
  primaryButton,
  secondaryButton,
  dangerButton,
  statusBadge,
} from '../config/pageTheme';

type StatusFilter = 'all' | 'active' | 'inactive';

function assignedMslLine(area: TherapeuticArea, msls: PortalUser[]): string {
  const ids = new Set([...(area.msl_ids || []), area.owner_msl_id].filter(Boolean));
  if (!ids.size) return 'No Physical MSLs assigned';
  return [...ids].map((id) => msls.find((m) => m.user_id === id)?.display_name || id).join(', ');
}

const isPlaceholder = (area: TherapeuticArea) => area.repository_id.startsWith('pending:');
const isComingSoon = (area: TherapeuticArea) => area.coming_soon === true || area.enabled === false;

export function TherapeuticAreasPage() {
  const toast = useToast();
  const [areas, setAreas] = useState<TherapeuticArea[]>([]);
  const [repos, setRepos] = useState<Repository[]>([]);
  const [msls, setMsls] = useState<PortalUser[]>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [assignId, setAssignId] = useState<string | null>(null);
  const [draftMslIds, setDraftMslIds] = useState<string[]>([]);
  const [draftOwner, setDraftOwner] = useState('');
  const [assignSaving, setAssignSaving] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<TherapeuticArea | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [owner, setOwner] = useState('');
  const [asDefault, setAsDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  const [seeding, setSeeding] = useState(false);
  const [seedResult, setSeedResult] = useState<SeedAreasResult | null>(null);
  const [editArea, setEditArea] = useState<TherapeuticArea | null>(null);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editOrder, setEditOrder] = useState('0');
  const [editKeywords, setEditKeywords] = useState('');
  const [editEnabled, setEditEnabled] = useState(true);
  const [editRepoId, setEditRepoId] = useState('');
  const [editSaving, setEditSaving] = useState(false);
  const [editError, setEditError] = useState('');

  const openEdit = (area: TherapeuticArea) => {
    setEditArea(area);
    setEditName(area.name);
    setEditDescription(area.description || '');
    setEditOrder(String(area.sort_order ?? 0));
    setEditKeywords((area.scope_keywords || []).join(', '));
    setEditEnabled(!isComingSoon(area));
    setEditRepoId('');
    setEditError('');
  };

  const saveEdit = async (e: FormEvent) => {
    e.preventDefault();
    if (!editArea) return;
    if (editName.trim().length < 3) {
      setEditError('The name needs at least 3 characters.');
      return;
    }
    const order = Number(editOrder);
    if (!Number.isFinite(order) || order < 0) {
      setEditError('Sort order must be 0 or more.');
      return;
    }
    setEditSaving(true);
    setEditError('');
    try {
      const updated = await updateTherapeuticAreaSettings(editArea.repository_id, {
        name: editName.trim(),
        description: editDescription.trim(),
        sort_order: order,
        enabled: editEnabled,
        scope_keywords: editKeywords.split(',').map((k) => k.trim().toLowerCase()).filter(Boolean),
        repository_id: editRepoId.trim() || undefined,
      });
      setAreas((prev) => prev.map((a) => (a.repository_id === editArea.repository_id ? updated : a)));
      setEditArea(null);
      toast.success('Therapeutic area saved.');
      await load();
    } catch (err) {
      setEditError(err instanceof Error ? err.message : 'Could not save.');
    } finally {
      setEditSaving(false);
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextAreas, users, nextRepos] = await Promise.all([
        listTherapeuticAreas(),
        listPortalUsers('msl'),
        fetchQuery(QUERY_KEYS.repositories, listRepositories, { staleMs: 60_000 }).catch(() => [] as Repository[]),
      ]);
      setAreas(nextAreas);
      setMsls(users);
      setRepos(nextRepos.filter((r) => !r.name?.startsWith('_temp_')));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch((err) => toast.error(err instanceof Error ? err.message : 'Could not load therapeutic areas.'));
  }, [load, toast]);

  useEffect(() => {
    if (!owner && msls[0]) setOwner(msls[0].user_id);
  }, [msls, owner]);

  const repoMeta = useMemo(() => {
    const map = new Map<string, Repository>();
    for (const r of repos) map.set(r.repository_id, r);
    return map;
  }, [repos]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return [...areas]
      .sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0) || a.name.localeCompare(b.name))
      .filter((area) => {
      if (statusFilter === 'active' && area.active === false) return false;
      if (statusFilter === 'inactive' && area.active !== false) return false;
      if (!q) return true;
      const hay = `${area.name} ${area.repository_id} ${area.description || ''} ${(area.scope_keywords || []).join(' ')}`.toLowerCase();
      return hay.includes(q);
    });
  }, [areas, search, statusFilter]);

  const assignArea = areas.find((a) => a.repository_id === assignId) || null;

  useEffect(() => {
    if (!assignArea) return;
    const ids = [...new Set([...(assignArea.msl_ids || []), assignArea.owner_msl_id].filter(Boolean))];
    setDraftMslIds(ids);
    setDraftOwner(assignArea.owner_msl_id || ids[0] || '');
  }, [assignArea?.repository_id]);

  const openAssign = (area: TherapeuticArea) => {
    setAssignId(area.repository_id);
  };

  const toggleDraftMsl = (mslId: string) => {
    setDraftMslIds((prev) => {
      if (prev.includes(mslId)) {
        const next = prev.filter((id) => id !== mslId);
        setDraftOwner((o) => (o === mslId ? next[0] || '' : o));
        return next;
      }
      const next = [...prev, mslId];
      if (!draftOwner) setDraftOwner(mslId);
      return next;
    });
  };

  const saveAssignment = async () => {
    if (!assignArea) return;
    if (!draftMslIds.length) {
      toast.error('Select at least one Physical MSL.');
      return;
    }
    const ownerId = draftOwner && draftMslIds.includes(draftOwner) ? draftOwner : draftMslIds[0];
    setAssignSaving(true);
    try {
      const updated = await setTherapeuticAreaMembers(assignArea.repository_id, draftMslIds, ownerId);
      setAreas((prev) => prev.map((a) => (a.repository_id === updated.repository_id ? updated : a)));
      toast.success('Physical MSLs assigned.');
      setAssignId(null);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save assignment.');
    } finally {
      setAssignSaving(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    try {
      await deleteTherapeuticArea(deleteTarget.repository_id);
      toast.success('Therapeutic area removed.');
      if (assignId === deleteTarget.repository_id) setAssignId(null);
      setDeleteTarget(null);
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not delete therapeutic area.');
    } finally {
      setDeleteBusy(false);
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setSaving(true);
    try {
      await createTherapeuticArea({
        name: name.trim(),
        description: description.trim(),
        owner_msl_id: owner,
        default_flag: asDefault,
        msl_ids: owner ? [owner] : [],
      });
      setName('');
      setDescription('');
      setShowCreate(false);
      toast.success('Therapeutic area created.');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not create therapeutic area.');
    } finally {
      setSaving(false);
    }
  };

  const seedStandard = async () => {
    setSeeding(true);
    setSeedResult(null);
    try {
      const result = await seedDefaultTherapeuticAreas();
      setSeedResult(result);
      if (result.created.length) toast.success(`${result.created.length} therapeutic area(s) added.`);
      else if (!result.failed.length) toast.success('All standard therapeutic areas already exist.');
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not add standard therapeutic areas.');
    } finally {
      setSeeding(false);
    }
  };

  const toggleRegistration = async (area: TherapeuticArea) => {
    const activate = area.active === false;
    try {
      const updated = await setTherapeuticAreaActive(area.repository_id, activate);
      setAreas((prev) => prev.map((a) => (a.repository_id === updated.repository_id ? updated : a)));
      toast.success(activate ? 'Available for doctor registration.' : 'Hidden from new doctor registration.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not update status.');
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-fg">Therapeutic area repositories</h2>
          <p className="text-sm text-muted mt-0.5 max-w-2xl">
            Each card links an IKM repository. Document counts come from the document service. Inactive hides an area from new doctor registration only.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1 sm:flex-none sm:w-56">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or id…"
              className={`${filterInputClass} w-full pl-9 pr-3`}
            />
          </div>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            className={`${filterInputClass} px-3`}
            aria-label="Filter by status"
          >
            <option value="all">All status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
          <button type="button" className={secondaryButton} onClick={() => load()} disabled={loading} aria-label="Refresh">
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <button type="button" className={secondaryButton} onClick={seedStandard} disabled={seeding}>
            {seeding ? <Loader2 className="w-4 h-4 animate-spin" /> : <Layers className="w-4 h-4" />}
            {seeding ? 'Adding…' : 'Add standard eye-care therapeutic areas'}
          </button>
          <button type="button" className={primaryButton} onClick={() => setShowCreate(true)}>
            <Plus className="w-4 h-4" /> Create
          </button>
        </div>
      </div>

      {seedResult && (
        <div className="rounded-xl border border-line bg-card px-4 py-3 text-sm space-y-1">
          <div className="flex items-start justify-between gap-3">
            <p className="font-semibold text-fg">
              Standard therapeutic areas: {seedResult.created.length} created, {seedResult.existing.length} already existed
              {seedResult.failed.length ? `, ${seedResult.failed.length} failed` : ''}
            </p>
            <button type="button" aria-label="Dismiss" onClick={() => setSeedResult(null)} className="p-0.5 text-muted hover:text-muted">
              <X className="w-4 h-4" />
            </button>
          </div>
          {seedResult.created.length > 0 && (
            <p className="text-xs text-link">Created: {seedResult.created.map((a) => a.name).join(', ')}</p>
          )}
          {seedResult.existing.length > 0 && <p className="text-xs text-muted">Existing: {seedResult.existing.join(', ')}</p>}
          {seedResult.failed.map((f) => (
            <p key={f.name} className="text-xs text-err-fg">
              {f.name}: {f.error}
            </p>
          ))}
        </div>
      )}

      {loading && !areas.length ? (
        <div className="rounded-xl border border-line bg-card p-12 text-center text-sm text-muted">
          Loading repositories…
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-xl border border-dashed border-line bg-card p-12 text-center">
          <p className="text-sm font-medium text-fg">No therapeutic areas yet</p>
          <p className="text-sm text-muted mt-1">Create one to provision an IKM repository and assign Physical MSLs.</p>
          <button type="button" className={`${primaryButton} mt-4`} onClick={() => setShowCreate(true)}>
            <Plus className="w-4 h-4" /> Create
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4">
          {filtered.map((area) => {
            const meta = repoMeta.get(area.repository_id);
            const docCount = meta?.document_count;
            const isActive = area.active !== false;
            return (
              <article
                key={area.repository_id}
                className="group flex flex-col rounded-xl border border-line bg-card shadow-sm hover:shadow-md hover:border-selb transition-all"
              >
                <div className="p-4 pb-3 flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className="text-sm font-semibold text-fg truncate" title={area.name}>
                      {area.name}
                    </h3>
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {area.default_flag ? <span className={statusBadge('cyan')}>Default</span> : null}
                      {isComingSoon(area) ? <span className={statusBadge('amber')}>Coming soon</span> : null}
                    </div>
                  </div>
                  <button
                    type="button"
                    title={
                      isActive
                        ? 'Doctors can select this area when registering. Click to mark inactive.'
                        : 'Hidden from doctor registration. Click to mark active.'
                    }
                    onClick={() => toggleRegistration(area)}
                    className={statusBadge(isActive ? 'blue' : 'slate')}
                  >
                    {isActive ? 'Active' : 'Inactive'}
                  </button>
                </div>
                {(area.scope_keywords || []).length > 0 && (
                  <div className="px-4 pb-2 flex flex-wrap gap-1">
                    {(area.scope_keywords || []).slice(0, 6).map((kw) => (
                      <span key={kw} className="rounded-full bg-selbg px-2 py-0.5 text-[10px] font-medium text-ok-fg">
                        {kw}
                      </span>
                    ))}
                    {(area.scope_keywords || []).length > 6 && (
                      <span className="text-[10px] text-muted self-center">+{(area.scope_keywords || []).length - 6}</span>
                    )}
                  </div>
                )}
                <div className="px-4 pb-3 space-y-1 text-xs text-muted">
                  <p>{typeof docCount === 'number' ? `${docCount} documents in repository` : 'Document count unavailable'}</p>
                  <p className="flex items-start gap-1.5">
                    <UserRound className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    <span>{assignedMslLine(area, msls)}</span>
                  </p>
                </div>
                <div className="mt-auto border-t border-line flex divide-x divide-line">
                  <button
                    type="button"
                    className="flex-1 inline-flex items-center justify-center gap-1.5 py-2.5 text-xs font-semibold text-fg hover:bg-soft"
                    onClick={() => openEdit(area)}
                  >
                    <Pencil className="w-3.5 h-3.5" aria-hidden="true" /> Edit
                  </button>
                  <button
                    type="button"
                    disabled={isPlaceholder(area)}
                    title={isPlaceholder(area) ? 'Link a repository (Edit) before assigning Physical MSLs.' : undefined}
                    className="flex-1 inline-flex items-center justify-center gap-1.5 py-2.5 text-xs font-semibold text-ok-fg hover:bg-selbg disabled:opacity-40 disabled:hover:bg-transparent"
                    onClick={() => openAssign(area)}
                  >
                    <UserPlus className="w-3.5 h-3.5" /> Assign
                  </button>
                  <button
                    type="button"
                    className="flex-1 inline-flex items-center justify-center gap-1.5 py-2.5 text-xs font-semibold text-err-fg hover:bg-err-bg"
                    onClick={() => setDeleteTarget(area)}
                  >
                    <Trash2 className="w-3.5 h-3.5" /> Delete
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {editArea && (
        <div className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-scrim" role="presentation" onClick={() => setEditArea(null)}>
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Edit therapeutic area"
            className="w-full max-w-md max-h-[90vh] overflow-y-auto rounded-2xl bg-card border border-line shadow-xl p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold">Edit therapeutic area</h3>
              <button type="button" className="p-1 rounded-lg hover:bg-soft" onClick={() => setEditArea(null)} aria-label="Close">
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={saveEdit} className="space-y-3" noValidate>
              <label className={labelClass}>
                Name
                <input value={editName} onChange={(e) => setEditName(e.target.value)} className={`mt-1 ${inputClass}`} autoFocus />
              </label>
              <label className={labelClass}>
                Description
                <input value={editDescription} onChange={(e) => setEditDescription(e.target.value)} className={`mt-1 ${inputClass}`} />
              </label>
              <label className={labelClass}>
                Scope keywords (comma-separated)
                <textarea rows={3} value={editKeywords} onChange={(e) => setEditKeywords(e.target.value)} className={`mt-1 ${inputClass}`} />
              </label>
              <label className={labelClass}>
                Sort order
                <input value={editOrder} onChange={(e) => setEditOrder(e.target.value)} inputMode="numeric" className={`mt-1 ${inputClass}`} />
              </label>
              <label className="flex items-center gap-2 text-sm text-fg">
                <input type="checkbox" checked={editEnabled} onChange={(e) => setEditEnabled(e.target.checked)} className="h-4 w-4 accent-accent" />
                Enabled (doctors can choose it; off shows “Coming soon”)
              </label>
              {(isPlaceholder(editArea) || editEnabled) && isPlaceholder(editArea) && (
                <label className={labelClass}>
                  Link existing IKM repository id (leave blank to create one)
                  <input value={editRepoId} onChange={(e) => setEditRepoId(e.target.value)} className={`mt-1 ${inputClass}`} />
                </label>
              )}
              {editError && (
                <p className="text-xs text-err-fg" role="alert">{editError}</p>
              )}
              <div className="flex justify-end gap-2">
                <button type="button" className={secondaryButton} onClick={() => setEditArea(null)}>
                  Cancel
                </button>
                <button type="submit" className={primaryButton} disabled={editSaving}>
                  {editSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : null} Save
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showCreate && (
        <div
          className="fixed inset-0 z-[80] flex items-center justify-center p-4 bg-scrim"
          role="presentation"
          onClick={() => setShowCreate(false)}
        >
          <div
            className="w-full max-w-md rounded-2xl bg-card border border-line shadow-xl p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between">
              <h3 className="text-base font-semibold">Create therapeutic area</h3>
              <button type="button" className="p-1 rounded-lg hover:bg-soft" onClick={() => setShowCreate(false)} aria-label="Close">
                <X className="w-5 h-5" />
              </button>
            </div>
            <form onSubmit={create} className="space-y-3">
              <label className={labelClass}>
                Name
                <input value={name} onChange={(e) => setName(e.target.value)} className={`mt-1 ${inputClass}`} required minLength={3} autoFocus />
              </label>
              <label className={labelClass}>
                Description
                <input value={description} onChange={(e) => setDescription(e.target.value)} className={`mt-1 ${inputClass}`} />
              </label>
              <label className={labelClass}>
                Owner Physical MSL
                <select value={owner} onChange={(e) => setOwner(e.target.value)} className={`mt-1 ${inputClass}`}>
                  {msls.map((msl) => (
                    <option key={msl.user_id} value={msl.user_id}>
                      {msl.display_name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 text-xs text-muted">
                <input type="checkbox" checked={asDefault} onChange={(e) => setAsDefault(e.target.checked)} />
                Default for new doctor registrations
              </label>
              <div className="flex justify-end gap-2 pt-2">
                <button type="button" className={secondaryButton} onClick={() => setShowCreate(false)}>
                  Cancel
                </button>
                <button type="submit" disabled={saving || name.trim().length < 3} className={primaryButton}>
                  {saving ? 'Creating…' : 'Create repository'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div
          className="fixed inset-0 z-[90] flex items-center justify-center p-4 bg-scrim"
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-ta-title"
          onClick={() => !deleteBusy && setDeleteTarget(null)}
        >
          <div
            className="w-full max-w-md rounded-2xl bg-card border border-line shadow-xl p-6 space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 id="delete-ta-title" className="text-base font-semibold text-fg">
              Remove therapeutic area?
            </h3>
            <p className="text-sm text-muted">
              <span className="font-medium text-fg">{deleteTarget.name}</span> will be removed from Digital MSL.
              Doctors lose access to this area. Documents stay in the IKM document service unless you delete the repository there separately.
            </p>
            {typeof repoMeta.get(deleteTarget.repository_id)?.document_count === 'number' &&
            (repoMeta.get(deleteTarget.repository_id)?.document_count ?? 0) > 0 ? (
              <p className="text-xs text-warn-fg bg-warn-bg border border-warn-line rounded-lg px-3 py-2">
                This repository has {repoMeta.get(deleteTarget.repository_id)?.document_count} document(s) in IKM.
              </p>
            ) : null}
            <div className="flex justify-end gap-2 pt-2">
              <button type="button" className={secondaryButton} disabled={deleteBusy} onClick={() => setDeleteTarget(null)}>
                Cancel
              </button>
              <button type="button" className={dangerButton} disabled={deleteBusy} onClick={() => confirmDelete()}>
                {deleteBusy ? 'Removing…' : 'Remove area'}
              </button>
            </div>
          </div>
        </div>
      )}

      {assignArea && (
        <div className="fixed inset-0 z-[80] flex justify-end bg-scrim" role="presentation" onClick={() => setAssignId(null)}>
          <div
            className="w-full max-w-md h-full bg-card border-l border-line shadow-xl flex flex-col overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="shrink-0 flex items-start justify-between gap-3 p-5 border-b border-line">
              <div>
                <h3 className="text-base font-semibold">Assign Physical MSLs</h3>
                <p className="text-sm text-muted mt-0.5">{assignArea.name}</p>
              </div>
              <button type="button" className="p-1.5 rounded-lg hover:bg-soft" onClick={() => setAssignId(null)} aria-label="Close">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-5 space-y-4">
              <p className="text-xs text-muted">
                Assigned MSLs can load documents and answer escalations for this repository. Pick one as owner for routing.
              </p>
              <div className="rounded-lg border border-line divide-y divide-line">
                {msls.map((msl) => {
                  const checked = draftMslIds.includes(msl.user_id);
                  return (
                    <label key={msl.user_id} className="flex items-center gap-3 px-3 py-2.5 text-sm cursor-pointer hover:bg-soft">
                      <input type="checkbox" checked={checked} onChange={() => toggleDraftMsl(msl.user_id)} />
                      {msl.display_name}
                    </label>
                  );
                })}
              </div>
              {draftMslIds.length > 0 && (
                <label className={labelClass}>
                  Owner (primary MSL)
                  <select
                    value={draftOwner}
                    onChange={(e) => setDraftOwner(e.target.value)}
                    className={`mt-1 ${inputClass}`}
                  >
                    {draftMslIds.map((id) => (
                      <option key={id} value={id}>
                        {msls.find((m) => m.user_id === id)?.display_name || id}
                      </option>
                    ))}
                  </select>
                </label>
              )}
            </div>
            <div className="shrink-0 p-5 border-t border-line flex justify-end gap-2">
              <button type="button" className={secondaryButton} onClick={() => setAssignId(null)}>
                Cancel
              </button>
              <button type="button" className={primaryButton} disabled={assignSaving} onClick={() => saveAssignment()}>
                {assignSaving ? 'Saving…' : 'Save assignment'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
