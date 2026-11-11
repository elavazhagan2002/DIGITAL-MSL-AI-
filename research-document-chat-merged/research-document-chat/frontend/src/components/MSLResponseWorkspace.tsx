import React, { useEffect, useState, useCallback } from 'react';
import {
  FileText,
  Save,
  Send,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Search,
  Plus,
  Eye,
  BookOpen,
  UserCheck,
  ShieldAlert,
  ArrowRight,
  ExternalLink,
  RefreshCw,
  X,
  Sparkles,
} from 'lucide-react';
import {
  listCases,
  getCase,
  saveCaseDraft,
  submitCaseReview,
  approveCaseDraft,
  sendCaseResponse,
  listTemplates,
  renderTemplateBody,
  type MslCase,
  type MslCaseReference,
  type MslCaseClaim,
  type MessageTemplate,
} from '../services/digitalMslService';
import {
  searchMedicalLiterature,
  type LiteraturePreview,
} from '../services/medicalLiteracyService';
import { useToast } from '../contexts/ToastContext';

interface MSLResponseWorkspaceProps {
  userId: string;
}

export const MSLResponseWorkspace: React.FC<MSLResponseWorkspaceProps> = ({ userId }) => {
  const toast = useToast();
  const [cases, setCases] = useState<MslCase[]>([]);
  const [loadingCases, setLoadingCases] = useState(false);
  const [selectedCaseId, setSelectedCaseId] = useState<string | null>(null);
  const [activeCase, setActiveCase] = useState<MslCase | null>(null);
  const [loadingCase, setLoadingCase] = useState(false);

  // Center panel state
  const [draftText, setDraftText] = useState('');
  const [isPreview, setIsPreview] = useState(false);
  const [saving, setSaving] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [approving, setApproving] = useState(false);
  const [sending, setSending] = useState(false);

  // Right panel state (Evidence & References)
  const [references, setReferences] = useState<MslCaseReference[]>([]);
  const [claims, setClaims] = useState<MslCaseClaim[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchSource, setSearchSource] = useState<'pubmed' | 'clinicaltrials'>('pubmed');
  const [searchResults, setSearchResults] = useState<LiteraturePreview[]>([]);
  const [searching, setSearching] = useState(false);
  const [showAddExternalModal, setShowAddExternalModal] = useState(false);
  const [externalTitle, setExternalTitle] = useState('');
  const [externalSource, setExternalSource] = useState('');
  const [externalCitation, setExternalCitation] = useState('');
  const [replyTemplates, setReplyTemplates] = useState<MessageTemplate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = useState('');

  // Load cases list
  const loadCases = useCallback(async () => {
    setLoadingCases(true);
    try {
      const assigned = await listCases({ assignedMslId: userId });
      const unassigned = userId !== 'msl_default' ? await listCases({ assignedMslId: 'msl_default' }) : [];
      const byId = new Map<string, MslCase>();
      for (const item of [...assigned, ...unassigned]) byId.set(item.case_id, item);
      const data = Array.from(byId.values());
      setCases(data);
      if (data.length > 0 && !selectedCaseId) {
        setSelectedCaseId(data[0].case_id);
      }
    } catch {
      toast.error('Could not load MSL assigned cases.');
    } finally {
      setLoadingCases(false);
    }
  }, [userId, selectedCaseId, toast]);

  useEffect(() => {
    loadCases();
  }, [loadCases]);

  useEffect(() => {
    listTemplates('msl_response_to_doctor')
      .then((tpls) => {
        setReplyTemplates(tpls);
        if (tpls[0]) setSelectedTemplateId(tpls[0].template_id);
      })
      .catch(() => undefined);
  }, []);

  // Load selected case
  useEffect(() => {
    if (!selectedCaseId) {
      setActiveCase(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoadingCase(true);
      try {
        const c = await getCase(selectedCaseId);
        if (!cancelled && c) {
          setActiveCase(c);
          setDraftText(c.draft_response || c.resolution || '');
          setReferences(c.references || []);
          setClaims(c.claims || []);
        }
      } catch {
        if (!cancelled) toast.error('Could not load case context.');
      } finally {
        if (!cancelled) setLoadingCase(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedCaseId, toast]);

  // Save Draft
  const handleSaveDraft = async () => {
    if (!selectedCaseId) return;
    setSaving(true);
    try {
      const updated = await saveCaseDraft(selectedCaseId, draftText, references, claims, userId);
      setActiveCase(updated);
      toast.success('Response draft and references saved successfully.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not save draft.');
    } finally {
      setSaving(false);
    }
  };

  // Submit for Review
  const handleSubmitReview = async () => {
    if (!selectedCaseId) return;
    if (!draftText.trim()) {
      toast.error('Please draft a response before submitting for review.');
      return;
    }
    setSubmitting(true);
    try {
      await saveCaseDraft(selectedCaseId, draftText, references, claims, userId);
      const updated = await submitCaseReview(selectedCaseId, userId, 'Ready for scientific/medical approval');
      setActiveCase(updated);
      toast.success('Submitted for scientific/compliance review.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not submit review.');
    } finally {
      setSubmitting(false);
    }
  };

  // Approve Draft
  const handleApproveDraft = async () => {
    if (!selectedCaseId) return;
    setApproving(true);
    try {
      const updated = await approveCaseDraft(selectedCaseId, userId, 'Approved by Physical MSL');
      setActiveCase(updated);
      toast.success('Draft approved. Response is ready for delivery to Doctor.');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Could not approve draft.');
    } finally {
      setApproving(false);
    }
  };

  // Send Response to Doctor
  const handleSendResponse = async () => {
    if (!selectedCaseId) return;
    setSending(true);
    try {
      const updated = await sendCaseResponse(selectedCaseId, userId);
      setActiveCase(updated);
      toast.success(`Response delivered to Doctor via ${updated.channel}.`);
      await loadCases();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to deliver response.');
    } finally {
      setSending(false);
    }
  };

  // Search literature
  const handleSearchLiterature = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;
    setSearching(true);
    try {
      const res = await searchMedicalLiterature({
        repository_id: 'default',
        query: searchQuery.trim(),
        connectors: [searchSource],
      });
      setSearchResults(res.results || []);
    } catch {
      toast.error('Literature search failed.');
    } finally {
      setSearching(false);
    }
  };

  // Add reference from search
  const handleAddSearchResultAsRef = (item: LiteraturePreview) => {
    const newRef: MslCaseReference = {
      id: `ref_${Date.now()}`,
      title: item.title,
      source: item.source === 'pubmed' ? `PubMed (PMID: ${item.record_id})` : `ClinicalTrials.gov (${item.record_id})`,
      quote: item.summary?.slice(0, 200) || '',
      is_external: true,
      verification_status: 'Verified External Literature',
    };
    setReferences((prev) => [...prev, newRef]);
    toast.success(`Added reference: ${item.title.slice(0, 40)}…`);
  };

  // Add custom external reference
  const handleAddExternalRef = (e: React.FormEvent) => {
    e.preventDefault();
    if (!externalTitle.trim()) return;
    const newRef: MslCaseReference = {
      id: `ref_ext_${Date.now()}`,
      title: externalTitle.trim(),
      source: externalSource.trim() || 'Scientific Publication / Guideline',
      quote: externalCitation.trim(),
      is_external: true,
      verification_status: 'External Reference Recorded',
    };
    setReferences((prev) => [...prev, newRef]);
    setShowAddExternalModal(false);
    setExternalTitle('');
    setExternalSource('');
    setExternalCitation('');
    toast.success('External reference recorded.');
  };

  // Quick insert classification tag into draft
  const insertStatement = (type: 'supported' | 'unsupported' | 'commentary') => {
    let tag = '';
    if (type === 'supported') tag = '\n\n**[Evidence-Supported Statement]:** ';
    if (type === 'unsupported') tag = '\n\n**[Requires Additional Evidence]:** ';
    if (type === 'commentary') tag = '\n\n**[MSL Scientific Interpretation & Clinical Commentary]:** ';
    setDraftText((prev) => prev + tag);
  };

  const insertReplyTemplate = () => {
    const tpl = replyTemplates.find((t) => t.template_id === selectedTemplateId) || replyTemplates[0];
    if (!tpl || !activeCase) return;
    setDraftText(
      renderTemplateBody(tpl.body, {
        doctor_name: activeCase.doctor_id || 'Doctor',
        question: activeCase.question,
        draft_body: draftText,
        msl_name: 'Medical Science Liaison',
        case_id: activeCase.case_id,
      }),
    );
  };

  const isApproved = activeCase?.approval_status === 'approved' || activeCase?.draft_status === 'approved';
  const isSent = activeCase?.status === 'resolved' || activeCase?.draft_status === 'sent';

  return (
    <div className="space-y-6">
      {/* Top Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-fg flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-link" />
            Response Workspace — Evidence-Assisted Response Drafting
          </h1>
          <p className="text-xs text-muted mt-1">
            Consult retrieved repository evidence, search verified external literature, apply clinical judgment, and draft
            approved scientific responses.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {activeCase && (
            <span
              className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider border ${
                isSent
                  ? 'bg-accent text-brandcyan border-selb'
                  : isApproved
                  ? 'bg-accent text-brandcyan border-selb'
                  : 'bg-warn-fg text-warn-fg border-warn-line'
              }`}
            >
              {isSent ? 'Delivered' : isApproved ? 'Approved for Sending' : activeCase.approval_status || 'Draft'}
            </span>
          )}
        </div>
      </div>

      {/* 3-Panel Professional Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-start">
        {/* LEFT PANEL: Case Context (3 cols) */}
        <div className="lg:col-span-3 bg-card border border-line rounded-xl overflow-hidden flex flex-col h-[760px] shadow-lg">
          <div className="p-3.5 border-b border-line bg-soft flex items-center justify-between">
            <span className="text-xs font-bold text-fg">Assigned Inquiries</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-soft text-muted font-mono">
              {cases.length}
            </span>
          </div>

          <div className="p-2 border-b border-line bg-card max-h-48 overflow-y-auto space-y-1">
            {cases.map((c) => (
              <button
                key={c.case_id}
                onClick={() => setSelectedCaseId(c.case_id)}
                className={`w-full text-left p-2.5 rounded-lg text-xs transition ${
                  selectedCaseId === c.case_id
                    ? 'bg-selbg text-ok-fg border border-selb'
                    : 'text-muted hover:bg-soft hover:text-fg'
                }`}
              >
                <div className="flex items-center justify-between text-[10px] mb-1">
                  <span className="font-mono text-muted">{c.case_id.slice(0, 12)}…</span>
                  <span className="uppercase text-[9px] font-bold text-muted">{c.channel}</span>
                </div>
                <p className="line-clamp-2 text-fg text-[11px] leading-snug">{c.queue_notice || c.question}</p>
              </button>
            ))}
          </div>

          {activeCase ? (
            <div className="flex-1 p-4 overflow-y-auto space-y-4 text-xs">
              <div>
                <span className="text-[10px] uppercase font-bold tracking-wider text-muted block mb-1">
                  Original Doctor Question
                </span>
                <div className="p-3 bg-soft rounded-lg border border-line text-fg leading-relaxed text-xs">
                  {activeCase.question}
                </div>
              </div>

              <div>
                <span className="text-[10px] uppercase font-bold tracking-wider text-muted block mb-1">
                  Inquirer & Routing Context
                </span>
                <div className="space-y-1 text-[11px] text-fg bg-soft p-2.5 rounded-lg border border-line">
                  <p>
                    <span className="text-muted">Doctor ID:</span> {activeCase.doctor_id}
                  </p>
                  <p>
                    <span className="text-muted">Originating Channel:</span>{' '}
                    <span className="uppercase font-semibold text-brandcyan">{activeCase.channel}</span>
                  </p>
                  <p>
                    <span className="text-muted">Assigned MSL:</span> {activeCase.assigned_msl_id}
                  </p>
                </div>
              </div>

              <div>
                <span className="text-[10px] uppercase font-bold tracking-wider text-warn-fg block mb-1">
                  Escalation Trigger Codes
                </span>
                <div className="flex flex-wrap gap-1">
                  {activeCase.reason_codes.map((rc) => (
                    <span
                      key={rc}
                      className="px-2 py-0.5 rounded text-[10px] font-mono bg-warn-fg text-warn-fg border border-warn-line"
                    >
                      {rc}
                    </span>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-xs text-muted p-6 text-center">
              Select an escalated inquiry to load context.
            </div>
          )}
        </div>

        {/* CENTER PANEL: Response Editor (5 cols) */}
        <div className="lg:col-span-5 bg-card border border-line rounded-xl overflow-hidden flex flex-col h-[760px] shadow-lg">
          {/* Editor Header */}
          <div className="p-3.5 border-b border-line bg-soft flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-fg">Scientific Response Editor</span>
              <span className="text-[10px] text-muted">v{activeCase?.version || 1}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setIsPreview(!isPreview)}
                className="px-2.5 py-1 text-[11px] rounded bg-soft hover:bg-soft text-fg flex items-center gap-1"
              >
                <Eye className="w-3 h-3 text-brandcyan" /> {isPreview ? 'Editor' : 'Preview'}
              </button>
            </div>
          </div>

          {/* Quick statement classification toolbar */}
          <div className="px-3.5 py-2 border-b border-line bg-soft flex flex-wrap items-center gap-1.5 text-[10px]">
            {replyTemplates.length > 0 && (
              <>
                <select
                  value={selectedTemplateId}
                  onChange={(e) => setSelectedTemplateId(e.target.value)}
                  className="rounded border border-line bg-card px-2 py-0.5 text-[10px] text-fg"
                >
                  {replyTemplates.map((tpl) => (
                    <option key={tpl.template_id} value={tpl.template_id}>
                      {tpl.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  onClick={insertReplyTemplate}
                  className="px-2 py-0.5 rounded bg-selbg text-ok-fg border border-selb hover:bg-selbg"
                >
                  Insert template
                </button>
              </>
            )}
            <span className="text-muted font-medium">Insert Tag:</span>
            <button
              type="button"
              onClick={() => insertStatement('supported')}
              className="px-2 py-0.5 rounded bg-selbg text-ok-fg border border-selb hover:bg-selbg"
            >
              + Evidence Supported
            </button>
            <button
              type="button"
              onClick={() => insertStatement('commentary')}
              className="px-2 py-0.5 rounded bg-selbg text-ok-fg border border-selb hover:bg-selbg"
            >
              + Clinical Commentary
            </button>
            <button
              type="button"
              onClick={() => insertStatement('unsupported')}
              className="px-2 py-0.5 rounded bg-warn-bg text-warn-fg border border-warn-line hover:bg-warn-bg"
            >
              + Unresolved Claim
            </button>
          </div>

          {/* Textarea or Preview */}
          <div className="flex-1 p-4 overflow-y-auto">
            {isPreview ? (
              <div className="p-4 bg-soft rounded-xl border border-line text-xs text-fg leading-relaxed whitespace-pre-wrap min-h-full">
                {draftText ? draftText : <span className="text-muted italic">No response drafted yet.</span>}
              </div>
            ) : (
              <textarea
                value={draftText}
                onChange={(e) => setDraftText(e.target.value)}
                placeholder="Draft the scientific response to the doctor's inquiry. You may synthesize available repository passages, incorporate verified PubMed/ClinicalTrials references, or provide professional MSL interpretation..."
                className="w-full h-full bg-soft border border-line rounded-xl p-4 text-xs text-fg placeholder:text-muted focus:outline-none focus:border-selb transition resize-none leading-relaxed font-sans"
              />
            )}
          </div>

          {/* Action Toolbar */}
          <div className="p-3.5 border-t border-line bg-soft flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleSaveDraft}
                disabled={saving || !selectedCaseId}
                className="px-3 py-1.5 rounded-lg bg-soft hover:bg-soft text-fg text-xs font-semibold inline-flex items-center gap-1.5 transition disabled:opacity-50"
              >
                <Save className="w-3.5 h-3.5 text-brandcyan" />
                {saving ? 'Saving…' : 'Save Draft'}
              </button>

              {!isApproved && (
                <button
                  type="button"
                  onClick={handleSubmitReview}
                  disabled={submitting || !draftText.trim()}
                  className="px-3 py-1.5 rounded-lg bg-accent hover:bg-accent text-accentfg text-xs font-semibold inline-flex items-center gap-1.5 transition disabled:opacity-50"
                >
                  <Clock className="w-3.5 h-3.5" />
                  {submitting ? 'Submitting…' : 'Submit Review'}
                </button>
              )}

              {!isApproved && (
                <button
                  type="button"
                  onClick={handleApproveDraft}
                  disabled={approving || !draftText.trim()}
                  className="px-3 py-1.5 rounded-lg bg-accent hover:bg-accent text-accentfg text-xs font-semibold inline-flex items-center gap-1.5 transition disabled:opacity-50"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  {approving ? 'Approving…' : 'Approve'}
                </button>
              )}
            </div>

            <button
              type="button"
              onClick={handleSendResponse}
              disabled={sending || !isApproved || isSent}
              className={`px-4 py-1.5 rounded-lg text-xs font-bold inline-flex items-center gap-1.5 shadow-md transition ${
                isApproved && !isSent
                  ? 'bg-accent hover:bg-accent text-accentfg cursor-pointer'
                  : 'bg-soft text-muted cursor-not-allowed border border-line'
              }`}
              title={isApproved ? 'Deliver response to Doctor' : 'Draft must be approved before delivery'}
            >
              <Send className="w-3.5 h-3.5" />
              {isSent ? 'Response Sent' : sending ? 'Delivering…' : 'Send to Doctor'}
            </button>
          </div>
        </div>

        {/* RIGHT PANEL: Evidence & References (4 cols) */}
        <div className="lg:col-span-4 bg-card border border-line rounded-xl overflow-hidden flex flex-col h-[760px] shadow-lg">
          {/* Header */}
          <div className="p-3.5 border-b border-line bg-soft flex items-center justify-between">
            <span className="text-xs font-bold text-fg">Evidence & Scientific References</span>
            <button
              type="button"
              onClick={() => setShowAddExternalModal(true)}
              className="text-[11px] px-2 py-1 rounded bg-selbg hover:bg-selbg text-ok-fg font-semibold inline-flex items-center gap-1 transition"
            >
              <Plus className="w-3 h-3" /> Add Source
            </button>
          </div>

          <div className="flex-1 p-4 overflow-y-auto space-y-5 text-xs">
            {/* References attached to this draft */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] uppercase font-bold text-muted">Attached References</span>
                <span className="text-[10px] text-brandcyan font-semibold">{references.length} attached</span>
              </div>
              {references.length > 0 ? (
                <div className="space-y-2">
                  {references.map((ref, idx) => (
                    <div
                      key={ref.id || idx}
                      className="p-2.5 rounded-lg bg-soft border border-line text-[11px] space-y-1 relative group"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <span className="font-semibold text-fg line-clamp-1">{ref.title}</span>
                        <button
                          type="button"
                          onClick={() => setReferences((prev) => prev.filter((_, i) => i !== idx))}
                          className="text-muted hover:text-err-fg text-xs px-1"
                        >
                          ✕
                        </button>
                      </div>
                      <p className="text-muted text-[10px] font-mono">{ref.source}</p>
                      {ref.quote && <p className="text-fg italic text-[10px] line-clamp-2">"{ref.quote}"</p>}
                      <span className="inline-block text-[9px] px-1.5 py-0.2 rounded bg-selbg text-ok-fg font-medium mt-1">
                        {ref.verification_status || 'External Literature'}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-4 rounded-lg bg-soft border border-line text-muted text-center text-[11px]">
                  No external scientific references attached yet. Search below or record an external source.
                </div>
              )}
            </div>

            {/* Live Literature Search */}
            <div className="pt-3 border-t border-line">
              <span className="text-[10px] uppercase font-bold text-muted block mb-2">
                Search Authorized Literature Sources
              </span>
              <form onSubmit={handleSearchLiterature} className="space-y-2">
                <div className="flex gap-2">
                  <select
                    value={searchSource}
                    onChange={(e) => setSearchSource(e.target.value as any)}
                    className="bg-soft border border-line rounded-lg px-2 py-1.5 text-xs text-fg focus:outline-none"
                  >
                    <option value="pubmed">PubMed</option>
                    <option value="clinicaltrials">ClinicalTrials</option>
                  </select>
                  <input
                    type="text"
                    placeholder="Search molecule / trial…"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="flex-1 bg-soft border border-line rounded-lg px-2.5 py-1.5 text-xs text-fg placeholder:text-muted focus:outline-none"
                  />
                  <button
                    type="submit"
                    disabled={searching || !searchQuery.trim()}
                    className="px-3 py-1.5 rounded-lg bg-accent hover:bg-accent text-accentfg font-semibold text-xs disabled:opacity-50"
                  >
                    {searching ? <RefreshCw className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
                  </button>
                </div>
              </form>

              {/* Search Results */}
              <div className="mt-3 space-y-2 max-h-56 overflow-y-auto">
                {searchResults.map((rec) => (
                  <div
                    key={rec.record_id}
                    className="p-2.5 rounded-lg bg-scrim border border-line text-[11px] space-y-1.5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="font-semibold text-fg line-clamp-2">{rec.title}</p>
                      <button
                        type="button"
                        onClick={() => handleAddSearchResultAsRef(rec)}
                        className="px-2 py-0.5 rounded text-[10px] bg-accent text-brandcyan hover:bg-accent font-semibold shrink-0"
                      >
                        + Attach
                      </button>
                    </div>
                    <p className="text-muted text-[10px] font-mono">
                      {rec.record_id} · {rec.source}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Manual External Reference Modal */}
      {showAddExternalModal && (
        <div className="fixed inset-0 z-50 bg-scrim backdrop-blur-sm flex items-center justify-center p-4">
          <form
            onSubmit={handleAddExternalRef}
            className="w-full max-w-md bg-card border border-line rounded-2xl p-6 shadow-2xl space-y-4 text-xs text-fg"
          >
            <div className="flex items-center justify-between pb-3 border-b border-line">
              <h3 className="text-sm font-bold text-fg">Record Verified External Reference</h3>
              <button
                type="button"
                onClick={() => setShowAddExternalModal(false)}
                className="text-muted hover:text-fg p-1 rounded-lg"
              >
                ✕
              </button>
            </div>

            <div>
              <label className="block text-[11px] text-muted mb-1">Publication Title / Clinical Guideline</label>
              <input
                type="text"
                required
                value={externalTitle}
                onChange={(e) => setExternalTitle(e.target.value)}
                placeholder="e.g. American Academy of Ophthalmology Retina Preferred Practice Pattern"
                className="w-full bg-soft border border-line rounded-lg px-3 py-2 text-xs text-fg focus:outline-none"
              />
            </div>

            <div>
              <label className="block text-[11px] text-muted mb-1">Citation Identifier (DOI / PMID / Journal)</label>
              <input
                type="text"
                value={externalSource}
                onChange={(e) => setExternalSource(e.target.value)}
                placeholder="e.g. Ophthalmology 2024;131:120-135 · DOI: 10.1016/j.ophtha.2024..."
                className="w-full bg-soft border border-line rounded-lg px-3 py-2 text-xs text-fg focus:outline-none"
              />
            </div>

            <div>
              <label className="block text-[11px] text-muted mb-1">Relevant Supporting Excerpt</label>
              <textarea
                rows={3}
                value={externalCitation}
                onChange={(e) => setExternalCitation(e.target.value)}
                placeholder="Paste key sentence or evidence passage..."
                className="w-full bg-soft border border-line rounded-lg px-3 py-2 text-xs text-fg focus:outline-none resize-none"
              />
            </div>

            <div className="flex justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setShowAddExternalModal(false)}
                className="px-3 py-1.5 rounded-lg bg-soft text-fg font-semibold"
              >
                Cancel
              </button>
              <button
                type="submit"
                className="px-4 py-1.5 rounded-lg bg-accent hover:bg-accent text-accentfg font-semibold"
              >
                Attach Reference
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
