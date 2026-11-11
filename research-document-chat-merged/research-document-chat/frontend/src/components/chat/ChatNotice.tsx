import { useEffect, useState } from 'react';
import { Info } from 'lucide-react';
import { fetchComplianceNotice, type ComplianceNotice } from '../../services/digitalMslService';
import { Modal } from '../ui/Modal';

/** Loads the versioned compliance notice once. Text is always shown exactly as the server sends it. */
export function useComplianceNotice() {
  const [notice, setNotice] = useState<ComplianceNotice | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    fetchComplianceNotice()
      .then((n) => live && setNotice(n))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, []);
  return { notice, failed };
}

export const ROUTING_SENTENCE = 'Your question will be reviewed by a Physical MSL.';
export const COMPOSER_DISCLAIMER =
  'AI-generated from approved sources. Not medical advice. Report adverse events to [PHARMACOVIGILANCE CONTACT].';

/** First item of a new conversation's thread. Scrolls away with the thread; gone once acknowledged. */
export function NoticeCard({
  notice,
  routedToMsl,
  onAcknowledge,
}: {
  notice: ComplianceNotice;
  routedToMsl: boolean;
  onAcknowledge: () => void;
}) {
  return (
    <section
      aria-label="Compliance notice"
      data-testid="notice-card"
      className="w-full max-w-4xl rounded-2xl border border-selb bg-selbg px-5 py-3.5 text-sm text-fg"
    >
      <div className="flex items-start gap-3">
        <Info className="mt-0.5 h-5 w-5 shrink-0 text-link" aria-hidden="true" />
        <div className="flex min-w-0 flex-1 flex-col gap-3 sm:flex-row sm:items-end sm:gap-5">
          <div className="min-w-0 flex-1 space-y-2">
            <p className="whitespace-pre-wrap leading-relaxed">{notice.text}</p>
            {routedToMsl && <p className="leading-relaxed">{ROUTING_SENTENCE}</p>}
            <p className="text-xs text-muted">Notice version {notice.version}</p>
          </div>
          <button
            type="button"
            onClick={onAcknowledge}
            data-testid="notice-gotit"
            className="inline-flex h-11 shrink-0 items-center justify-center rounded-[10px] bg-accent px-5 text-sm font-semibold text-accentfg hover:opacity-90"
          >
            Got it
          </button>
        </div>
      </div>
    </section>
  );
}

/** Full notice text and version, opened from the Notice link in the area header. */
export function NoticeModal({ open, notice, onClose }: { open: boolean; notice: ComplianceNotice | null; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Compliance notice" testId="notice-modal">
      {notice ? (
        <div className="space-y-3 text-sm text-fg">
          <p className="whitespace-pre-wrap leading-relaxed">{notice.text}</p>
          <p className="text-xs text-muted">Notice version {notice.version}</p>
        </div>
      ) : (
        <p className="text-sm text-muted">The compliance notice could not be loaded. Reload the page to try again.</p>
      )}
    </Modal>
  );
}
