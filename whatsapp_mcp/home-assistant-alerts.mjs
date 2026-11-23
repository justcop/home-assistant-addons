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
