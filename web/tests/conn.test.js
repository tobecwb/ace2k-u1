import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Connection, OBJECTS, mergeStatus } from '../js/conn.js';

class FakeWS {
  static all = [];
  constructor(url) { this.url = url; this.sent = []; this.readyState = 0; FakeWS.all.push(this); }
  send(text) { this.sent.push(JSON.parse(text)); }
  reply(id, result) { this.onmessage({ data: JSON.stringify({ jsonrpc: '2.0', id, result }) }); }
  fail(id, message) { this.onmessage({ data: JSON.stringify({ jsonrpc: '2.0', id, error: { code: 400, message } }) }); }
  notify(method, params) { this.onmessage({ data: JSON.stringify({ jsonrpc: '2.0', method, params }) }); }
  close() { this.readyState = 3; this.onclose(); }
}
const tick = () => new Promise((r) => setImmediate(r));

function make() {
  FakeWS.all = [];
  const log = { status: [], state: [], gcode: [], timers: [] };
  const c = new Connection({
    url: 'ws://x/websocket', WebSocketImpl: FakeWS,
    setTimer: (fn, ms) => log.timers.push({ fn, ms }),
    onStatus: (s) => log.status.push(s), onState: (s) => log.state.push(s), onGcode: (l) => log.gcode.push(l),
  });
  c.start();
  return { c, log, ws: () => FakeWS.all[FakeWS.all.length - 1] };
}

async function handshake(ws, state = 'ready') {
  ws.readyState = 1;
  ws.onopen();
  await tick();
  assert.equal(ws.sent[0].method, 'server.connection.identify');
  ws.reply(ws.sent[0].id, { connection_id: 1 });
  await tick();
  assert.equal(ws.sent[1].method, 'printer.info');
  ws.reply(ws.sent[1].id, { state });
  await tick();
}

test('handshake subscribes to every object', async () => {
  const { log, ws } = make();
  await handshake(ws());
  const sub = ws().sent[2];
  assert.equal(sub.method, 'printer.objects.subscribe');
  assert.deepEqual(Object.keys(sub.params.objects), OBJECTS);
  ws().reply(sub.id, { status: { ace2k: { version: '0.11.0' } }, eventtime: 1 });
  await tick();
  assert.deepEqual(log.status.at(-1), { ace2k: { version: '0.11.0' } });
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'ready' });
});

test('klippy not ready waits for notify_klippy_ready', async () => {
  const { log, ws } = make();
  await handshake(ws(), 'startup');
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'startup' });
  assert.equal(ws().sent.length, 2);
  ws().notify('notify_klippy_ready', []);
  await tick();
  assert.equal(ws().sent[2].method, 'printer.info');
});

test('deltas merge, gcode lines forwarded, shutdown reported', async () => {
  const { log, ws } = make();
  await handshake(ws());
  ws().reply(ws().sent[2].id, { status: { ace2k: { version: '1', chamber: 20 } } });
  await tick();
  ws().notify('notify_status_update', [{ ace2k: { chamber: 21 } }, 2]);
  assert.deepEqual(log.status.at(-1), { ace2k: { version: '1', chamber: 21 } });
  ws().notify('notify_gcode_response', ['// ace2k: x']);
  assert.deepEqual(log.gcode, ['// ace2k: x']);
  ws().notify('notify_klippy_shutdown', []);
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'shutdown' });
  assert.deepEqual(mergeStatus({ a: { x: 1, y: 2 } }, { a: { y: 3 }, b: { z: 1 } }), { a: { x: 1, y: 3 }, b: { z: 1 } });
});

test('gcode resolves and rejects with the message', async () => {
  const { c, ws } = make();
  await handshake(ws());
  const ok = c.gcode('ACE_STOP');
  const call = ws().sent.at(-1);
  assert.deepEqual([call.method, call.params], ['printer.gcode.script', { script: 'ACE_STOP' }]);
  ws().reply(call.id, 'ok');
  assert.equal(await ok, 'ok');
  const bad = c.gcode('ACE_CLEAR LANE=1');
  ws().fail(ws().sent.at(-1).id, 'ace2k: dryer clear refused: not_cool');
  await assert.rejects(bad, /not_cool/);
});

test('close rejects pending calls and reconnects with backoff', async () => {
  const { c, log, ws } = make();
  await handshake(ws());
  const pending = c.gcode('ACE_STOP');
  ws().close();
  await assert.rejects(pending, /closed/);
  assert.deepEqual(log.state.at(-1), { online: false });
  const delays = [];
  for (let i = 0; i < 5; i++) {
    const timer = log.timers.at(-1);
    delays.push(timer.ms);
    timer.fn();
    ws().close();
  }
  assert.deepEqual(delays, [1000, 2000, 5000, 10000, 10000]);
});

test('a call while down rejects at once', async () => {
  const { c, ws } = make();
  await handshake(ws());
  ws().close();
  await assert.rejects(c.gcode('ACE_STOP'), /connection closed/);
  const fresh = make();
  await assert.rejects(fresh.c.gcode('ACE_STOP'), /connection closed/); // socket still connecting
});

test('backoff is kept when the server opens then closes', async () => {
  const { log, ws } = make();
  const delays = [];
  for (let i = 0; i < 5; i++) {
    ws().readyState = 1;
    ws().onopen();
    ws().close();
    delays.push(log.timers.at(-1).ms);
    log.timers.at(-1).fn();
  }
  assert.deepEqual(delays, [1000, 2000, 5000, 10000, 10000]);
});

test('backoff resets after a completed handshake', async () => {
  const { log, ws } = make();
  ws().close();
  log.timers.at(-1).fn();
  await handshake(ws());
  ws().reply(ws().sent[2].id, { status: {} });
  await tick();
  ws().close();
  assert.equal(log.timers.at(-1).ms, 1000);
});

test('a malformed frame is ignored', async () => {
  const { log, ws } = make();
  await handshake(ws());
  assert.doesNotThrow(() => ws().onmessage({ data: 'not json' }));
  ws().notify('notify_gcode_response', ['ok']);
  assert.deepEqual(log.gcode, ['ok']);
});

test('an error without a message still rejects with text', async () => {
  const { c, ws } = make();
  await handshake(ws());
  const p = c.gcode('X');
  ws().onmessage({ data: JSON.stringify({ jsonrpc: '2.0', id: ws().sent.at(-1).id, error: { code: 500 } }) });
  await assert.rejects(p, /500/);
});

test('the default timer calls setTimeout unbound (a browser throws Illegal invocation otherwise)', () => {
  const real = globalThis.setTimeout;
  const receivers = [];
  globalThis.setTimeout = function fake(fn, ms) { receivers.push(this); return 0; };
  try {
    FakeWS.all = [];
    const c = new Connection({ url: 'ws://x/websocket', WebSocketImpl: FakeWS, onStatus() {}, onState() {}, onGcode() {} });
    c.start();
    FakeWS.all[0].close();
    assert.equal(receivers.length, 1);
    assert.notEqual(receivers[0], c);
  } finally {
    globalThis.setTimeout = real;
  }
});
