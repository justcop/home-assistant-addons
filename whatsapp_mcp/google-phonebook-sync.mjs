import { normalisePhone, isHumanName } from './archive-data.mjs';

const API = 'http://supervisor/core/api';
const INTERVAL_MS = 6 * 60 * 60 * 1000;
const RETRY_MS = 5 * 60 * 1000;
const MAX_CONTACTS = 5000;
const WORKERS = 4;

// Google Phonebook, not this app, handles OAuth, refresh tokens and People API
// pagination. This bridge only invokes its documented, cached lookup service.
export class GooglePhonebookBridge {
  constructor(store, { token = process.env.SUPERVISOR_TOKEN, fetchImpl = fetch, apiBase = API } = {}) {
    this.store = store;
    this.token = token;
    this.fetch = fetchImpl;
    this.apiBase = apiBase;
    this.timer = null;
    this.running = false;
    this.closed = false;
    this.lastAttempt = null;
    this.lastSuccess = null;
    this.lastError = 'Awaiting Google Phonebook authorisation in Home Assistant';
    this.lastChecked = 0;
    this.ready = false;
  }
  status() {
    return {
      available: this.ready,
      running: this.running,
      matched: Object.keys(this.store.googleNames).length,
      lastSync: this.lastSuccess,
      lastChecked: this.lastChecked,
      status: this.lastError ? (this.ready ? 'error' : 'setup_required') : 'connected',
      message: this.lastError ? this.lastError.slice(0, 140) : 'Google Contacts sync enabled'
    };
  }
  start() { this.schedule(1800); }
  stop() { this.closed = true; clearTimeout(this.timer); }
  schedule(ms) {
    if (this.closed) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.sync().catch(err => console.error('Google Phonebook bridge:', err.message));
    }, ms);
    this.timer.unref();
  }
  async request(method, path, body) {
    if (!this.token) throw new Error('Home Assistant API permission not configured');
    const res = await this.fetch(this.apiBase + path, {
      method,
      headers: { Authorization: 'Bearer ' + this.token,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(12000)
    });
    if (!res.ok) throw new Error('Home Assistant contact lookup unavailable (HTTP ' + res.status + ')');
    return await res.json();
  }
  async lookup(phone) {
    const result = await this.request('POST',
      '/services/google_phonebook/lookup?return_response',
      {phone_number: '+' + phone});
    const entry = result?.service_response;
    if (!entry || typeof entry.found !== 'boolean')
      throw new Error('Unexpected Google Phonebook response');
    if (entry.found && (!isHumanName(entry.name) || String(entry.name).length > 160))
      return null;
    return entry.found ? entry.name.trim() : null;
  }
  async sync() {
    if (this.running || this.closed) return false;
    this.running = true;
    this.lastAttempt = new Date().toISOString();
    try {
      // Never send a lookup until this specifically named read-only integration
      // has been authorised and registered its service.
      const registered = await this.request('GET', '/services');
      const service = Array.isArray(registered) && registered.find(s => s.domain === 'google_phonebook');
      this.ready = !!(service?.services?.lookup);
      if (!this.ready) throw new Error('Finish Google Phonebook setup in Home Assistant → Devices & Services');
      const numbers = this.store.contactNumbers();
      if (numbers.length > MAX_CONTACTS) throw new Error('Archive contact list exceeds safe lookup limit');
      const next = {...this.store.googleNames};
      let cursor = 0, failed = 0, checked = 0;
      const worker = async () => {
        while (!this.closed) {
          const index = cursor++;
          if (index >= numbers.length) return;
          const phone = numbers[index];
          try {
            const name = await this.lookup(phone);
            if (name) next[phone] = name;
            else delete next[phone];
            checked++;
          } catch {
            failed++;
          }
        }
      };
      await Promise.all(Array.from({length: WORKERS}, () => worker()));
      if (this.closed) return false;
      if (failed && !checked) throw new Error('Google Phonebook lookups failed; check Google authorisation');
      this.store.setGoogleNames(next);
      this.lastSuccess = new Date().toISOString();
      this.lastChecked = checked;
      this.lastError = failed ? failed + ' contact lookups temporarily unavailable; cached names retained' : null;
      console.log('Google Phonebook cache sync: ' + checked + ' numbers checked, ' + Object.keys(next).length + ' named, ' + failed + ' unavailable.');
      return true;
    } catch (err) {
      this.lastError = err.message;
      this.ready = false;
      console.log('Google Phonebook sync pending: ' + this.lastError);
      return false;
    } finally {
      this.running = false;
      this.schedule(this.lastError ? RETRY_MS : INTERVAL_MS);
    }
  }
}
