import { useEffect, useState } from 'react';
import { Loader2, Plus, Trash2 } from 'lucide-react';
import { getPolicy, savePolicy, type DmslPolicy } from '../../services/digitalMslService';
import { useToast } from '../../contexts/ToastContext';
import { inputClass, primaryButton, secondaryButton } from '../../config/pageTheme';
import { SaveBar } from '../../components/ui/SaveBar';
import { SaveButton } from '../../components/ui/SaveButton';
import { SettingsCard } from './SettingsCard';

const EMPTY: DmslPolicy = { molecules: [], confidence_threshold: 0.9, claim_min_score: 0.7 };

export function ScopeSection() {
  const toast = useToast();
  const [loaded, setLoaded] = useState<DmslPolicy | null>(null);
  const [policy, setPolicy] = useState<DmslPolicy>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    getPolicy()
      .then((p) => {
        setLoaded(p);
        setPolicy(p);
      })
      .catch(() => setError('We could not load the scope policy.'));
  }, []);

  const dirty = !!loaded && JSON.stringify(policy) !== JSON.stringify(loaded);
  const blank = policy.molecules.some((m) => !m.name.trim());

  const save = async () => {
    if (blank) return;
    setSaving(true);
    try {
      const saved = await savePolicy(policy);
      setLoaded(saved);
      setPolicy(saved);
      toast.success('Scope policy saved.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save the scope policy.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <SettingsCard
        title="In-scope medicines (optional)"
        description="Molecule scope for Digital MSL validation. Repositories and documents are managed per therapeutic area by assigned Physical MSLs."
        testId="card-scope"
        badge={
          <button type="button" className={secondaryButton} onClick={() => setPolicy((p) => ({ ...p, molecules: [...p.molecules, { name: '', aliases: '' }] }))}>
            <Plus className="h-4 w-4" aria-hidden="true" /> Add
          </button>
        }
        footer={<SaveButton onClick={save} disabled={!dirty || blank} saving={saving} testId="scope-save" />}
      >
        {error ? <p className="text-sm text-err-fg" role="alert">{error}</p> : null}
        {loaded === null && !error ? <div className="h-24 animate-pulse rounded-lg bg-soft" role="status" aria-busy="true" aria-label="Loading scope policy" /> : null}
        {loaded && policy.molecules.length === 0 ? <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">No medicines listed. Add one to restrict scope.</p> : null}
        {policy.molecules.map((mol, i) => (
          <div key={i} className="grid items-start gap-2 sm:grid-cols-[1fr_2fr_44px]">
            <input
              value={mol.name}
              placeholder="Scientific name"
              aria-label={`Medicine ${i + 1} scientific name`}
              aria-invalid={dirty && !mol.name.trim()}
              onChange={(e) => setPolicy((p) => ({ ...p, molecules: p.molecules.map((m, idx) => (idx === i ? { ...m, name: e.target.value } : m)) }))}
              className={inputClass}
            />
            <input
              value={mol.aliases}
              placeholder="Brand names, comma-separated"
              aria-label={`Medicine ${i + 1} brand names`}
              onChange={(e) => setPolicy((p) => ({ ...p, molecules: p.molecules.map((m, idx) => (idx === i ? { ...m, aliases: e.target.value } : m)) }))}
              className={inputClass}
            />
            <button
              type="button"
              className="flex h-11 w-11 items-center justify-center rounded-[10px] text-err-fg hover:bg-err-bg"
              onClick={() => setPolicy((p) => ({ ...p, molecules: p.molecules.filter((_, idx) => idx !== i) }))}
              aria-label={`Remove medicine ${i + 1}`}
            >
              <Trash2 className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        ))}
        {dirty && blank ? <p className="text-xs text-err-fg" role="alert">Every medicine needs a scientific name.</p> : null}
      </SettingsCard>
      <SaveBar dirty={dirty} saving={saving} canSave={!blank} onSave={save} onDiscard={() => setPolicy(loaded ?? EMPTY)} />
    </>
  );
}
