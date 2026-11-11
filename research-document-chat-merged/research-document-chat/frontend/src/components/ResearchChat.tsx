import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeRaw from 'rehype-raw';
import { Send, BookOpen, AlertCircle, RefreshCw, FileText, ExternalLink, X } from 'lucide-react';
import { sendChatMessage, readSSEStream, getSessionMessages, listChatSessions, type ChatMessage } from '../services/chatService';

interface ResearchChatProps {
  repositoryId: string;
  repositoryName?: string;
}

export const ResearchChat: React.FC<ResearchChatProps> = ({ repositoryId, repositoryName }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [thinkingStatus, setThinkingStatus] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [citationDrawer, setCitationDrawer] = useState<{ label: string; excerpt?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, thinkingStatus]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    const userQuestion = input.trim();
    setInput('');
    setError(null);

    const newMessages: ChatMessage[] = [
      ...messages,
      { role: 'user', content: userQuestion }
    ];
    setMessages(newMessages);
    setIsLoading(true);
    setThinkingStatus('Initiating repository hybrid search...');

    try {
      const history = messages.map(m => ({ role: m.role, content: m.content }));
      const response = await sendChatMessage(
        userQuestion,
        repositoryId,
        sessionId || undefined,
        history
      );

      const newSessionId = response.headers.get('X-Session-ID');
      if (newSessionId && !sessionId) {
        setSessionId(newSessionId);
      }

      let assistantText = '';
      setMessages([...newMessages, { role: 'assistant', content: '' }]);

      for await (const event of readSSEStream(response)) {
        if (event.type === 'thinking') {
          setThinkingStatus(event.status || 'Thinking...');
        } else if (event.type === 'content') {
          assistantText += event.text || '';
          setMessages([
            ...newMessages,
            { role: 'assistant', content: assistantText }
          ]);
        } else if (event.type === 'done') {
          setThinkingStatus(null);
        } else if (event.type === 'error') {
          setError(event.detail || 'An error occurred during response generation.');
        }
      }
    } catch (err: any) {
      setError(err.message || 'Failed to send message.');
    } finally {
      setIsLoading(false);
      setThinkingStatus(null);
    }
  };

  const handleCitationClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = (e.target as HTMLElement).closest('.citation-pill');
    if (target) {
      const text = target.textContent || '';
      const parts = text.split('—');
      const label = parts[0]?.trim() || text;
      const excerpt = parts[1]?.trim().replace(/^"/, '').replace(/"$/, '');
      setCitationDrawer({ label, excerpt });
    }
  };

  return (
    <div className="flex flex-col h-[750px] bg-navy border border-line rounded-xl overflow-hidden shadow-2xl relative">
      {/* Header */}
      <div className="px-6 py-4 bg-scrim border-b border-line flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-accent text-brandcyan rounded-lg border border-selb">
            <BookOpen className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-onnavy">Repository Evidence Chat</h2>
            <p className="text-xs text-muted">
              Grounded in repository: <span className="text-brandcyan font-medium">{repositoryName || repositoryId}</span>
            </p>
          </div>
        </div>
        <button
          onClick={() => { setMessages([]); setSessionId(null); }}
          className="text-xs px-3 py-1.5 bg-navy hover:bg-navy text-onnavymuted rounded-md transition flex items-center gap-1.5"
          title="Start a fresh conversation"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          New Chat
        </button>
      </div>

      {/* Messages */}
      <div
        className="flex-1 p-6 overflow-y-auto space-y-4"
        onClick={handleCitationClick}
      >
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center text-muted">
            <FileText className="w-12 h-12 mb-3 stroke-1" />
            <p className="text-sm font-medium text-muted">No messages in this repository session yet.</p>
            <p className="text-xs mt-1 max-w-sm">
              Ask questions regarding clinical trial endpoints, PubMed findings, or regulatory documents indexed in this repository.
            </p>
          </div>
        ) : (
          messages.map((m, idx) => (
            <div
              key={idx}
              className={`flex flex-col ${m.role === 'user' ? 'items-end' : 'items-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-5 py-3.5 text-sm leading-relaxed ${
                  m.role === 'user'
                    ? 'bg-accent text-accentfg rounded-br-none'
                    : 'bg-scrim text-onnavymuted border border-line rounded-bl-none shadow-md'
                }`}
              >
                {m.role === 'user' ? (
                  m.content
                ) : (
                  <div className="prose prose-invert prose-sm max-w-none">
                    <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeRaw]}>
                      {m.content}
                    </ReactMarkdown>
                  </div>
                )}
              </div>
            </div>
          ))
        )}

        {/* Thinking status */}
        {thinkingStatus && (
          <div className="flex items-center gap-2 text-xs text-brandcyan bg-accent border border-selb px-3 py-2 rounded-lg w-fit animate-pulse">
            <div className="w-2 h-2 rounded-full bg-selbg animate-ping" />
            <span>{thinkingStatus}</span>
          </div>
        )}

        {error && (
          <div className="p-3 bg-err-fg border border-err-line text-err-fg text-xs rounded-lg flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="p-4 bg-navy border-t border-line flex items-center gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={`Ask about documents in ${repositoryName || 'repository'}...`}
          disabled={isLoading}
          className="flex-1 bg-navy border border-line rounded-lg px-4 py-2.5 text-sm text-onnavy placeholder-muted focus:outline-none focus:ring-2 focus:ring-selb"
        />
        <button
          type="submit"
          disabled={isLoading || !input.trim()}
          className="p-2.5 bg-accent hover:bg-accent disabled:bg-navy disabled:text-muted text-accentfg rounded-lg transition"
        >
          <Send className="w-4 h-4" />
        </button>
      </form>

      {/* Citation Evidence Preview Drawer */}
      {citationDrawer && (
        <div className="absolute top-0 right-0 w-80 h-full bg-navy border-l border-line p-5 shadow-2xl flex flex-col z-20 transition-all">
          <div className="flex items-center justify-between pb-3 border-b border-line">
            <div className="flex items-center gap-2 text-brandcyan text-sm font-semibold">
              <FileText className="w-4 h-4" />
              <span>Evidence Source</span>
            </div>
            <button
              onClick={() => setCitationDrawer(null)}
              className="p-1 hover:bg-navy text-muted rounded transition"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="mt-4 flex-1 overflow-y-auto space-y-4 text-xs">
            <div>
              <span className="text-muted font-mono">Reference Pill:</span>
              <p className="mt-1 font-semibold text-onnavymuted bg-navy p-2 rounded border border-line">
                {citationDrawer.label}
              </p>
            </div>
            {citationDrawer.excerpt && (
              <div>
                <span className="text-muted font-mono">Supporting Excerpt:</span>
                <blockquote className="mt-1 p-3 bg-navy text-onnavymuted italic border-l-2 border-selb rounded">
                  "{citationDrawer.excerpt}"
                </blockquote>
              </div>
            )}
            <div className="pt-4 border-t border-line text-muted">
              <p>Verified against repository indexed chunks via Weaviate hybrid search.</p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
