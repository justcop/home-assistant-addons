import json
from datetime import date
from analytics.attendance import AttendanceImporter
from analytics.db import Database
from analytics.gigs import GigStore

def fixture(id, artist="Muse", day="15-06-2025"):
    return {"id":id, "artist":{"name":artist},
            "venue":{"name":"Wembley Stadium"}, "eventDate":day,
            "url":"https://www.setlist.fm/setlist/muse/2025/wembley-stadium-"+id+".html",
            "sets":{"set":[{"song":[{"name":"Hysteria"}]}]}, "versionId":"v1"}

class FakeResponse:
    def __init__(self,payload):
        self.data=json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def read(self,lim):
        return self.data[:lim]

def test_attended_import_resumes_without_duplicate_calls_or_rows(tmp_path):
    database=Database(tmp_path/"listening.sqlite3")
    config={"timezone":"Europe/London","setlist_cache_retention_days":30,
            "setlist_daily_request_budget":3,"setlistfm_api_key":"test",
            "setlistfm_user_id":"someone"}
    store=GigStore(database,config)
    calls=[]
    def opener(req,timeout):
        calls.append(req.full_url)
        page=len(calls)
        if page==1:
            return FakeResponse({"setlist":[fixture("123abc")],
                                 "total":2,"itemsPerPage":1})
        return FakeResponse({"setlist":[fixture("456def","Radiohead")],
                             "total":2,"itemsPerPage":1})
    importer=AttendanceImporter(store,opener=opener)
    assert importer.run(maximum=1)==1
    assert importer.status()["next_page"]==2
    assert len(store.list())==1
    assert importer.run(maximum=1)==1
    assert len(store.list())==2
    assert importer.status()["last_completed"]==date.today().isoformat() or importer.status()["last_completed"]==store.today().isoformat()
    assert importer.run(maximum=1)==0
    assert len(calls)==2
    assert store.list()[0]["sets"] or store.list()[1]["sets"]

def test_classification_preserved_when_same_attendance_reimported(tmp_path):
    db=Database(tmp_path/"gigs.sqlite3")
    cfg={"timezone":"Europe/London","setlist_cache_retention_days":30,
         "setlist_daily_request_budget":10,"setlistfm_api_key":"key",
         "setlistfm_user_id":"account"}
    store=GigStore(db,cfg)
    importer=AttendanceImporter(store)
    today=store.today()
    importer._apply_page([fixture("abc456")],1,1,20,today,None)
    gig=store.list()[0]
    assert gig["show_type"]=="unknown"
    store.classify(gig["id"],"festival","Example Festival")
    with db.connect() as conn:
        conn.execute("UPDATE gigs SET last_checked='2026-06-15' WHERE id=?", (gig["id"],))
    importer._apply_page([fixture("abc456")],1,1,20,today,None)
    assert len(store.list())==1
    assert store.list()[0]["festival"]=="Example Festival"
    assert store.list()[0]["last_checked"]=="2026-06-15"
