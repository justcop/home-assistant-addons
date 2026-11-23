"""Deduplicated deployment/availability alerts, independently of target apps."""
import hashlib
import json
import os

import requests


def deliver(service, body):
    response = requests.post(
        "http://supervisor/core/api/services/persistent_notification/" + service,
        headers={"Authorization": "Bearer " + os.environ["SUPERVISOR_TOKEN"]},
        json=body, timeout=10)
    response.raise_for_status()


def reconcile(state, send=deliver):
    """Two failed checks for transient outages; interrupted updates alert at once.

    Notification failure never replaces the original deployment failure. The
    pending alert stays in /data and is retried after HA connectivity returns.
    """
    alerts = state.setdefault("notifications", {})
    desired = {}
    link = "[Open My Add-ons](/hassio/addon/64b28acc_addon_updater/info)."
    if state.get("error"):
        desired["repository"] = ("Repository update check failed. Automatic code updates may be delayed. "
                                 "Check repository/network access in My Add-ons. " + link, False)
    for ident, row in state.get("apps", {}).items():
        if ident in state.get("transactions", {}):
            desired[ident] = (row.get("name", ident) + ": a code update failed or was interrupted. "
                              "Open My Add-ons and use Recover previous compatible code. " + link, True)
        elif row.get("error") or row.get("mode") == "failed":
            desired[ident] = (row.get("name", ident) + ": the application reported an update/startup failure. "
                              "Check its status and recovery action in My Add-ons. " + link, True)
        elif row.get("mode") == "unavailable" and row.get("state") in {"started", "error"}:
            desired[ident] = (row.get("name", ident) + ": the application is not responding to its independent "
                              "readiness check. Open its Home Assistant app page and inspect its startup/status. " + link, False)
    notification_errors = []
    for ident in set(alerts) | set(desired):
        item = alerts.setdefault(ident, {"count": 0, "sent": None})
        notification_id = "my_addons_" + ident + "_failure"
        if ident in desired:
            message, urgent = desired[ident]
            item["count"] = item.get("count", 0) + 1
            if not urgent and item["count"] < 2:
                continue
            signature = hashlib.sha256(message.encode()).hexdigest()
            if item.get("sent") == signature:
                continue
            try:
                send("create", {"notification_id": notification_id, "title": "My Add-ons needs attention", "message": message})
                item["sent"] = signature
            except Exception:
                notification_errors.append(ident)
        else:
            # A failed repository check cannot establish recovery of app alerts.
            if state.get("error") and ident != "repository":
                continue
            if item.get("sent"):
                try:
                    send("dismiss", {"notification_id": notification_id})
                except Exception:
                    notification_errors.append(ident)
                    continue
            alerts.pop(ident, None)
    state["notification_error"] = ("Home Assistant notification delivery is unavailable; pending alerts will be retried."
                                   if notification_errors else None)
