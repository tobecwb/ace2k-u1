// Moonraker's websocket JSON-RPC: identify, subscribe, merge deltas, call, reconnect.
export const OBJECTS = [
  'ace2k', 'ace2k_u1_history', 'print_task_config', 'print_stats',
  'filament_motion_sensor e0_filament', 'filament_motion_sensor e1_filament',
  'filament_motion_sensor e2_filament', 'filament_motion_sensor e3_filament',
];
export const BACKOFF_S = [1, 2, 5, 10];

export function mergeStatus(status, delta) {
  const out = { ...status };
  for (const [name, fields] of Object.entries(delta || {})) out[name] = { ...(out[name] || {}), ...fields };
  return out;
}

export class Connection {
  constructor({ url, WebSocketImpl = globalThis.WebSocket, setTimer = (fn, ms) => setTimeout(fn, ms), onStatus, onState, onGcode }) {
    Object.assign(this, { url, WS: WebSocketImpl, setTimer, onStatus, onState, onGcode });
    this.nextId = 1;
    this.pending = new Map();
    this.attempt = 0;
    this.status = {};
  }

  start() { this._open(); }

  _open() {
    this.ws = new this.WS(this.url);
    this.down = false;
    this.ws.onopen = () => { this._handshake(); };
    this.ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      this._message(msg);
    };
    this.ws.onclose = () => this._closed();
  }

  call(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      if (this.down || this.ws.readyState !== 1) {
        reject(new Error('connection closed'));
        return;
      }
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ jsonrpc: '2.0', method, params, id }));
    });
  }

  gcode(script) { return this.call('printer.gcode.script', { script }); }

  async _handshake() {
    try {
      await this.call('server.connection.identify', { client_name: 'ace2k-web', version: '0.1.0', type: 'web', url: 'local' });
      await this._subscribe();
    } catch (e) {
      this._failed(e);
    }
  }

  // a call that failed because the socket closed is reported by _closed, not here
  _failed(e) {
    if (!this.down) this.onState({ online: true, klippy: 'error', message: e.message });
  }

  async _subscribe() {
    const info = await this.call('printer.info');
    if (info.state !== 'ready') {
      this.attempt = 0;
      this.onState({ online: true, klippy: info.state });
      return;
    }
    const sub = await this.call('printer.objects.subscribe', { objects: Object.fromEntries(OBJECTS.map((o) => [o, null])) });
    this.status = mergeStatus({}, sub.status);
    this.attempt = 0;
    this.onStatus(this.status);
    this.onState({ online: true, klippy: 'ready' });
  }

  _message(msg) {
    if (msg.id !== undefined && this.pending.has(msg.id)) {
      const p = this.pending.get(msg.id);
      this.pending.delete(msg.id);
      if (msg.error) p.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
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

  _closed() {
    this.down = true;
    for (const p of this.pending.values()) p.reject(new Error('connection closed'));
    this.pending.clear();
    this.onState({ online: false });
    const delay = BACKOFF_S[Math.min(this.attempt, BACKOFF_S.length - 1)];
    this.attempt += 1;
    this.setTimer(() => this._open(), delay * 1000);
  }
}
