import { API_BASE_URL, API_ENDPOINTS } from '../config/api';

export interface ChatMessage {
  id?: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
  metadata?: any;
}

export interface ChatSession {
  id: number;
  session_id: string;
  title: string;
  repository_id?: string;
  created_at: string;
}

export async function sendChatMessage(
  message: string,
  repositoryId: string,
  sessionId?: string,
  conversationHistory: { role: string; content: string }[] = [],
  signal?: AbortSignal
): Promise<Response> {
  const formData = new FormData();
  formData.append('message', message);
  formData.append('repository_id', repositoryId);
  formData.append('conversation_history', JSON.stringify(conversationHistory));
  if (sessionId) {
    formData.append('session_id', sessionId);
  }

  const response = await fetch(`${API_BASE_URL}${API_ENDPOINTS.CHAT}`, {
    method: 'POST',
    body: formData,
    signal,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Chat error (${response.status}): ${errorText}`);
  }

  return response;
}

export async function listChatSessions(repositoryId?: string): Promise<ChatSession[]> {
  const url = repositoryId
    ? `${API_BASE_URL}${API_ENDPOINTS.CHAT_SESSIONS}?repository_id=${encodeURIComponent(repositoryId)}`
    : `${API_BASE_URL}${API_ENDPOINTS.CHAT_SESSIONS}`;
  const res = await fetch(url);
  if (!res.ok) return [];
  const data = await res.json();
  return data.sessions || [];
}

export async function getSessionMessages(sessionId: string): Promise<ChatMessage[]> {
  const res = await fetch(`${API_BASE_URL}${API_ENDPOINTS.CHAT_SESSION_MESSAGES(sessionId)}`);
  if (!res.ok) return [];
  const data = await res.json();
  return data.messages || [];
}

export async function* readSSEStream(
  response: Response
): AsyncGenerator<{ type: string; [key: string]: any }, void, unknown> {
  const reader = response.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.startsWith('data: ')) {
        const jsonStr = trimmed.slice(6);
        try {
          yield JSON.parse(jsonStr);
        } catch {
          // ignore incomplete json chunk
        }
      }
    }
  }
}
