import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import {
  listTemplates,
  listTherapeuticAreas,
  saveTemplate,
  type MessageTemplate,
  type PortalUser,
  type TherapeuticArea,
} from '../../services/digitalMslService';
import { useToast } from '../../contexts/ToastContext';
import { inputClass, primaryButton, secondaryButton, statusBadge } from '../../config/pageTheme';
import { FormField } from '../../components/ui/FormField';
import { SaveBar } from '../../components/ui/SaveBar';
import { SaveButton } from '../../components/ui/SaveButton';
import { SettingsCard } from './SettingsCard';

export function WordingSection() {
  const toast = useToast();
  const [loaded, setLoaded] = useState<MessageTemplate[] | null>(null);
  const [draft, setDraft] = useState<MessageTemplate[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    listTemplates('msl_response_to_doctor')
      .then((t) => {
        setLoaded(t);
        setDraft(t);
        if (t[0]) setSelectedId(t[0].template_id);
      })
      .catch(() => setError('We could not load the reply wording.'));
  }, []);

  const selected = draft.find((t) => t.template_id === selectedId) ?? draft[0];
  const original = loaded?.find((t) => t.template_id === selected?.template_id);
  const dirty = !!selected && !!original && (selected.name !== original.name || selected.body !== original.body);
  const nameError = selected && selected.name.trim().length < 2 ? 'Give the template a name.' : '';
  const bodyError = selected && selected.body.trim().length < 10 ? 'The reply needs some text.' : '';

  const patch = (field: 'name' | 'body', value: string) =>
    setDraft((prev) => prev.map((t) => (t.template_id === selected?.template_id ? { ...t, [field]: value } : t)));

  const save = async () => {
    if (!selected || nameError || bodyError) return;
    setSaving(true);
    try {
      const saved = await saveTemplate(selected);
      setLoaded((prev) => (prev ?? []).map((t) => (t.template_id === saved.template_id ? saved : t)));
      toast.success('Reply wording saved.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save the reply wording.');
    } finally {
      setSaving(false);
    }
  };
  const discard = () => setDraft(loaded ?? []);

  return (
    <>
      <SettingsCard
        title="Reply wording"
        description="The letter used when you reply to a doctor. Variables are filled in when you send."
        testId="card-wording"
        footer={<SaveButton onClick={save} disabled={!dirty || !!nameError || !!bodyError} saving={saving} testId="wording-save" />}
      >
        {error ? <p className="text-sm text-err-fg" role="alert">{error}</p> : null}
        {loaded === null && !error ? <div className="h-40 animate-pulse rounded-lg bg-soft" role="status" aria-busy="true" aria-label="Loading reply wording" /> : null}
        {loaded && loaded.length === 0 ? <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">No reply template is configured yet. Ask an administrator.</p> : null}
        {selected ? (
          <>
            {draft.length > 1 ? (
              <FormField id="wording-template" label="Template">
                <select id="wording-template" className={inputClass} value={selected.template_id} onChange={(e) => setSelectedId(e.target.value)}>
                  {draft.map((t) => <option key={t.template_id} value={t.template_id}>{t.name}</option>)}
                </select>
              </FormField>
            ) : null}
            <FormField id="wording-name" label="Template name" error={dirty ? nameError : ''}>
              <input id="wording-name" className={inputClass} value={selected.name} onChange={(e) => patch('name', e.target.value)} aria-invalid={!!nameError && dirty} />
            </FormField>
            <FormField
              id="wording-body"
              label="Reply text"
              helper="Variables: {{doctor_name}} {{question}} {{draft_body}} {{msl_name}} {{case_id}}"
              error={dirty ? bodyError : ''}
            >
              <textarea id="wording-body" rows={12} className={`${inputClass} font-mono`} value={selected.body} onChange={(e) => patch('body', e.target.value)} aria-invalid={!!bodyError && dirty} />
            </FormField>
          </>
        ) : null}
      </SettingsCard>
      <SaveBar dirty={dirty} saving={saving} canSave={!nameError && !bodyError} onSave={save} onDiscard={discard} />
    </>
  );
}

export function MyAreasSection({ user }: { user: PortalUser }) {
  const [areas, setAreas] = useState<TherapeuticArea[] | null>(null);
  const [error, setError] = useState('');
  const load = () => {
    setError('');
    listTherapeuticAreas()
      .then(setAreas)
      .catch(() => setError('We could not load your therapeutic areas.'));
  };
  useEffect(load, []);
  const mine = useMemo(
    () => (areas ?? []).filter((a) => (a.msl_ids ?? []).includes(user.user_id) || a.owner_msl_id === user.user_id),
    [areas, user.user_id],
  );

  return (
    <SettingsCard
      title="My therapeutic areas"
      description="Areas whose doctors and questions you cover. An administrator assigns these."
      testId="card-my-areas"
    >
      {error ? (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-err-b bg-err-bg px-4 py-3 text-sm text-err-fg" role="alert">
          <span>{error}</span>
          <button type="button" className={secondaryButton} onClick={load}>Retry</button>
        </div>
      ) : null}
      {areas === null && !error ? <div className="h-20 animate-pulse rounded-lg bg-soft" role="status" aria-busy="true" aria-label="Loading areas" /> : null}
      {areas && mine.length === 0 ? <p className="rounded-lg border border-dashed border-line px-4 py-6 text-center text-sm text-muted">No therapeutic areas are assigned to you yet.</p> : null}
      {mine.length > 0 ? (
        <ul className="space-y-3">
          {mine.map((a) => {
            const enabled = a.enabled ?? a.active ?? true;
            return (
              <li key={a.repository_id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line bg-soft px-4 py-3">
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-fg">{a.name}</p>
                  {a.description ? <p className="line-clamp-2 text-xs text-muted">{a.description}</p> : null}
                </div>
                <span className={statusBadge(enabled ? 'blue' : 'slate')}>{enabled ? (a.owner_msl_id === user.user_id ? 'Owner' : 'Member') : 'Coming soon'}</span>
              </li>
            );
          })}
        </ul>
      ) : null}
    </SettingsCard>
  );
}
