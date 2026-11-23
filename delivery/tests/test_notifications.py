"""Failure visibility survives retries, target-app crashes and HA outages."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "addon_updater/app"))
from notifier import reconcile


def state(mode="unavailable", app_state="started"):
    return {"apps":{"demo":{"name":"Demo", "mode":mode, "state":app_state}}, "transactions":{}, "error":None}


def test_independent_outage_detection_has_threshold_and_one_stable_alert():
    data=state(); calls=[]
    send=lambda service,body:calls.append((service,body))
    reconcile(data,send)
    assert calls==[]
    reconcile(data,send)
    reconcile(data,send)
    assert len(calls)==1 and calls[0][0]=="create"
    assert calls[0][1]["notification_id"]=="my_addons_demo_failure"
    assert "readiness" in calls[0][1]["message"]


def test_deliberately_stopped_application_does_not_raise_an_alert():
    data=state(app_state="stopped"); calls=[]
    for _ in range(3):reconcile(data,lambda *args:calls.append(args))
    assert calls==[]


def test_interrupted_update_alerts_immediately_and_offers_recovery():
    data=state();data["transactions"]["demo"]={"target":"a"*40};calls=[]
    reconcile(data,lambda service,body:calls.append((service,body)))
    assert len(calls)==1
    assert "Recover previous compatible code" in calls[0][1]["message"]


def test_notification_delivery_failure_preserves_original_error_and_retries():
    data=state(mode="failed");data["apps"]["demo"]["error"]="Original failed activation"
    def offline(*args):raise ConnectionError("HA restarting")
    reconcile(data,offline)
    assert data["apps"]["demo"]["error"]=="Original failed activation"
    assert data["notification_error"]
    assert data["notifications"]["demo"]["sent"] is None
    calls=[];reconcile(data,lambda *args:calls.append(args))
    assert len(calls)==1 and data["notification_error"] is None


def test_verified_recovery_dismisses_once():
    data=state(mode="failed");calls=[]
    send=lambda *args:calls.append(args)
    reconcile(data,send)
    data["apps"]["demo"]["mode"]="current"
    reconcile(data,send);reconcile(data,send)
    assert [c[0] for c in calls]==["create","dismiss"]


def test_repository_outage_cannot_clear_an_unverified_application_alert():
    data=state(mode="failed");calls=[]
    send=lambda *args:calls.append(args)
    reconcile(data,send)
    data["apps"]["demo"]["mode"]="current"
    data["error"]="Unable to check current applications"
    reconcile(data,send)
    assert all(c[0]!="dismiss" for c in calls)
