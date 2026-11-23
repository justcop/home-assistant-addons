// Passive GitHub release/upstream monitor. Read-only public API; no GitHub
// write token or real WhatsApp messages enter this component.
const API = 'https://api.github.com/repos/justcop/home-assistant-addons';
const ISSUE_LABEL = 'whatsapp-upstream-action-needed';
const WORKFLOWS = ['whatsapp-upstream.yml', 'addon-delivery.yml'];
const INTERVAL = 6 * 60 * 60 * 1000;

export function collectMaintenanceProblems(issues, workflowRuns) {
  const problems = [];
  for (const issue of issues || []) {
    if (issue && issue.state === 'open' &&
        Array.isArray(issue.labels) &&
        issue.labels.some(label => (typeof label === 'string' ? label : label.name) === ISSUE_LABEL)) {
      problems.push('Upstream candidate requires review: ' + String(issue.title || 'GitHub issue').slice(0, 110));
    }
  }
  for (const [name, runs] of Object.entries(workflowRuns || {})) {
    const latest = runs?.[0];
    if (!latest || latest.status !== 'completed') continue;
    if (!['success', 'skipped', 'neutral'].includes(latest.conclusion)) {
      problems.push(name + ' last run failed (' + String(latest.conclusion || 'unknown') + ')');
    }
  }
  return problems.slice(0, 5);
}
export class WhatsAppUpstreamMonitor {
  constructor({ alerts, request = fetch, intervalMs = INTERVAL, now = () => Date.now() } = {}) {
    if (!alerts) throw new Error('Home Assistant maintenance alerts required');
    this.alerts = alerts;
    this.request = request;
    this.intervalMs = intervalMs;
    this.now = now;
    this.timer = null;
    this.running = false;
    this.errors = 0;
    this.lastResult = null;
  }
  async _get(url) {
    const result = await this.request(url, {
      headers: { 'Accept': 'application/vnd.github+json',
        'User-Agent': 'whatsapp-private-upstream-monitor' },
      signal: AbortSignal.timeout(15000)
    });
    if (!result.ok) throw new Error('GitHub API HTTP ' + result.status);
    return result.json();
  }
  async check() {
    if (this.running) return;
    this.running = true;
    try {
      const [issues, ...runs] = await Promise.all([
        this._get(API + '/issues?state=open&labels=' + ISSUE_LABEL + '&per_page=30'),
        ...WORKFLOWS.map(name => this._get(API + '/actions/workflows/' + name +
          '/runs?branch=main&per_page=1'))
      ]);
      const workflows = Object.fromEntries(WORKFLOWS.map((name, i) =>
        [name, runs[i]?.workflow_runs || []]));
      const problems = collectMaintenanceProblems(issues, workflows);
      this.errors = 0;
      this.lastResult = { checked: new Date(this.now()).toISOString(),
        pendingIssues: issues.length, problems };
      if (problems.length) await this.alerts.failure(problems.join('; '));
      else await this.alerts.recovered();
    } catch (error) {
      this.errors++;
      // A single transient GitHub outage should not produce a new HA warning.
      // Preserve any existing maintenance alert until reliable recovery.
      if (this.errors >= 2)
        await this.alerts.failure('Unable to check WhatsApp upstream/release automation after ' +
          this.errors + ' attempts (' + String(error.message).slice(0, 90) + ')');
    } finally {
      this.running = false;
    }
  }
  start() {
    if (this.timer) return;
    this.timer = setInterval(() => {
      void this.check().catch(e => console.error('WhatsApp upstream alert delivery failed:', e.message));
    }, this.intervalMs);
    void this.check().catch(e => console.error('WhatsApp upstream alert delivery failed:', e.message));
  }
  close() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }
}
