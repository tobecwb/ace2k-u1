import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CONNECTION_CLOSED, Connection, OBJECTS, mergeStatus, socketUrl } from '../js/conn.js';

class FakeWS {
  static all = [];
  constructor(url) {
    this.url = url;
    this.sent = [];
    this.readyState = 0;
    FakeWS.all.push(this);
  }
  send(text) {
    this.sent.push(JSON.parse(text));
  }
  reply(id, result) {
    this.onmessage({ data: JSON.stringify({ jsonrpc: '2.0', id, result }) });
  }
  fail(id, message, code = 400) {
    this.onmessage({ data: JSON.stringify({ jsonrpc: '2.0', id, error: { code, message } }) });
  }
  notify(method, params) {
    this.onmessage({ data: JSON.stringify({ jsonrpc: '2.0', method, params }) });
  }
  close() {
    this.readyState = 3;
    this.onclose();
  }
}
const tick = () => new Promise((r) => setImmediate(r));

function make() {
  FakeWS.all = [];
  const log = { status: [], state: [], gcode: [], timers: [] };
  const c = new Connection({
    url: 'ws://x/websocket',
    WebSocketImpl: FakeWS,
    setTimer: (fn, ms) => log.timers.push({ fn, ms }),
    onStatus: (s) => log.status.push(s),
    onState: (s) => log.state.push(s),
    onGcode: (l) => log.gcode.push(l),
  });
  c.start();
  return { c, log, ws: () => FakeWS.all[FakeWS.all.length - 1] };
}

async function handshake(ws, state = 'ready', units = { units: ['ace2k'] }) {
  ws.readyState = 1;
  ws.onopen();
  await tick();
  assert.equal(ws.sent[0].method, 'server.connection.identify');
  ws.reply(ws.sent[0].id, { connection_id: 1 });
  await tick();
  assert.equal(ws.sent[1].method, 'printer.info');
  ws.reply(ws.sent[1].id, { state });
  await tick();
  if (state === 'ready') {
    assert.equal(ws.sent[2].method, 'printer.objects.query');
    assert.deepEqual(ws.sent[2].params.objects, { ace2k_units: null });
    ws.reply(ws.sent[2].id, { status: { ace2k_units: units } });
    await tick();
  }
}

test('handshake subscribes to every object', async () => {
  const { c, log, ws } = make();
  await handshake(ws());
  const sub = ws().sent[3];
  assert.equal(sub.method, 'printer.objects.subscribe');
  assert.deepEqual(Object.keys(sub.params.objects), ['ace2k_units', 'ace2k', ...OBJECTS.filter((o) => o !== 'ace2k')]);
  assert.equal(c.unit, 'ace2k');
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

test("Moonraker's 'Klippy Host not connected' is Klipper not connected, not a page error", async () => {
  const { c, log, ws } = make();
  ws().readyState = 1;
  ws().onopen();
  await tick();
  ws().reply(ws().sent[0].id, {});
  await tick();
  assert.equal(ws().sent[1].method, 'printer.info');
  ws().fail(ws().sent[1].id, 'Klippy Host not connected', 503);
  await tick();
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'disconnected' });
  assert.equal(log.timers.length, 0); // Moonraker says when Klipper is ready, so no retry is due.
  assert.equal(c.attempt, 0);
  ws().notify('notify_klippy_ready', []);
  await tick();
  assert.equal(ws().sent.at(-1).method, 'printer.info');
});

test('another error of printer.info is still the page error', async () => {
  const { log, ws } = make();
  ws().readyState = 1;
  ws().onopen();
  await tick();
  ws().reply(ws().sent[0].id, {});
  await tick();
  ws().fail(ws().sent[1].id, 'Internal Server Error', 500);
  await tick();
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'page_error', message: 'Internal Server Error' });
});

test('deltas merge, gcode lines forwarded, shutdown reported', async () => {
  const { log, ws } = make();
  await handshake(ws());
  ws().reply(ws().sent[3].id, { status: { ace2k: { version: '1', chamber: 20 } } });
  await tick();
  ws().notify('notify_status_update', [{ ace2k: { chamber: 21 } }, 2]);
  assert.deepEqual(log.status.at(-1), { ace2k: { version: '1', chamber: 21 } });
  ws().notify('notify_gcode_response', ['// ace2k: x']);
  assert.deepEqual(log.gcode, ['// ace2k: x']);
  ws().notify('notify_klippy_shutdown', []);
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'shutdown' });
  ws().notify('notify_klippy_disconnected', []);
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'disconnected' });
  const seen = log.state.length;
  ws().notify('notify_proc_stat_update', [{}]); // a notification that the page does not use is ignored
  assert.equal(log.state.length, seen);
  assert.deepEqual(mergeStatus({ a: { x: 1, y: 2 } }, { a: { y: 3 }, b: { z: 1 } }), {
    a: { x: 1, y: 3 },
    b: { z: 1 },
  });
});

test('gcode resolves and rejects with the message', async () => {
  const { c, ws } = make();
  await handshake(ws());
  const ok = c.gcode('ACEK_STOP');
  const call = ws().sent.at(-1);
  assert.deepEqual([call.method, call.params], ['printer.gcode.script', { script: 'ACEK_STOP' }]);
  ws().reply(call.id, 'ok');
  assert.equal(await ok, 'ok');
  const bad = c.gcode('ACEK_CLEAR LANE=1');
  ws().fail(ws().sent.at(-1).id, 'ace2k: dryer clear refused: not_cool');
  await assert.rejects(bad, /not_cool/);
});

test('a script sent once per key is not sent again while it is in flight', async () => {
  // The start of a print (ACEK_PLAN_SET + SDCARD_PRINT_FILE_WITH_PARAMETERS) must reach the U1 once per click.
  // A second send with the same key, while the first has no answer, gets the answer of the first.
  // It never sends a second request.
  const { c, ws } = make();
  await handshake(ws());
  const before = ws().sent.length;
  const first = c.gcodeOnce('print:go', 'ACEK_PLAN_SET T0=a:1\nSTART');
  const second = c.gcodeOnce('print:go', 'ACEK_PLAN_SET T0=a:1\nSTART');
  const scripts = ws()
    .sent.slice(before)
    .filter((m) => m.method === 'printer.gcode.script');
  assert.equal(scripts.length, 1);
  ws().reply(scripts[0].id, 'ok');
  assert.equal(await first, 'ok');
  assert.equal(await second, 'ok');
  c.gcodeOnce('print:go', 'START'); // Once answered, a new start is a new request.
  assert.equal(
    ws()
      .sent.slice(before)
      .filter((m) => m.method === 'printer.gcode.script').length,
    2,
  );
});

test('a refused once-per-key script can be sent again', async () => {
  const { c, ws } = make();
  await handshake(ws());
  const first = c.gcodeOnce('print:go', 'START');
  ws().fail(ws().sent.at(-1).id, 'refused');
  await assert.rejects(first, /refused/);
  c.gcodeOnce('print:go', 'START');
  assert.equal(ws().sent.at(-1).params.script, 'START');
  assert.equal(ws().sent.filter((m) => m.method === 'printer.gcode.script').length, 2);
});

test('close rejects pending calls and reconnects with backoff', async () => {
  const { c, log, ws } = make();
  await handshake(ws());
  const pending = c.gcode('ACEK_STOP');
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
  await assert.rejects(c.gcode('ACEK_STOP'), /connection closed/);
  const fresh = make();
  await assert.rejects(fresh.c.gcode('ACEK_STOP'), /connection closed/); // socket still connecting
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
  ws().reply(ws().sent[3].id, { status: {} });
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
  globalThis.setTimeout = function fake(fn, ms) {
    receivers.push(this);
    return 0;
  };
  try {
    FakeWS.all = [];
    const c = new Connection({
      url: 'ws://x/websocket',
      WebSocketImpl: FakeWS,
      onStatus() {},
      onState() {},
      onGcode() {},
    });
    c.start();
    FakeWS.all[0].close();
    assert.equal(receivers.length, 1);
    assert.notEqual(receivers[0], c);
  } finally {
    globalThis.setTimeout = real;
  }
});

test('every unit named by ace2k_units is subscribed to, none aliased', async () => {
  const { c, log, ws } = make();
  await handshake(ws(), 'ready', { units: ['ace2k a', 'ace2k b'] });
  const sub = ws().sent[3];
  assert.deepEqual(Object.keys(sub.params.objects).slice(0, 3), ['ace2k_units', 'ace2k a', 'ace2k b']);
  assert.ok(!('ace2k' in sub.params.objects));
  assert.deepEqual(c.units, ['ace2k a', 'ace2k b']);
  assert.equal(c.unit, 'ace2k a');
  ws().reply(sub.id, { status: { 'ace2k a': { chamber: 20 }, 'ace2k b': { chamber: 30 } } });
  await tick();
  assert.deepEqual(log.status.at(-1), { 'ace2k a': { chamber: 20 }, 'ace2k b': { chamber: 30 } });
});

test('without ace2k_units the one unit is ace2k', async () => {
  const { c, ws } = make();
  await handshake(ws(), 'ready', null);
  assert.deepEqual(c.units, ['ace2k']);
  assert.deepEqual(Object.keys(ws().sent[3].params.objects).slice(0, 2), ['ace2k_units', 'ace2k']);
});

test("socketUrl: wss on https, ws otherwise, on the page's own host", () => {
  assert.equal(socketUrl('https:', 'u1.local'), 'wss://u1.local/websocket');
  assert.equal(socketUrl('http:', '192.168.1.5:8080'), 'ws://192.168.1.5:8080/websocket');
});

test('a handshake failure is the page error, and it is retried with the backoff until it succeeds', async () => {
  const { c, log, ws } = make();
  ws().readyState = 1;
  ws().onopen();
  await tick();
  ws().reply(ws().sent[0].id, {});
  await tick();
  ws().fail(ws().sent[1].id, 'boom');
  await tick();
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'page_error', message: 'boom' });
  assert.deepEqual(
    log.timers.map((x) => x.ms),
    [1000],
  );
  log.timers[0].fn(); // The retry does not identify again. It asks printer.info again.
  await tick();
  assert.equal(ws().sent.filter((m) => m.method === 'server.connection.identify').length, 1);
  assert.equal(ws().sent.at(-1).method, 'printer.info');
  ws().fail(ws().sent.at(-1).id, 'again');
  await tick();
  assert.deepEqual(
    log.timers.map((x) => x.ms),
    [1000, 2000],
  );
  log.timers[1].fn();
  await tick();
  ws().reply(ws().sent.at(-1).id, { state: 'startup' });
  await tick();
  assert.deepEqual(log.state.at(-1), { online: true, klippy: 'startup' });
  assert.equal(log.timers.length, 2); // It succeeded, so there are no more retries.
  assert.equal(c.attempt, 0);
});

test('a failing identify is retried too; a retry after the socket closed does nothing', async () => {
  const { c, log, ws } = make();
  ws().readyState = 1;
  ws().onopen();
  await tick();
  ws().fail(ws().sent[0].id, 'no identify');
  await tick();
  assert.equal(log.state.at(-1).klippy, 'page_error');
  log.timers[0].fn();
  await tick();
  assert.equal(ws().sent.filter((m) => m.method === 'server.connection.identify').length, 2);
  ws().fail(ws().sent.at(-1).id, 'again'); // a second failure while a retry is already due
  await tick();
  const due = log.timers.length;
  ws().notify('notify_klippy_ready', []); // Its subscribe fails while a retry is due, and no second timer starts.
  await tick();
  ws().fail(ws().sent.at(-1).id, 'x');
  await tick();
  assert.equal(log.timers.length, due);
  ws().close();
  const states = log.state.length;
  log.timers[due - 1].fn(); // the retry of the closed socket
  await tick();
  assert.equal(log.state.length, states + 0);
  assert.equal(c.down, true);
});

// The retry timer of a closed socket must not touch the retry state of the socket after it.
test("a closed socket's late retry leaves the new socket's due retry alone: one retry, one handshake", async () => {
  const { log, ws } = make();
  const a = ws();
  a.readyState = 1;
  a.onopen();
  await tick();
  a.fail(a.sent[0].id, 'old'); // socket a: a retry is due
  await tick();
  const oldRetry = log.timers.at(-1);
  a.close(); // a reconnect is due
  log.timers.at(-1).fn(); // the new socket b opens
  const b = ws();
  assert.notEqual(b, a);
  b.readyState = 1;
  b.onopen();
  await tick();
  b.fail(b.sent[0].id, 'new'); // socket b: its own retry is due
  await tick();
  const timers = log.timers.length;
  oldRetry.fn(); // a's retry fires late
  await tick();
  b.notify('notify_klippy_ready', []); // b fails again while its retry is still due
  await tick();
  b.fail(b.sent.at(-1).id, 'again');
  await tick();
  assert.equal(log.timers.length, timers); // no second retry for b
  log.timers.at(-1).fn(); // b's one retry
  await tick();
  assert.equal(b.sent.filter((m) => m.method === 'server.connection.identify').length, 2);
});

test('a G-code call waits for its answer with no time limit of its own, as a head load of ~90 s needs', async () => {
  const { c, log, ws } = make();
  await handshake(ws());
  const timers = log.timers.length;
  let done = null;
  c.gcode('AUTO_FEEDING_BATCH ACTION=DOING EXTRUDER=0 LOAD=1').then((r) => {
    done = r;
  });
  const sent = ws().sent.at(-1);
  assert.equal(sent.method, 'printer.gcode.script');
  // Nothing arms a timer that would end the call; it ends only with its answer or a closed socket.
  await tick();
  assert.equal(log.timers.length, timers);
  assert.equal(done, null);
  ws().reply(sent.id, 'ok');
  await tick();
  assert.equal(done, 'ok');
});

test('a call that fails because the socket closed rejects with CONNECTION_CLOSED', async () => {
  const { c, ws } = make();
  await handshake(ws());
  const inFlight = c.gcode('G28');
  ws().close();
  await assert.rejects(inFlight, { message: CONNECTION_CLOSED });
  await assert.rejects(c.gcode('G28'), { message: CONNECTION_CLOSED });
  assert.equal(CONNECTION_CLOSED, 'connection closed');
  assert.ok(OBJECTS.includes('gcode_macro AUTO_FEEDING_BATCH'));
  assert.ok(OBJECTS.includes('virtual_sdcard'));
  assert.ok(!OBJECTS.some((o) => o.startsWith('filament_feed')));
});

test('rangeText asks for a byte range and returns the text of a 206', async () => {
  /** @type {RequestInit | undefined} */
  let seen;
  const fetchFn = async (_url, init) => {
    seen = init;
    return new Response('; filament: 1,2', { status: 206 });
  };
  const { c } = make();
  assert.equal(await c.rangeText('a b.gcode', 0, 8192, fetchFn), '; filament: 1,2');
  assert.equal(new Headers(seen?.headers).get('Range'), 'bytes=0-8191');
});

test('rangeText reads no more than asked when the server ignores the range, and cancels the rest', async () => {
  let pulled = 0;
  let cancelled = false;
  const body = new ReadableStream({
    pull(ctrl) {
      pulled += 1;
      ctrl.enqueue(new Uint8Array(4096).fill(65));
      if (pulled > 1000) ctrl.close();
    },
    cancel() {
      cancelled = true;
    },
  });
  const { c } = make();
  const text = await c.rangeText('big.gcode', 0, 8192, async () => new Response(body, { status: 200 }));
  assert.equal(text.length, 8192);
  assert.ok(pulled < 10);
  assert.ok(cancelled);
});

test('rangeText cancels the body when a read fails, and says when there is no body', async () => {
  let cancels = 0;
  const reader = {
    read: async () => {
      throw new Error('reset');
    },
    // A stream that failed may refuse the cancel too; the read's error is the one that counts.
    cancel: async () => {
      cancels += 1;
      throw new Error('gone');
    },
  };
  const broken = { status: 206, body: { getReader: () => reader } };
  const { c } = make();
  await assert.rejects(
    c.rangeText('x.gcode', 0, 10, async () => /** @type {Response} */ (/** @type {unknown} */ (broken))),
    /reset/,
  );
  assert.equal(cancels, 1);
  await assert.rejects(
    c.rangeText('x.gcode', 0, 10, async () => new Response(null, { status: 206 })),
    /no body/,
  );
});

test('rangeText takes a 200 to a range of the last bytes when the whole file fits in it', async () => {
  const { c } = make();
  /** @type {string[]} */
  const ranges = [];
  /** @param {BodyInit} body @param {HeadersInit} [headers] */
  const answer = (body, headers) => async (/** @type {string} */ _url, /** @type {RequestInit} */ init) => {
    ranges.push(String(new Headers(init.headers).get('Range')));
    return new Response(body, { status: 200, headers });
  };
  // Moonraker answers 200 when the range covers the whole file: the file is the asked bytes.
  assert.equal(await c.rangeText('s.gcode', -65536, 65536, answer('; tail')), '; tail');
  assert.equal(await c.rangeText('s.gcode', -10, 10, answer('0123456789', { 'Content-Length': '10' })), '0123456789');
  assert.deepEqual(ranges, ['bytes=-65536', 'bytes=-10']);
});

test('rangeText refuses a 200 to a range of the last bytes when the file is longer, and cancels it', async () => {
  const { c } = make();
  /** @param {number} chunks @param {{ cancelled: boolean }} seen */
  const stream = (chunks, seen) => {
    let n = 0;
    return new ReadableStream({
      pull(ctrl) {
        n += 1;
        ctrl.enqueue(new Uint8Array(4).fill(65));
        if (n >= chunks) ctrl.close();
      },
      cancel() {
        seen.cancelled = true;
      },
    });
  };
  // Content-Length says it is too long: refused before any byte is read.
  const said = { cancelled: false };
  const long = new Response(stream(100, said), { status: 200, headers: { 'Content-Length': '400' } });
  await assert.rejects(
    c.rangeText('l.gcode', -10, 10, async () => long),
    /range ignored/,
  );
  assert.ok(said.cancelled);
  // No Content-Length: the read goes one byte past the range, then refuses and cancels the rest.
  const read = { cancelled: false };
  await assert.rejects(
    c.rangeText('l.gcode', -10, 10, async () => new Response(stream(100, read), { status: 200 })),
    /range ignored/,
  );
  assert.ok(read.cancelled);
  // A range from inside the file is never the whole file.
  await assert.rejects(
    c.rangeText('l.gcode', 4, 10, async () => new Response('whole', { status: 200 })),
    /range ignored/,
  );
});

test('rangeText stops early when a 200 body is shorter than asked', async () => {
  const { c } = make();
  assert.equal(await c.rangeText('s.gcode', 0, 8192, async () => new Response('tiny', { status: 200 })), 'tiny');
});

test('a tail range uses a suffix range', async () => {
  let range = null;
  const { c } = make();
  await c.rangeText('x.gcode', -65536, 65536, async (_u, init) => {
    range = new Headers(init?.headers).get('Range');
    return new Response('', { status: 206 });
  });
  assert.equal(range, 'bytes=-65536');
});

test('rangeText refuses a whole-file answer that is not the asked bytes, and any other status', async () => {
  const { c } = make();
  for (const [start, body] of [
    [-10, 'a whole file longer than ten bytes'],
    [5, 'whole'],
  ])
    await assert.rejects(
      c.rangeText('x.gcode', Number(start), 10, async () => new Response(String(body), { status: 200 })),
      /range ignored/,
    );
  await assert.rejects(
    c.rangeText('x.gcode', 0, 10, async () => new Response('', { status: 404 })),
    /HTTP 404/,
  );
});

test('fileCard takes colours from the file, else from the metadata', async () => {
  const head = '; filament: 2\n';
  const tail = '; filament_colour = #111111;#222222\n; filament_type = PLA;PETG\n';
  const meta = {
    result: { estimated_time: 5760, modified: 7, thumbnails: [], filament_colors: ['#333333'], filament_type: 'PLA' },
  };
  const fetchFn = async (url, init) => {
    const r = new Headers(init?.headers).get('Range');
    if (String(url).includes('metadata')) return Response.json(meta);
    return new Response(r === 'bytes=0-8191' ? head : tail, { status: 206 });
  };
  const { c } = make();
  const card = await c.fileCard('f.gcode', fetchFn);
  assert.deepEqual(card.colours, [{ colour: '#222222', material: 'PETG', vendor: null }]);
  assert.equal(card.time, 5760);
  assert.equal(card.modified, 7);
  assert.equal(card.thumb, null);
  const noRange = async (url) =>
    String(url).includes('metadata') ? Response.json(meta) : new Response('', { status: 404 });
  const card2 = await c.fileCard('f.gcode', noRange);
  assert.deepEqual(card2.colours, [{ colour: '#333333', material: 'PLA', vendor: null }]);
});

test('fileCard reads the metadata once: the thumbnail comes from the same answer', async () => {
  const meta = {
    result: {
      modified: 9,
      thumbnails: [
        { relative_path: '.thumbs/f-32.png', width: 32 },
        { relative_path: '.thumbs/f-300.png', width: 300 },
      ],
    },
  };
  const metaReads = [];
  const fetchFn = async (url) => {
    if (String(url).includes('metadata')) {
      metaReads.push(url);
      return Response.json(meta);
    }
    return new Response('', { status: 404 });
  };
  const { c } = make();
  const card = await c.fileCard('sub/f.gcode', fetchFn);
  assert.equal(card.thumb, '/server/files/gcodes/sub/.thumbs/f-300.png?v=9');
  assert.equal(metaReads.length, 1);
});

test('fileCard without colours or metadata gives nulls', async () => {
  const { c } = make();
  const none = await c.fileCard('f.gcode', async () => new Response('', { status: 404 }));
  assert.deepEqual(none, { colours: null, time: null, thumb: null, modified: null, gcodeStart: null, gcodeEnd: null });
  const bare = { result: { filament_colors: ['#444444'] } };
  const card = await c.fileCard('f.gcode', async (url) =>
    String(url).includes('metadata') ? Response.json(bare) : new Response('', { status: 404 }),
  );
  assert.deepEqual(card.colours, [{ colour: '#444444', material: null, vendor: null }]);
  // The G-code's byte range, when the metadata has it.
  const bytes = { result: { gcode_start_byte: 300, gcode_end_byte: 9000 } };
  const ranged = await c.fileCard('f.gcode', async (url) =>
    String(url).includes('metadata') ? Response.json(bytes) : new Response('', { status: 404 }),
  );
  assert.deepEqual([ranged.gcodeStart, ranged.gcodeEnd], [300, 9000]);
});

test('fileCard reads a list of types, and shows the file when its metadata cannot be read', async () => {
  const { c } = make();
  const listed = { result: { filament_colors: ['#444444', '#555555'], filament_type: ['PLA', 'PETG'] } };
  const card = await c.fileCard('f.gcode', async (url) =>
    String(url).includes('metadata') ? Response.json(listed) : new Response('', { status: 404 }),
  );
  assert.deepEqual(
    card.colours?.map((x) => x.material),
    ['PLA', 'PETG'],
  );
  const head = '; filament: 1\n';
  const tail = '; filament_colour = #111111\n; filament_type = PLA\n';
  const badMeta = async (url, init) => {
    if (String(url).includes('metadata')) return new Response('not json', { status: 200 });
    return new Response(new Headers(init?.headers).get('Range') === 'bytes=0-8191' ? head : tail, { status: 206 });
  };
  const fromFile = await c.fileCard('f.gcode', badMeta);
  assert.deepEqual(fromFile.colours, [{ colour: '#111111', material: 'PLA', vendor: null }]);
  assert.deepEqual([fromFile.time, fromFile.modified], [null, null]);
  const down = await c.fileCard('f.gcode', async (url) => {
    if (String(url).includes('metadata')) throw new Error('offline');
    return new Response('', { status: 404 });
  });
  assert.deepEqual(down.colours, null);
});

test('the G-code files, newest first', async () => {
  const body = {
    result: [
      { path: 'old.gcode', modified: 10 },
      { path: 'notes.txt', modified: 99 },
      { path: 'sub/new.GCODE', modified: 30 },
      { path: 'mid.gcode', modified: 20 },
    ],
  };
  const urls = [];
  const fetchFn = async (url) => {
    urls.push(url);
    return { ok: true, json: async () => body };
  };
  const conn = new Connection({ url: 'ws://x', WebSocketImpl: class {}, onStatus() {}, onState() {}, onGcode() {} });
  assert.deepEqual(await conn.files(fetchFn), [
    { path: 'sub/new.GCODE', modified: 30, size: null },
    { path: 'mid.gcode', modified: 20, size: null },
    { path: 'old.gcode', modified: 10, size: null },
  ]);
  assert.deepEqual(urls, ['/server/files/list?root=gcodes']);
  await assert.rejects(conn.files(async () => ({ ok: false, status: 500 })));
});

test('files keeps each file size, null when Moonraker gives none', async () => {
  const fetchFn = async () => ({
    ok: true,
    json: async () => ({
      result: [
        { path: 'a.gcode', modified: 2, size: 1000 },
        { path: 'b.gcode', modified: 1 },
        { path: 'c.gcode', modified: 3, size: -1 },
        { path: 'd.gcode', modified: 4, size: Infinity },
      ],
    }),
  });
  const conn = new Connection({ url: 'ws://x', WebSocketImpl: class {}, onStatus() {}, onState() {}, onGcode() {} });
  assert.deepEqual(await conn.files(fetchFn), [
    { path: 'd.gcode', modified: 4, size: null },
    { path: 'c.gcode', modified: 3, size: null },
    { path: 'a.gcode', modified: 2, size: 1000 },
    { path: 'b.gcode', modified: 1, size: null },
  ]);
});

test("thumbnail: the largest of the file's thumbnails, next to the file, as a URL of Moonraker's files", async () => {
  const urls = [];
  const meta = {
    result: {
      modified: 1729771234.56,
      thumbnails: [
        { width: 32, height: 32, relative_path: '.thumbs/a b-32x32.png' },
        { width: 300, height: 300, relative_path: '.thumbs/a b-300x300.png' },
        { width: 48, height: 48 }, // no path: left out
        { relative_path: '.thumbs/odd.png' }, // no size: the smallest
      ],
    },
  };
  const fetchFn = async (url) => {
    urls.push(url);
    return { ok: true, json: async () => meta };
  };
  const conn = new Connection({ url: 'ws://x', WebSocketImpl: class {}, onStatus() {}, onState() {}, onGcode() {} });
  assert.equal(
    await conn.thumbnail('sub dir/a b.gcode', fetchFn),
    '/server/files/gcodes/sub%20dir/.thumbs/a%20b-300x300.png?v=1729771234.56', // the file's time
  );
  assert.deepEqual(urls, ['/server/files/metadata?filename=sub%20dir%2Fa%20b.gcode']);
  delete meta.result.modified; // no time: the plain URL
  assert.equal(await conn.thumbnail('a b.gcode', fetchFn), '/server/files/gcodes/.thumbs/a%20b-300x300.png');
});

test('thumbnail: none in the file, no metadata, or a failed request is no thumbnail', async () => {
  const conn = new Connection({ url: 'ws://x', WebSocketImpl: class {}, onStatus() {}, onState() {}, onGcode() {} });
  const answer =
    (body, ok = true) =>
    async () => ({ ok, status: ok ? 200 : 404, json: async () => body });
  assert.equal(await conn.thumbnail('p.gcode', answer({ result: { thumbnails: [] } })), null);
  assert.equal(await conn.thumbnail('p.gcode', answer({ result: {} })), null);
  assert.equal(await conn.thumbnail('p.gcode', answer(null)), null);
  assert.equal(await conn.thumbnail('p.gcode', answer({}, false)), null);
});

test('fileBytes reads the metadata alone: the G-code bytes and the time of change', async () => {
  const conn = new Connection({ url: 'ws://x', WebSocketImpl: class {}, onStatus() {}, onState() {}, onGcode() {} });
  const seen = [];
  const answer = (result) => async (url) => {
    seen.push(url);
    return { ok: true, json: async () => ({ result }) };
  };
  assert.deepEqual(await conn.fileBytes('a b.gcode', answer({ gcode_start_byte: 5, gcode_end_byte: 9, modified: 3 })), {
    gcodeStart: 5,
    gcodeEnd: 9,
    modified: 3,
  });
  assert.deepEqual(seen, ['/server/files/metadata?filename=a%20b.gcode']);
  assert.deepEqual(await conn.fileBytes('x', async () => ({ ok: false })), {
    gcodeStart: null,
    gcodeEnd: null,
    modified: null,
  });
});
