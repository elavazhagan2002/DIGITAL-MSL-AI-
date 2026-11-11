import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MetricsDetails, messageKind } from './MessageTypes';
import type { AgentResponse } from '../../services/digitalMslService';

const claim = { claim_id: 'c', claim_text: 't', evidence: [], validation: { score: 1, verdict: 'supported' } };
const base = { message_id: 'm', answer: 'a', claims: [claim], retrieval_ran: true, metrics: { retrieval_confidence: 0.8, groundedness: 0.9, response_time_ms: 800 } };
const as = (o: Record<string, unknown>) => ({ ...base, ...o }) as unknown as AgentResponse;

describe('message type selection', () => {
  it('uses message_type only', () => {
    expect(messageKind(as({ message_type: 'answer' }))).toBe('answer');
    expect(messageKind(as({ message_type: 'escalation', decision: 'respond' }))).toBe('escalation');
    expect(messageKind(as({ message_type: 'refusal' }))).toBe('refusal');
    expect(messageKind(as({ message_type: 'safety' }))).toBe('safety');
    expect(messageKind(as({ message_type: 'assistant_note', decision: 'respond' }))).toBe('assistant_note');
  });
  it('never renders an answer without claims or without a retrieval run', () => {
    expect(messageKind(as({ message_type: 'answer', claims: [] }))).toBe('assistant_note');
    expect(messageKind(as({ message_type: 'answer', retrieval_ran: false }))).toBe('assistant_note');
  });
});

describe('staff Details panel', () => {
  const html = (r: AgentResponse) => renderToStaticMarkup(createElement(MetricsDetails, { result: r }));
  it('renders only when metrics exist, retrieval ran and there are claims', () => {
    expect(html(as({}))).toContain('Details');
    expect(html(as({ metrics: null }))).toBe('');
    expect(html(as({ claims: [] }))).toBe('');
    expect(html(as({ retrieval_ran: false }))).toBe('');
  });
});
