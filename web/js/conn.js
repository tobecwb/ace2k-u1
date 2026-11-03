import { fileColours, HEAD_BYTES, TAIL_BYTES } from './slicer-file.js';

/** @typedef {import('./types.js').BannerView} BannerView */

// The link to Moonraker over its websocket JSON-RPC. It identifies the page, subscribes to the status,
// merges the changes, makes calls and reconnects.
export const OBJECTS = [
  'ace2k',
  'ace2k_u1',
  'ace2k_u1_history',
  'print_task_config',
  'print_stats',
  // The byte position of a running print in its file (the printing view reads it).
  'virtual_sdcard',
  'filament_motion_sensor e0_filament',
  'filament_motion_sensor e1_filament',
  'filament_motion_sensor e2_filament',
  'filament_motion_sensor e3_filament',
  // Whether the U1's head load macro has a batch open. Load into head N waits while it does.
  'gcode_macro AUTO_FEEDING_BATCH',
];
// The error of a call that the closed socket ended, or that was made while it was closed.
// Its request may still run in Klipper.
export const CONNECTION_CLOSED = 'connection closed';
const BACKOFF_S = [1, 2, 5, 10];

/**
 * @param {import('./types.js').Status} status
 * @param {Record<string, object> | null | undefined} delta  The changes that Moonraker sends, by object.
 * @returns {import('./types.js').Status}
 */
export function mergeStatus(status, delta) {
  // The status is a record of objects by name, and each merge below keeps that shape.
  const out = /** @type {Record<string, object>} */ (/** @type {unknown} */ ({ ...status }));
  for (const [name, fields] of Object.entries(delta || {})) out[name] = { ...(out[name] || {}), ...fields };
  return /** @type {import('./types.js').Status} */ (out);
}

// The printer's websocket address, for the page's protocol and host.
/**
 * @param {string} protocol  location.protocol
 * @param {string} host  location.host
 */
export const socketUrl = (protocol, host) => `${protocol === 'https:' ? 'wss' : 'ws'}://${host}/websocket`;

/** @typedef {{ online: boolean, klippy?: string | null, message?: string }} LinkState */
// This is the only `any` of the page. The shape of what Moonraker sends depends on the method.
// So it is read field by field where it is used, and not typed here.
/** @typedef {any} Rpc */
/** @typedef {{ resolve: (value: Rpc) => void, reject: (reason: Error) => void }} PendingCall */

// The first bytes of a body, at least limit of them unless it ends first, and the rest cancelled.
// It is cancelled also when a read fails, so the rest of the body is never downloaded.
/** @param {ReadableStream<Uint8Array>} body @param {number} limit @returns {Promise<Uint8Array>} */
async function readUpTo(body, limit) {
  const reader = body.getReader();
  const parts = [];
  let got = 0;
  try {
    while (got < limit) {
      const { done, value } = await reader.read();
      if (done) break;
      parts.push(value);
      got += value.length;
    }
  } finally {
    await reader.cancel().catch(() => {});
  }
  const all = new Uint8Array(got);
  let at = 0;
  for (const p of parts) {
    all.set(p, at);
    at += p.length;
  }
  return all;
}

export class Connection {
  /**
   * @param {object} opts
   * @param {string} opts.url
   * @param {typeof WebSocket} [opts.WebSocketImpl]
   * @param {(fn: () => void, ms: number) => unknown} [opts.setTimer]
   * @param {(status: import('./types.js').Status) => void} opts.onStatus
   * @param {(state: LinkState) => void} opts.onState
   * @param {(line: string) => void} opts.onGcode
   */
  constructor({
    url,
    WebSocketImpl = globalThis.WebSocket,
    setTimer = (fn, ms) => setTimeout(fn, ms),
    onStatus,
    onState,
    onGcode,
  }) {
    this.url = url;
    this.WS = WebSocketImpl;
    this.setTimer = setTimer;
    this.onStatus = onStatus;
    this.onState = onState;
    this.onGcode = onGcode;
    this.nextId = 1;
    /** @type {Map<number, PendingCall>} */
    this.pending = new Map();
    /** @type {Map<string, Promise<Rpc>>} */
    this.once = new Map(); // For gcodeOnce, the promise of the script in flight under each key.
    this.attempt = 0;
    /** @type {import('./types.js').Status} */
    this.status = {};
    this.units = ['ace2k'];
    this.unit = 'ace2k'; // The first unit, the page's main one.
    /** @type {WebSocket | undefined} */
    this.ws = undefined;
    this.down = false;
    this.identified = false;
    this.retrying = false; // True when a retry of the handshake is already due.
  }

  // Opens the socket. A closed socket opens again by itself, after the backoff.
  /** @returns {void} */
  start() {
    this._open();
  }

  // Opens a new socket and sets its handlers.
  /** @returns {void} */
  _open() {
    this.ws = new this.WS(this.url);
    this.down = false;
    this.identified = false;
    this.retrying = false;
    this.ws.onopen = () => {
      this._handshake();
    };
    this.ws.onmessage = (ev) => {
      /** @type {Rpc} */
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      this._message(msg);
    };
    this.ws.onclose = () => this._closed();
  }

  /**
   * @param {string} method
   * @param {object} [params]
   * @returns {Promise<Rpc>}  The JSON-RPC result.
   */
  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      // Before start() there is no socket, and reading it throws.
      if (this.down || /** @type {WebSocket} */ (this.ws).readyState !== 1) {
        reject(new Error(CONNECTION_CLOSED));
        return;
      }
      this.pending.set(id, { resolve, reject });
      /** @type {WebSocket} */ (this.ws).send(JSON.stringify({ jsonrpc: '2.0', method, params, id }));
    });
  }

  /** @param {string} script */
  gcode(script) {
    return this.call('printer.gcode.script', { script });
  }

  // Sends a script that must reach the printer only once, such as the start of a print.
  // While a script sent under key has no answer yet, a second call with the same key gets the same answer.
  // No second request is sent.
  /**
   * @param {string} key
   * @param {string} script
   */
  gcodeOnce(key, script) {
    const inFlight = this.once.get(key);
    if (inFlight) return inFlight;
    const p = this.gcode(script).finally(() => {
      if (this.once.get(key) === p) this.once.delete(key);
    });
    this.once.set(key, p);
    return p;
  }

  // Returns the G-code files, newest first, each as its path, its time of change and its size. They come from Moonraker's file list, over HTTP.
  /**
   * @param {typeof fetch} [fetchFn]
   * @returns {Promise<{ path: string, modified: number, size: number | null }[]>}
   */
  async files(fetchFn = globalThis.fetch) {
    const res = await fetchFn('/server/files/list?root=gcodes');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const list = (await res.json())?.result;
    return (Array.isArray(list) ? list : [])
      .filter((f) => typeof f?.path === 'string' && /\.gcode$/i.test(f.path))
      .sort((a, b) => (b.modified ?? 0) - (a.modified ?? 0))
      .map((/** @type {{ path: string, modified?: number, size?: number }} */ f) => ({
        path: f.path,
        modified: f.modified ?? 0,
        // The file's size in bytes, for the reading bar's estimate (reading.js); null when Moonraker gives none.
        size: typeof f.size === 'number' && Number.isFinite(f.size) && f.size >= 0 ? f.size : null,
      }));
  }

  // Returns the URL of the largest thumbnail that the slicer wrote in a G-code file, or null when it has none.
  // Moonraker lists them in the file's metadata. Each image sits next to the file, under the gcodes root.
  // The file's time goes in the URL, so a file sliced again under the same name never shows the old image.
  /**
   * @param {string} file
   * @param {typeof fetch} [fetchFn]
   * @returns {Promise<string | null>}
   */
  async thumbnail(file, fetchFn = globalThis.fetch) {
    const res = await fetchFn(`/server/files/metadata?filename=${encodeURIComponent(file)}`);
    if (!res.ok) return null;
    return Connection.thumbOf(file, (await res.json())?.result);
  }

  // The thumbnail URL from a file's metadata, as thumbnail() reads it, or null when it lists none. fileCard
  // reads the metadata once and passes it here.
  /** @param {string} file @param {any} meta @returns {string | null} */
  static thumbOf(file, meta) {
    const thumbs = meta?.thumbnails;
    const best = (Array.isArray(thumbs) ? thumbs : [])
      .filter((/** @type {{ relative_path?: unknown }} */ x) => typeof x?.relative_path === 'string')
      .sort(
        (/** @type {{ width?: number }} */ a, /** @type {{ width?: number }} */ b) => (b.width ?? 0) - (a.width ?? 0),
      )[0];
    if (!best) return null;
    const dir = file.slice(0, file.lastIndexOf('/') + 1);
    const v = Number.isFinite(meta.modified) ? `?v=${meta.modified}` : '';
    return `/server/files/gcodes/${`${dir}${best.relative_path}`.split('/').map(encodeURIComponent).join('/')}${v}`;
  }

  // The download URL of a G-code file under the gcodes root.
  /** @param {string} file */
  static fileUrl(file) {
    return `/server/files/gcodes/${file.split('/').map(encodeURIComponent).join('/')}`;
  }

  // Up to length bytes of a file as text: from start, or the last length bytes when start is
  // negative. A server that ignores the range answers 200 with the whole file; then only length
  // bytes are read and the rest of the body is cancelled, so a large file is never downloaded.
  // That whole file holds the asked bytes for a read from 0. For the last length bytes it holds
  // them when it is no longer than length: Moonraker answers 200 to a range that covers the whole
  // file. A longer whole file is refused, as is any other range.
  /**
   * @param {string} file @param {number} start @param {number} length
   * @param {typeof fetch} [fetchFn] @returns {Promise<string>}
   */
  async rangeText(file, start, length, fetchFn = globalThis.fetch) {
    const range = start < 0 ? `bytes=${start}` : `bytes=${start}-${start + length - 1}`;
    const res = await fetchFn(Connection.fileUrl(file), { headers: { Range: range } });
    if (res.status !== 206 && res.status !== 200) throw new Error(`HTTP ${res.status}`);
    if (!res.body) throw new Error('no body');
    // A 200 is the whole file: the right bytes for a read from the start, or for the last length
    // bytes of a file no longer than length; the wrong ones for any other.
    const whole = res.status === 200 && start !== 0;
    // Content-Length, when the server sends it, refuses a long file before any byte is read.
    if (whole && (start > 0 || Number(res.headers.get('Content-Length')) > length)) {
      await res.body.cancel();
      throw new Error('range ignored');
    }
    // A whole file of unknown size is read one byte past length: a file that long is refused.
    const all = await readUpTo(res.body, whole ? length + 1 : length);
    if (all.length > length && whole) throw new Error('range ignored');
    return new TextDecoder().decode(all.subarray(0, length));
  }

  // What a file card shows: the colours the file uses, the slicer's time, the thumbnail URL and
  // the file's time. Colours come from the file itself (fileColours), else from Moonraker's
  // metadata, else null.
  /** @param {string} file @param {typeof fetch} [fetchFn] */
  async fileCard(file, fetchFn = globalThis.fetch) {
    const meta = await this.fileMeta(file, fetchFn);
    /** @type {import('./slicer-file.js').FileColour[] | null} */
    let colours = null;
    try {
      const [head, tail] = await Promise.all([
        this.rangeText(file, 0, HEAD_BYTES, fetchFn),
        this.rangeText(file, -TAIL_BYTES, TAIL_BYTES, fetchFn),
      ]);
      colours = fileColours(head, tail);
    } catch {
      colours = null;
    }
    if (!colours && Array.isArray(meta.filament_colors) && meta.filament_colors.length) {
      // The types come as "PLA;PETG", or as a list.
      const types = Array.isArray(meta.filament_type)
        ? meta.filament_type.map(String)
        : String(meta.filament_type ?? '').split(';');
      colours = meta.filament_colors.map((/** @type {string} */ colour, /** @type {number} */ i) => ({
        colour,
        material: types[i] || types[0] || null,
        vendor: null,
      }));
    }
    return {
      colours,
      time: Number.isFinite(meta.estimated_time) ? meta.estimated_time : null,
      thumb: Connection.thumbOf(file, meta), // the metadata read above: no second request
      ...Connection.bytesOf(meta),
    };
  }

  // Moonraker's metadata of a file, or {} when it cannot be read: a card still shows the file's own colours.
  /** @param {string} file @param {typeof fetch} [fetchFn] @returns {Promise<any>} */
  fileMeta(file, fetchFn = globalThis.fetch) {
    return fetchFn(`/server/files/metadata?filename=${encodeURIComponent(file)}`)
      .then(async (res) => (res.ok ? ((await res.json())?.result ?? {}) : {}))
      .catch(() => ({}));
  }

  // The G-code's own bytes, past the header and before the config block: progress is counted over them. And the
  // time the file changed, which tells a file sliced again under the same name.
  /** @param {any} meta */
  static bytesOf(meta) {
    return {
      gcodeStart: Number.isFinite(meta.gcode_start_byte) ? meta.gcode_start_byte : null,
      gcodeEnd: Number.isFinite(meta.gcode_end_byte) ? meta.gcode_end_byte : null,
      modified: Number.isFinite(meta.modified) ? meta.modified : null,
    };
  }

  // Only the bytes and the time of change, from the metadata alone: no range of the file is read.
  /** @param {string} file @param {typeof fetch} [fetchFn] */
  async fileBytes(file, fetchFn = globalThis.fetch) {
    const meta = await this.fileMeta(file, fetchFn);
    return Connection.bytesOf(meta);
  }

  // Identifies the page to Moonraker once, then subscribes. A failure is retried after the backoff.
  /** @returns {Promise<void>} */
  async _handshake() {
    try {
      if (!this.identified) {
        await this.call('server.connection.identify', {
          client_name: 'ace2k-web',
          version: '0.1.0',
          type: 'web',
          url: 'local',
        });
        this.identified = true;
      }
      await this._subscribe();
    } catch (e) {
      this._failed(/** @type {Error} */ (e)); // call and _message only reject with an Error.
    }
  }

  // A failure of the page itself, not of Klipper. It is reported with its message.
  // The handshake is tried again after the reconnect backoff, until it works or the socket closes.
  // A call that failed because the socket closed is reported by _closed, not here.
  /** @param {Error} e */
  _failed(e) {
    if (this.down) return;
    this.onState({ online: true, klippy: 'page_error', message: e.message });
    if (this.retrying) return;
    this.retrying = true;
    const ws = this.ws;
    const delay = BACKOFF_S[Math.min(this.attempt, BACKOFF_S.length - 1)];
    this.attempt += 1;
    this.setTimer(() => {
      // A retry belongs to its socket. The retry of a closed socket leaves the new socket's state alone.
      if (this.ws !== ws) return;
      this.retrying = false;
      if (!this.down) this._handshake();
    }, delay * 1000);
  }

  // Reads Klipper's state. When it is ready, subscribes to the units and the printer objects.
  /** @returns {Promise<void>} */
  async _subscribe() {
    /** @type {string} */
    let state;
    try {
      state = (await this.call('printer.info')).state;
    } catch (e) {
      // Moonraker answers 503 ("Klippy Host not connected") while Klipper is not connected to it.
      // That is Klipper's state, not a failure of the page. Moonraker says when Klipper is ready.
      if (/** @type {{ code?: unknown }} */ (e).code !== 503) throw e;
      state = 'disconnected';
    }
    if (state !== 'ready') {
      this.attempt = 0;
      this.onState({ online: true, klippy: state });
      return;
    }
    // ace2k_units names every unit of printer.cfg. Each unit has its own object name.
    const q = await this.call('printer.objects.query', { objects: { ace2k_units: null } });
    const listed = q?.status?.ace2k_units?.units;
    this.units = Array.isArray(listed) && listed.length ? listed : ['ace2k'];
    this.unit = this.units[0];
    const names = ['ace2k_units', ...this.units, ...OBJECTS.filter((o) => o !== 'ace2k')];
    const sub = await this.call('printer.objects.subscribe', {
      objects: Object.fromEntries(names.map((o) => [o, null])),
    });
    this.status = mergeStatus({}, sub.status);
    this.attempt = 0;
    this.onStatus(this.status);
    this.onState({ online: true, klippy: 'ready' });
  }

  /** @param {Rpc} msg  A parsed frame (see _open). */
  _message(msg) {
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const p = /** @type {PendingCall} */ (this.pending.get(msg.id)); // has() was checked above.
      this.pending.delete(msg.id);
      // The error keeps Moonraker's code, such as 503 when Klipper is not connected.
      if (msg.error)
        p.reject(Object.assign(new Error(msg.error.message || JSON.stringify(msg.error)), { code: msg.error.code }));
      else p.resolve(msg.result);
      return;
    }
    switch (msg.method) {
      case 'notify_status_update':
        this.status = mergeStatus(this.status, msg.params[0]);
        this.onStatus(this.status);
        break;
      case 'notify_gcode_response':
        this.onGcode(msg.params[0]);
        break;
      case 'notify_klippy_ready':
        this._subscribe().catch((e) => this._failed(e));
        break;
      case 'notify_klippy_shutdown':
        this.onState({ online: true, klippy: 'shutdown' });
        break;
      case 'notify_klippy_disconnected':
        this.onState({ online: true, klippy: 'disconnected' });
        break;
      default:
        break;
    }
  }

  // The socket closed. The calls in flight fail, and a new socket opens after the backoff.
  /** @returns {void} */
  _closed() {
    this.down = true;
    for (const p of this.pending.values()) p.reject(new Error(CONNECTION_CLOSED));
    this.pending.clear();
    this.onState({ online: false });
    const delay = BACKOFF_S[Math.min(this.attempt, BACKOFF_S.length - 1)];
    this.attempt += 1;
    this.setTimer(() => this._open(), delay * 1000);
  }
}

// The states that Moonraker reports for Klipper. Each has its own sentence in the catalogue.
export const KLIPPY_STATES = ['startup', 'ready', 'shutdown', 'error', 'disconnected'];

// The banner above the page, as a key and vars for t(). It shows the demo, a link that is down, or Klipper
// not ready. Otherwise it is hidden. online is the link to Moonraker (app.online), not ready().
// message says why the page's handshake failed. main.js shows it in #banner, a live region of its own.
/**
 * @param {{ demo: boolean, online: boolean, klippy: string | null | undefined, message?: string }} state
 * @returns {BannerView}
 */
export function bannerView({ demo, online, klippy, message }) {
  if (demo && klippy === 'demo_missing')
    return { hidden: false, className: 'banner', key: 'conn.demo_missing', vars: {} };
  if (demo) return { hidden: false, className: 'banner demo', key: 'conn.demo', vars: {} };
  if (!online) return { hidden: false, className: 'banner', key: 'conn.offline', vars: {} };
  if (klippy === 'ready') return { hidden: true, className: null, key: null, vars: {} };
  /** @param {string} key @param {Record<string, string>} [vars] */
  const shown = (key, vars = {}) => ({ hidden: /** @type {const} */ (false), className: 'banner', key, vars });
  if (klippy === 'page_error') return shown('conn.page_error', { message: message || '…' });
  if (!klippy) return shown('klippy.checking');
  if (KLIPPY_STATES.includes(klippy)) return shown(`klippy.state.${klippy}`);
  return shown('klippy.unknown', { state: klippy });
}
