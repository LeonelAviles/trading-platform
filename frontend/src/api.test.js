import { afterEach, describe, expect, it, vi } from 'vitest';
import { streamAgentMessage } from './api';

function streamingResponse(chunks) {
  const encoder = new TextEncoder();
  return {
    ok: true,
    body: new ReadableStream({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    }),
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('streamAgentMessage', () => {
  it('parses NDJSON events split across network chunks', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamingResponse([
      '{"type":"status","message":"Reasoning…"}\n{"type":"del',
      'ta","delta":"Hello "}\n{"type":"delta","delta":"world"}\n',
      '{"type":"done","message":{"content":"Hello world"}}\n',
    ])));
    const events = [];
    await streamAgentMessage('thread-1', 'Hello', (event) => events.push(event));
    expect(events.map((event) => event.type)).toEqual(['status', 'delta', 'delta', 'done']);
    expect(events.filter((event) => event.type === 'delta').map((event) => event.delta).join('')).toBe('Hello world');
  });

  it('surfaces streamed server errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamingResponse([
      '{"type":"error","message":"OpenAI unavailable"}\n',
    ])));
    await expect(streamAgentMessage('thread-1', 'Hello')).rejects.toThrow('OpenAI unavailable');
  });
});
