// Read-only public GitHub Actions health monitor for WhatsApp maintenance.
// It never needs a GitHub token or access to WhatsApp messages/database.
const URL_ROOT = 'https://api.github.com/repos/justcop/home-assistant-addons/actions/workflows/';
const CHECKS = [
  {name: 'WhatsApp upstream checks', file: 'whatsapp-upstream.yml'},
  {name: 'Add-on Docker image publication', file: 'addon-delivery.yml'}
];
const NOTIFICATION_ID = 'whatsapp_maintenance_pipeline_failure';
const INTERVAL_MS = 60 * 60 * 1000;

export class WhatsAppMaintenanceMonitor {
  constructor({ request = fetch, haToken = process.env.SUPERVISOR_TOKEN,
                intervalMs = INTERVAL_MS, logger = console } = {}) {
    this.request = request;
    this.haToken = haToken;
    this.intervalMs = intervalMs;
    this.logger = logger;
    this.timer = null;
    this.running = false;
    this.lastProblem = undefined;
    this.currentProblem = null;
  }
  async _latest(file) {
    const url = URL_ROOT + encodeURIComponent(file) + '/runs?per_page=1&branch=main';
    const response = await this.request(url, {
      headers: {'Accept': 'application/vnd.github+json',
        'User-Agent': 'WhatsApp-Archive-Maintenance-Monitor'},
      signal: AbortSignal.timeout(15000)
    });
    if (!response.ok) throw new Error('GitHub Actions status API HTTP ' + response.status);
    const body = await response.json();
    const run = body.workflow_runs?.[0];
    if (!run) return null;
    return {status: run.status, conclusion: run.conclusion,
      // Links are derived from GitHub IDs only, not untrusted API URLs.
      id: Number(run.id), updated: run.updated_at};
  }
  async _notify(message) {
    if (!this.haToken) throw new Error('Home Assistant Core API token unavailable');
    const action = message ? 'create' : 'dismiss';
    const payload = message ? {
      notification_id: NOTIFICATION_ID,
      title: 'WhatsApp update maintenance needs attention',
      message: message + '\n\nReview justcop/home-assistant-addons → Actions. ' +
        'The live WhatsApp archive remains on its existing image; no automatic production update was attempted.'
    } : {notification_id: NOTIFICATION_ID};
    const response = await this.request('http://supervisor/core/api/services/persistent_notification/' + action, {
      method: 'POST', headers: {'Authorization': 'Bearer ' + this.haToken,
        'Content-Type': 'application/json'}, body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000)
    });
    if (!response.ok) throw new Error('Home Assistant notification API HTTP ' + response.status);
  }
  async check() {
    if (this.running) return;
    this.running = true;
    try {
      const issues = [];
      for (const item of CHECKS) {
        const run = await this._latest(item.file);
        if (!run || run.status !== 'completed') continue;
        if (run.conclusion !== 'success' && run.conclusion !== 'skipped') {
          issues.push(item.name + ': ' + String(run.conclusion || 'unknown') +
            ' (run ' + run.id + ')');
        }
      }
      const problem = issues.join('; ') || null;
      this.currentProblem = problem;
      // First healthy observation also dismisses a previous notification
      // left behind across an add-on restart.
      if (problem !== this.lastProblem) {
        await this._notify(problem);
        this.lastProblem = problem;
      }
    } catch (error) {
      // An inability to query GitHub is not equivalent to a failed release.
      // Do not clear an existing HA warning when the monitor is offline.
      this.logger.warn('WhatsApp maintenance status unavailable: ' +
        String(error.message).slice(0, 100));
    } finally { this.running = false; }
  }
  start() {
    if (this.timer) return;
    this.timer = setInterval(() => { void this.check(); }, this.intervalMs);
    this.timer.unref?.();
    void this.check();
  }
  close() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
  status() { return {problem: this.currentProblem, running: this.running}; }
}
