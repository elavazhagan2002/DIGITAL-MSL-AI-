import { useEffect, useRef, useState, type CSSProperties } from 'react';
import {
  ArrowLeft,
  CheckCheck,
  Mic,
  MoreVertical,
  Paperclip,
  Phone,
  Send,
  Smile,
  User,
  Video,
} from 'lucide-react';
import {
  askWhatsAppDemo,
  fetchWhatsAppDemoConfig,
  newConversationId,
  type WhatsAppDemoConfig,
} from '../services/digitalMslService';
import { useToast } from '../contexts/ToastContext';

type ChatLine = {
  id: string;
  role: 'user' | 'bot';
  text: string;
  time: string;
  at: number;
  attachments?: string[];
};

type ChatRow =
  | { kind: 'date'; key: string; label: string }
  | { kind: 'meta'; key: string }
  | { kind: 'msg'; line: ChatLine };

function sameCalendarDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function whatsAppDayLabel(at: number): string {
  const d = new Date(at);
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameCalendarDay(d, now)) return 'Today';
  if (sameCalendarDay(d, yesterday)) return 'Yesterday';
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'numeric', year: 'numeric' });
}

function formatWhatsAppTime(at: number) {
  return new Date(at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }).toLowerCase();
}

function buildChatRows(lines: ChatLine[]): ChatRow[] {
  const rows: ChatRow[] = [];
  let lastDay = '';
  let metaShown = false;
  for (const line of lines) {
    const dayKey = new Date(line.at).toDateString();
    if (dayKey !== lastDay) {
      rows.push({ kind: 'date', key: `d-${dayKey}`, label: whatsAppDayLabel(line.at) });
      if (!metaShown) {
        rows.push({ kind: 'meta', key: 'meta-secure' });
        metaShown = true;
      }
      lastDay = dayKey;
    }
    rows.push({ kind: 'msg', line });
  }
  return rows;
}

function WaDateChip({ label }: { label: string }) {
  return (
    <div className="flex justify-center py-2">
      <span className="inline-block px-2.5 py-1 rounded-md bg-card text-[12px] leading-none text-muted shadow-sm">
        {label}
      </span>
    </div>
  );
}

function MetaSecureBanner() {
  return (
    <div className="flex justify-center px-4 py-1">
      <p className="text-center text-[12px] leading-[1.35] text-muted max-w-[92%]">
        <span className="inline-flex items-start gap-1.5 rounded-lg bg-selbg px-3 py-2 shadow-sm text-left">
          <svg className="w-4 h-4 shrink-0 mt-0.5 text-muted" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
            <path d="M8 1a3 3 0 0 0-3 3v1H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2H9V4a3 3 0 0 0-3-3zm0 1.5A1.5 1.5 0 0 1 9.5 4v1h-3V4A1.5 1.5 0 0 1 8 2.5z" />
          </svg>
          <span>
            This business uses a secure service from Meta to manage this chat.{' '}
            <button type="button" className="text-link">
              Tap to learn more.
            </button>
          </span>
        </span>
      </p>
    </div>
  );
}

/** WhatsApp Android composer camera (Material-style). */
function WaCameraIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" width="24" height="24" aria-hidden>
      <path
        fill="currentColor"
        d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4z"
      />
      <path
        fill="currentColor"
        d="M9 2 7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5z"
      />
    </svg>
  );
}

const STAGE_LABELS: Record<string, string> = {
  received: 'Reading your message',
  scope: 'Checking scope',
  retrieving: 'Searching therapeutic area',
  evidence: 'Collecting evidence',
  drafting: 'Drafting answer',
  validating: 'Validating',
  routing: 'Preparing reply',
};

function formatTime(d = new Date()) {
  return formatWhatsAppTime(d.getTime());
}

const tileBase = import.meta.env.BASE_URL;
/** WhatsApp mobile-style doodle wallpaper (PNG if added to public/, else SVG). */
const WA_CHAT_PANEL_STYLE: CSSProperties = {
  backgroundColor: 'var(--soft)',
  backgroundImage: `url(${tileBase}whatsapp-chat-tile.png), url(${tileBase}whatsapp-chat-tile.svg)`,
  backgroundRepeat: 'repeat',
  backgroundSize: '412.5px 412.5px',
};

interface WhatsAppDemoChatProps {
  userId: string;
}

export function WhatsAppDemoChat({ userId }: WhatsAppDemoChatProps) {
  const toast = useToast();
  const [config, setConfig] = useState<WhatsAppDemoConfig | null>(null);
  const [lines, setLines] = useState<ChatLine[]>([]);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [stageLabel, setStageLabel] = useState('');
  const [conversationId, setConversationId] = useState(() => newConversationId());
  const fromPhone = '+919701038776';
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    fetchWhatsAppDemoConfig()
      .then(setConfig)
      .catch(() =>
        setConfig({
          mock_mode: true,
          global_mock: false,
          business_display: '+1 555-658-2876',
          note: '',
        }),
      );
  }, []);

  useEffect(() => {
    const key = `wa_demo_conv_${userId}`;
    const saved = localStorage.getItem(key);
    if (saved) setConversationId(saved);
  }, [userId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines, sending, stageLabel]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setDraft('');
    setLines((prev) => [...prev, { id: `u_${Date.now()}`, role: 'user', text, time: formatTime(), at: Date.now() }]);
    setSending(true);
    setStageLabel(STAGE_LABELS.received);
    const history = lines.map((line) => ({
      role: line.role === 'user' ? 'user' : 'assistant',
      content: line.text,
    }));
    try {
      for await (const event of askWhatsAppDemo(text, {
        from: fromPhone,
        conversationId,
        history,
      })) {
        if (event.type === 'status' && event.stage) {
          setStageLabel(STAGE_LABELS[event.stage] || event.stage);
        } else if (event.type === 'result') {
          const reply = (event.reply_text || event.answer || '').trim() || '(No reply)';
          if (event.conversation_id) {
            setConversationId(event.conversation_id);
            localStorage.setItem(`wa_demo_conv_${userId}`, event.conversation_id);
          }
          setLines((prev) => [
            ...prev,
            {
              id: `b_${Date.now()}`,
              role: 'bot',
              text: reply,
              time: formatTime(),
              at: Date.now(),
              attachments: event.delivery?.attachments,
            },
          ]);
          if (event.decision === 'escalate_to_msl') {
            toast.success(`Escalated to Physical MSL (case ${event.msl_case_id || '—'}).`);
          }
        } else if (event.type === 'error') {
          const msg = event.detail || 'Send failed';
          setLines((prev) => [
            ...prev,
            { id: `e_${Date.now()}`, role: 'bot', text: msg, time: formatTime(), at: Date.now() },
          ]);
          toast.error(msg);
        }
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Send failed';
      setLines((prev) => [...prev, { id: `e_${Date.now()}`, role: 'bot', text: msg, time: formatTime(), at: Date.now() }]);
      toast.error(msg);
    } finally {
      setSending(false);
      setStageLabel('');
      inputRef.current?.focus();
    }
  };

  const business = config?.business_display || '+1 555-658-2876';
  const displayNumber = business.replace(/\s/g, '').replace(/-/g, '-');
  const chatRows = buildChatRows(lines);
  const hasDraft = draft.trim().length > 0;

  return (
    <div className="flex flex-col h-full min-h-0 bg-soft">
      {/* Android WhatsApp business chat header */}
      <div className="shrink-0 bg-soft text-fg px-1 py-1.5 flex items-center gap-0.5 shadow-sm border-b border-line">
        <button type="button" className="p-2.5 text-muted" aria-label="Back">
          <ArrowLeft className="w-6 h-6" strokeWidth={2} />
        </button>
        <div className="w-10 h-10 rounded-full bg-line flex items-center justify-center shrink-0 overflow-hidden">
          <span className="w-9 h-9 rounded-full bg-brandcyan flex items-center justify-center">
            <User className="w-5 h-5 text-onnavy" strokeWidth={2} />
          </span>
        </div>
        <div className="flex-1 min-w-0 pl-1">
          <p className="text-[16px] font-normal truncate leading-tight text-fg">{displayNumber}</p>
          <p className="text-[12px] text-muted truncate">
            {sending ? stageLabel || 'typing…' : 'Business account'}
          </p>
        </div>
        <button type="button" className="p-2.5 text-muted" aria-label="Video call">
          <Video className="w-[22px] h-[22px]" strokeWidth={1.75} />
        </button>
        <button type="button" className="p-2.5 text-muted" aria-label="Voice call">
          <Phone className="w-[22px] h-[22px]" strokeWidth={1.75} />
        </button>
        <button type="button" className="p-2.5 text-muted" aria-label="Menu">
          <MoreVertical className="w-[22px] h-[22px]" strokeWidth={1.75} />
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-2 sm:px-3 py-1" style={WA_CHAT_PANEL_STYLE}>
        {lines.length === 0 && !sending && (
          <p className="text-center text-xs text-muted py-12">Send a message to start.</p>
        )}
        {chatRows.map((row) => {
          if (row.kind === 'date') {
            return <WaDateChip key={row.key} label={row.label} />;
          }
          if (row.kind === 'meta') {
            return <MetaSecureBanner key={row.key} />;
          }
          const line = row.line;
          return (
            <div key={line.id} className={`flex ${line.role === 'user' ? 'justify-end' : 'justify-start'} py-0.5`}>
              <div
                className={`relative max-w-[88%] sm:max-w-[75%] rounded-lg px-2 py-1 text-[14.2px] leading-[19px] shadow-sm whitespace-pre-wrap text-fg ${
                  line.role === 'user' ? 'bg-selbg rounded-tr-none' : 'bg-card rounded-tl-none'
                }`}
              >
                {line.attachments?.map((name) => (
                  <div key={name} className="mb-1 flex items-center gap-2 rounded-md bg-soft px-2 py-2">
                    <Paperclip className="w-4 h-4 shrink-0 text-muted" />
                    <span className="truncate text-[13px] text-fg">{name}</span>
                  </div>
                ))}
                {line.text}
                <span className="inline-flex items-center gap-0.5 float-right text-[11px] text-muted ml-2 mt-1 translate-y-0.5 whitespace-nowrap min-w-[52px] justify-end">
                  {line.time}
                  {line.role === 'user' && <CheckCheck className="w-[15px] h-[15px] text-link" strokeWidth={2.25} />}
                </span>
              </div>
            </div>
          );
        })}
        {sending && (
          <div className="flex justify-start py-0.5">
            <div className="bg-card rounded-lg rounded-tl-none px-3 py-2 shadow-sm flex gap-1 items-center">
              <span className="w-1.5 h-1.5 rounded-full bg-soft animate-bounce [animation-delay:0ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-soft animate-bounce [animation-delay:150ms]" />
              <span className="w-1.5 h-1.5 rounded-full bg-soft animate-bounce [animation-delay:300ms]" />
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* WhatsApp Android composer */}
      <div className="shrink-0 bg-soft px-2 py-1.5 flex items-end gap-2">
        <div className="flex-1 flex items-end gap-1 min-h-[48px] bg-card rounded-[24px] pl-2 pr-1 py-1 shadow-sm">
          <button type="button" className="p-2 text-muted shrink-0" aria-label="Emoji">
            <Smile className="w-[26px] h-[26px]" strokeWidth={1.5} />
          </button>
          <textarea
            ref={inputRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={1}
            className="flex-1 bg-transparent text-[16px] text-fg py-2 resize-none border-0 outline-none placeholder:text-muted max-h-28 min-h-[24px] leading-snug"
            placeholder="Message"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (hasDraft) void send();
              }
            }}
          />
          <button type="button" className="p-2 text-muted shrink-0" aria-label="Attach file">
            <Paperclip className="w-[22px] h-[22px]" strokeWidth={1.75} />
          </button>
          <button type="button" className="p-2 text-muted shrink-0 mr-0.5" aria-label="Camera">
            <WaCameraIcon className="w-6 h-6" />
          </button>
        </div>
        <button
          type="button"
          disabled={sending || (hasDraft && !draft.trim())}
          onClick={() => {
            if (hasDraft) void send();
          }}
          className="shrink-0 w-[48px] h-[48px] rounded-full bg-accent text-accentfg flex items-center justify-center shadow-md disabled:opacity-60"
          aria-label={hasDraft ? 'Send' : 'Record voice message'}
        >
          {hasDraft ? <Send className="w-5 h-5 ml-0.5" /> : <Mic className="w-6 h-6" />}
        </button>
      </div>
    </div>
  );
}
