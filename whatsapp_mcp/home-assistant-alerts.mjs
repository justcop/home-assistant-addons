// Narrowly scoped Home Assistant Core notification adapter.
// This is intentionally separate from the read-only WhatsApp API.
const id = 'whatsapp_cloud_backup_failure';
export class HomeAssistantBackupAlerts {
  constructor({ token = process.env.SUPERVISOR_TOKEN, request = fetch } = {}) {
    this.token = token;
    this.request = request;
    this.lastError = null;
    this.pending = Promise.resolve();
  }
  async _service(name, data) {
    if (!this.token) throw new Error('Home Assistant notification token unavailable');
    const res = await this.request('http://supervisor/core/api/services/persistent_notification/' + name, {
      method: 'POST',
      headers: { Authorization: 'Bearer ' + this.token, 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
      signal: AbortSignal.timeout(10000)
    });
    if (!res.ok) throw new Error('Home Assistant notification API returned HTTP ' + res.status);
  }
  failure(message) {
    const safeMessage = String(message || 'Unknown backup error').slice(0, 230);
    // Queue operations so an immediate recovery cannot race an in-flight
    // failure notification and leave an obsolete notification on screen.
    const operation = this.pending.catch(() => {}).then(async () => {
      if (safeMessage === this.lastError) return;
      await this._service('create', {
        notification_id: id,
        title: 'WhatsApp cloud backup failed',
        message: 'The encrypted WhatsApp cloud backup needs attention. The live archive is unaffected.\n\nReason: ' +
          safeMessage + '\n\nOpen WhatsApp Archive → Settings → Cloud backup & recovery.'
      });
      this.lastError = safeMessage;
    });
    this.pending = operation;
    return operation;
  }
  recovered() {
    const operation = this.pending.catch(() => {}).then(async () => {
      if (this.lastError === null) return;
      await this._service('dismiss', { notification_id: id });
      this.lastError = null;
    });
    this.pending = operation;
    return operation;
  }
}


// Separate fixed notification ID for upstream/release health.
// This class only posts a summary of public GitHub status. It cannot access
// messages, modify WhatsApp, or deploy an update.
export class HomeAssistantUpstreamAlerts extends HomeAssistantBackupAlerts {
  failure(message) {
    const safe = String(message || 'WhatsApp update problem').slice(0, 380);
    const operation = this.pending.catch(() => {}).then(async () => {
      if (safe === this.lastError) return;
      await this._service('create', {
        notification_id: 'whatsapp_upstream_maintenance',
        title: 'WhatsApp update requires attention',
        message: safe + '\\n\\nThe existing WhatsApp Archive continues running. Review GitHub Actions and outstanding upstream PRs before updating; never restore an older message database as part of an app rollback.'
      });
      this.lastError = safe;
    });
    this.pending = operation;
    return operation;
  }
  recovered() {
    const operation = this.pending.catch(() => {}).then(async () => {
      if (this.lastError === null) return;
      await this._service('dismiss', { notification_id: 'whatsapp_upstream_maintenance' });
      this.lastError = null;
    });
    this.pending = operation;
    return operation;
  }
}
