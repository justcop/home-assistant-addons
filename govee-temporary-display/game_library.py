"""Small persistent PS5 catalogue, independent of replaceable application code."""
import json
import time
import threading
from pathlib import Path
import artwork_store as artwork

FILE=Path("/data/ps5_games.json")
LOCK=threading.RLock()
UNKNOWN={"", "none", "unknown", "unavailable", "null", "off", "idle"}
MAX_GAMES=2000
MAX_ART=100

def key(title):
    if not isinstance(title,str) or not 1<=len(title.strip())<=160:
        return None
    value=artwork._normalize(title)
    return value if value not in UNKNOWN else None

def _read():
    if not FILE.exists():
        return {}
    data=json.loads(FILE.read_text(encoding="utf-8"))
    if not isinstance(data,dict) or any(not isinstance(v,dict) for v in data.values()):
        raise ValueError("Invalid PS5 library; refusing to overwrite")
    return data

def _write(data):
    artwork._atomic_write(FILE,json.dumps(data,ensure_ascii=False,indent=2).encode("utf-8"))

def observe(title,activity,now=None):
    k=key(title)
    if activity!="playing" or not k:
        return
    now=int(time.time() if now is None else now)
    with LOCK:
        data=_read()
        row=data.get(k)
        if row is not None and now-row.get("last_seen",0)<60:
            return
        if row is None and len(data)>=MAX_GAMES:
            raise ValueError("PS5 library is full")
        if row is None:
            row={"title":title.strip(),"first_seen":now,"rotation_minutes":10}
            data[k]=row
        row["last_seen"]=now
        _write(data)

def _mapped(title,items):
    return [a["id"] for a in items if any(m.get("kind")=="game" and key(m.get("value"))==key(title) for m in a.get("mappings",[]))]

def _playlist(row,items):
    available={a["id"] for a in items}
    return [i for i in row.get("artwork_ids",_mapped(row["title"],items)) if i in available]

def list_games():
    items=artwork.gallery()["items"]
    with LOCK:
        data=_read()
    return [dict(row,artwork_ids=_playlist(row,items),custom="artwork_ids" in row)
            for row in sorted(data.values(),key=lambda r:(-r.get("last_seen",0),r["title"].casefold()))]

def update(body):
    title=body.get("title")
    k=key(title)
    ids=body.get("artwork_ids")
    minutes=body.get("rotation_minutes",10)
    if not k or not isinstance(ids,list) or len(ids)>MAX_ART or any(not isinstance(i,str) for i in ids):
        raise ValueError("Invalid game or artwork playlist")
    if type(minutes) is not int or not 1<=minutes<=120:
        raise ValueError("Rotation interval must be 1–120 minutes")
    available={a["id"] for a in artwork.gallery()["items"]}
    if len(set(ids))!=len(ids) or any(i not in available for i in ids):
        raise ValueError("Choose unique, available artwork")
    with LOCK:
        data=_read()
        if k not in data:
            raise ValueError("Game has not been detected")
        data[k].update(artwork_ids=ids,rotation_minutes=minutes)
        _write(data)
        return dict(data[k],custom=True)

class Rotation:
    """Monotonic, per-session rotation; artwork changes never alter the timer."""
    def __init__(self):
        self.signature=None
        self.started=0

    def select(self,title,activity,visible,now):
        k=key(title)
        if not visible or activity!="playing" or not k:
            self.signature=None
            return None
        items=artwork.gallery()["items"]
        with LOCK:
            row=_read().get(k,{"title":title,"rotation_minutes":10})
        ids=_playlist(row,items)
        if not ids:
            ids=["ps5_default"]
        interval=row.get("rotation_minutes",10)*60
        signature=(k,tuple(ids),interval)
        if signature!=self.signature:
            self.signature=signature
            self.started=now
        return ids[int(max(0,now-self.started)//interval)%len(ids)]
