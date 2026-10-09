// Mock upstream recorder for T4 prompt-mutation capture.
// Logs every request (headers minus auth + raw body) to /captured/<ts>-<n>.json and returns a
// minimal valid OpenAI chat-completions response (SSE when the request asks for stream:true).
// Node stdlib only — no deps. Runs in node:22-alpine (see docker-compose upstream-recorder).
import http from 'node:http';
import { writeFileSync, mkdirSync } from 'node:fs';

const PORT = 8089;
const OUT = '/captured';
try { mkdirSync(OUT, { recursive: true }); } catch {}
let n = 0;

const server = http.createServer((req, res) => {
  const chunks = [];
  req.on('data', (c) => chunks.push(c));
  req.on('end', () => {
    const raw = Buffer.concat(chunks).toString('utf8');
    n += 1;
    const stamp = new Date().toISOString().replace(/[:.]/g, '-');
    const headers = { ...req.headers };
    // Never store credentials; keep only which kind arrived (OAuth vs API key vs gateway key).
    const credKind = (v) => (!v ? null : /sk-ant-oat/.test(v) ? 'claude-oauth' : /sk-ant-api/.test(v) ? 'anthropic-api-key' : 'other');
    const auth = { authorization: credKind(headers['authorization']), 'x-api-key': credKind(headers['x-api-key']),
      'x-litellm-api-key': credKind(headers['x-litellm-api-key']), 'x-pii-guard-key': credKind(headers['x-pii-guard-key']) };
    delete headers['authorization'];
    delete headers['x-api-key'];
    delete headers['x-litellm-api-key'];
    delete headers['x-pii-guard-key'];
    let parsed = null;
    try { parsed = JSON.parse(raw); } catch {}
    const record = { seq: n, time: stamp, method: req.method, url: req.url, auth, headers, rawBody: raw, jsonBody: parsed };
    try { writeFileSync(`${OUT}/${stamp}-${String(n).padStart(3, '0')}.json`, JSON.stringify(record, null, 2)); } catch (e) { console.error('write failed', e); }

    const wantsStream = !!(parsed && parsed.stream);
    if (req.url.startsWith('/v1/messages')) {
      // Anthropic Messages format (Claude Code subscription profile).
      const message = { id: 'msg_mock', type: 'message', role: 'assistant', model: (parsed && parsed.model) || 'mock',
        content: [], stop_reason: null, stop_sequence: null, usage: { input_tokens: 1, output_tokens: 0 } };
      // MOCK_ECHO in the prompt: echo the first placeholder split across deltas, in text AND in a
      // tool_use input, to test stream rehydration (scripts/test_claude_subscription.sh).
      const echo = /MOCK_ECHO/.test(raw) && (raw.match(/<[A-Z_]+_\d+>/) || [])[0];
      if (wantsStream) {
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
        const ev = (type, data) => res.write(`event: ${type}\ndata: ${JSON.stringify({ type, ...data })}\n\n`);
        ev('message_start', { message });
        ev('content_block_start', { index: 0, content_block: { type: 'text', text: '' } });
        if (echo) {
          const cut = Math.floor(echo.length / 2);
          ev('content_block_delta', { index: 0, delta: { type: 'text_delta', text: `ECHO ${echo.slice(0, cut)}` } });
          ev('content_block_delta', { index: 0, delta: { type: 'text_delta', text: `${echo.slice(cut)} END` } });
          ev('content_block_stop', { index: 0 });
          ev('content_block_start', { index: 1, content_block: { type: 'tool_use', id: 'toolu_mock', name: 'Bash', input: {} } });
          const json = JSON.stringify({ command: `echo ${echo}` });
          const jcut = json.indexOf(echo) + cut;
          ev('content_block_delta', { index: 1, delta: { type: 'input_json_delta', partial_json: json.slice(0, jcut) } });
          ev('content_block_delta', { index: 1, delta: { type: 'input_json_delta', partial_json: json.slice(jcut) } });
          ev('content_block_stop', { index: 1 });
        } else {
          ev('content_block_delta', { index: 0, delta: { type: 'text_delta', text: 'MOCK_OK' } });
          ev('content_block_stop', { index: 0 });
        }
        ev('message_delta', { delta: { stop_reason: 'end_turn', stop_sequence: null }, usage: { output_tokens: 2 } });
        ev('message_stop', {});
        res.end();
      } else {
        res.writeHead(200, { 'content-type': 'application/json' });
        res.end(JSON.stringify({ ...message, content: [{ type: 'text', text: 'MOCK_OK' }], stop_reason: 'end_turn',
          usage: { input_tokens: 1, output_tokens: 2 } }));
      }
    } else if (wantsStream) {
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
      const id = 'chatcmpl-mock';
      const created = 0; // deterministic (no wall clock in body)
      res.write(`data: ${JSON.stringify({ id, object: 'chat.completion.chunk', created, model: 'mock', choices: [{ index: 0, delta: { role: 'assistant', content: 'MOCK_OK' }, finish_reason: null }] })}\n\n`);
      res.write(`data: ${JSON.stringify({ id, object: 'chat.completion.chunk', created, model: 'mock', choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] })}\n\n`);
      res.write('data: [DONE]\n\n');
      res.end();
    } else {
      const payload = {
        id: 'chatcmpl-mock', object: 'chat.completion', created: 0, model: 'mock',
        choices: [{ index: 0, message: { role: 'assistant', content: 'MOCK_OK' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 0, completion_tokens: 2, total_tokens: 2 },
      };
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(payload));
    }
    console.log(`recorded #${n} ${req.method} ${req.url} (${raw.length} bytes, stream=${wantsStream})`);
  });
});

server.listen(PORT, () => console.log(`mock-upstream recorder on :${PORT}, writing to ${OUT}`));
