"""Reusable Home Assistant state-driven countdown overlay for the pixel display.

Reads two HA entity states through the Supervisor's authenticated local API.
The template is stored in the add-on's /data, not in Home Assistant config.
"""
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from artwork_modes import get_artwork_rgb, is_artwork_mode, resolve_game_artwork

W,H=52,32
FILE=Path("/data/countdown_binding.json")
FONT={
"A":["010","101","111","101","101"],"B":["110","101","110","101","110"],
"C":["011","100","100","100","011"],"D":["110","101","101","101","110"],
"E":["111","100","110","100","111"],"F":["111","100","110","100","100"],
"G":["011","100","101","101","011"],"H":["101","101","111","101","101"],
"I":["111","010","010","010","111"],"J":["011","001","001","101","010"],
"K":["101","101","110","101","101"],"L":["100","100","100","100","111"],
"M":["101","111","111","101","101"],"N":["101","111","111","111","101"],
"O":["010","101","101","101","010"],"P":["110","101","110","100","100"],
"Q":["010","101","101","111","011"],"R":["110","101","110","101","101"],
"S":["011","100","010","001","110"],"T":["111","010","010","010","010"],
"U":["101","101","101","101","111"],"V":["101","101","101","101","010"],
"W":["101","101","111","111","101"],"X":["101","101","010","101","101"],
"Y":["101","101","010","010","010"],"Z":["111","001","010","100","111"],
"0":["111","101","101","101","111"],"1":["010","110","010","010","111"],
"2":["110","001","111","100","111"],"3":["110","001","011","001","110"],
"4":["101","101","111","001","001"],"5":["111","100","110","001","110"],
"6":["011","100","111","101","111"],"7":["111","001","010","010","010"],
"8":["111","101","111","101","111"],"9":["111","101","111","001","110"],
":":["0","1","0","1","0"]," ":["000"]*5,
}

def validate(cfg):
    if not isinstance(cfg,dict): raise ValueError("configuration must be an object")
    for name in ("state_entity","deadline_entity","power_entity"):
        value=cfg.get(name)
        if name=="power_entity" and value is None: continue
        if not isinstance(value,str) or len(value)>120 or not value.replace("_","").replace(".","").isalnum() or "." not in value:
            raise ValueError("invalid "+name)
    states=cfg.get("active_states",["active"])
    if not isinstance(states,list) or not 1<=len(states)<=5 or not all(isinstance(x,str) and 0<len(x)<30 for x in states):
        raise ValueError("invalid active_states")
    title=cfg.get("title_lines",["GRAND","THEFT","AUTO"])
    if not isinstance(title,list) or not 1<=len(title)<=3 or not all(isinstance(x,str) and 1<=len(x)<=12 and all(k.upper() in FONT for k in x) for x in title):
        raise ValueError("invalid title lines")
    marker=cfg.get("mark","VI")
    if not isinstance(marker,str) or len(marker)>3 or any(k.upper() not in FONT for k in marker):
        raise ValueError("invalid mark")
    mode=cfg.get("artwork_mode")
    if mode is None:
        # Migrate existing GTA countdown bindings to automatic game selection.
        mode="auto" if marker.upper()=="VI" else "classic"
    if mode not in ("auto", "classic") and not is_artwork_mode(mode):
        raise ValueError("unknown artwork_mode")
    game_entity=cfg.get("game_entity", "sensor.justcop_now_playing")
    activity_entity=cfg.get("activity_entity", "sensor.ps5_044_activity")
    for name,value in (("game_entity",game_entity),("activity_entity",activity_entity)):
        if not isinstance(value,str) or len(value)>120 or not value.replace("_","").replace(".","").isalnum() or "." not in value:
            raise ValueError("invalid "+name)
    binding={"state_entity":cfg["state_entity"],"deadline_entity":cfg["deadline_entity"],
             "active_states":states,"title_lines":[x.upper() for x in title],
             "mark":marker.upper(),"artwork_mode":mode,
             "game_entity":game_entity,"activity_entity":activity_entity}
    if cfg.get("power_entity"):
        binding["power_entity"]=cfg["power_entity"]
    return binding

def countdown_label(remaining):
    """Use minutes alone below one hour, retaining rounded-up whole minutes.

    59:59 deliberately displays 60M rather than 1H 00M; hours only appear
    once at least a full hour actually remains.
    """
    remaining=max(0,int(remaining))
    total_minutes=(remaining+59)//60
    if remaining < 3600:
        return f"{total_minutes}M"
    hours,minutes=divmod(total_minutes,60)
    return f"{hours}H {minutes:02d}M"

def selected_artwork_mode(cfg,game_title=None,activity="none"):
    """Resolve a fixed or game-specific display mode from one saved binding."""
    mode=cfg.get("artwork_mode", "auto" if cfg.get("mark")=="VI" else "classic")
    if mode=="auto":
        return resolve_game_artwork(game_title,activity)
    return mode


def render(cfg,remaining,game_title=None,activity="none"):
    out=bytearray(W*H*3)
    def put(x,y,c):
        if 0<=x<W and 0<=y<H:
            k=(y*W+x)*3
            out[k:k+3]=bytes(c)
    def text(s,x,y,color,scale=1):
        for ch in s:
            rows=FONT.get(ch,FONT[" "])
            width=len(rows[0])
            for iy,row in enumerate(rows):
                for ix,cell in enumerate(row):
                    if cell=="1":
                        for dx in range(scale):
                            for dy in range(scale):
                                put(x+ix*scale+dx,y+iy*scale+dy,color)
            x+=(width+1)*scale
    mark=cfg["mark"]
    artwork_mode=selected_artwork_mode(cfg,game_title,activity)
    if artwork_mode!="classic":
        # Persistent, selectable artwork mode. The timer is a separate overlay.
        out[:] = get_artwork_rgb(artwork_mode)
    else:
        # Legacy generic title/mark layout, preserved for other bindings.
        for y in range(H):
            for x in range(W):
                put(x,y,(max(0,10+y//5),4,22+y//3))
        for y in range(2,22):
            for x in range(28,51):
                if (x-39)**2/135+(y-11)**2/75<=1:
                    put(x,y,(129+min(120,y*5),40+y*4,112+max(0,13-y)*4))
        if mark:
            text(mark,32,5,(25,3,40),3)
            text(mark,31,4,(250,129,122),3)
        for idx,line in enumerate(cfg["title_lines"]):
            text(line,3,2+idx*7,(17,5,36),2 if len(line)<=3 else 1)
            text(line,2,1+idx*7,(255,250,237),2 if len(line)<=3 else 1)
        for x in range(2,W-2):
            put(x,20,(224,51,151) if x%3 else (255,147,109))
    remaining=max(0,int(remaining))
    # Display whole hours and minutes only. Round up so a partial minute
    # is never shown as zero while the session is still running.
    label=countdown_label(remaining)
    # Keep long custom sessions within the 52-pixel display as well.
    double_width=sum((len(FONT[c][0])+1)*2 for c in label)-2
    scale=2 if double_width<=W-2 else 1
    total=sum((len(FONT[c][0])+1)*scale for c in label)-scale
    start=(W-total)//2
    if artwork_mode!="classic":
        # Overprint a high-contrast timer on the logo; only digit outlines
        # go dark, rather than obscuring the colours with a black footer bar.
        y0=20 if scale==2 else 23
        lit=set()
        xpos=start
        for ch in label:
            for gy,row in enumerate(FONT[ch]):
                for gx,cell in enumerate(row):
                    if cell=="1":
                        for dy in range(scale):
                            for dx in range(scale):
                                lit.add((xpos+gx*scale+dx,y0+gy*scale+dy))
            xpos+=(len(FONT[ch][0])+1)*scale
        outline=set()
        for x,y in lit:
            for dy in (-1,0,1):
                for dx in (-1,0,1):
                    if 0<=x+dx<W and 0<=y+dy<H:
                        outline.add((x+dx,y+dy))
        for x,y in outline:
            put(x,y,(18,2,39))
        for x,y in lit:
            put(x,y,(255,249,228))
    else:
        text(label,start,22,(255,231,241),scale)
    return bytes(out)

def read_entity(entity):
    token=os.environ.get("SUPERVISOR_TOKEN","")
    if not token:raise RuntimeError("SUPERVISOR_TOKEN unavailable: enable homeassistant_api")
    url="http://supervisor/core/api/states/"+urllib.parse.quote(entity,safe="._")
    req=urllib.request.Request(url,headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=4) as response:
        return json.load(response)

class CountdownBinding:
    def __init__(self,lock,state):
        self.lock,self.state=lock,state
        self.config=None
        self.status={"configured":False,"active":False,"error":None,"seconds_remaining":0}
        if FILE.is_file():
            try:self.config=validate(json.loads(FILE.read_text()))
            except (OSError,ValueError):pass
        self.status["configured"]=self.config is not None
        self.status["binding"]=self.config
        threading.Thread(target=self.worker,daemon=True,name="ha-countdown-watcher").start()

    def configure(self,cfg):
        new=validate(cfg)
        # Atomic replacement to avoid corrupting persistent settings.
        temp=FILE.with_suffix(".tmp")
        temp.write_text(json.dumps(new))
        temp.replace(FILE)
        with self.lock:
            self.config=new
            self.status["configured"]=True
            self.status["error"]=None
            self.status["binding"]=new
        return new

    def worker(self):
        last_check=0
        end_time=0
        visible=False
        last_label=None
        last_mode=None
        last_config=None
        game_title="unknown"
        activity="none"
        resolved_mode=selected_artwork_mode(self.config) if self.config else "ps5_default"
        while True:
            now=time.monotonic()
            with self.lock:cfg=self.config
            if not cfg:
                time.sleep(.5);continue
            if now-last_check>2:
                last_check=now
                try:
                    st=read_entity(cfg["state_entity"])
                    dt=read_entity(cfg["deadline_entity"])
                    # HA input_datetime's timestamp is Unix time (handles UK BST correctly).
                    end_time=float(dt["attributes"]["timestamp"])
                    # The overlay is visible only while the bound device is actually on.
                    # Unknown/unavailable/off all hide it (fail closed).
                    power_on=True
                    if cfg.get("power_entity"):
                        power_on=(read_entity(cfg["power_entity"])["state"]=="on")
                    visible=(st["state"] in cfg["active_states"] and end_time>time.time() and power_on)
                    with self.lock:self.status["error"]=None
                except Exception as exc:
                    visible=False
                    with self.lock:self.status["error"]=str(exc)[:250]
                # Game selection never alters the gaming limits or the
                # countdown's visibility. An unavailable sensor means fallback.
                game_title="unknown"
                activity="none"
                game_error=None
                if cfg["artwork_mode"]=="auto":
                    try:
                        activity=read_entity(cfg["activity_entity"]).get("state","none")
                        game_title=read_entity(cfg["game_entity"]).get("state","unknown")
                    except Exception as exc:
                        game_error=str(exc)[:250]
                resolved_mode=selected_artwork_mode(cfg,game_title,activity)
                with self.lock:
                    self.status.update(game_entity=cfg["game_entity"],
                                       activity_entity=cfg["activity_entity"],
                                       game_title=game_title,ps5_activity=activity,
                                       selected_artwork_mode=resolved_mode,
                                       game_detection_error=game_error)
            remaining=max(0,int(end_time-time.time()+.999))
            if not remaining:visible=False
            with self.lock:
                self.status.update(active=visible,seconds_remaining=remaining if visible else 0)
                owner=self.state.get("owner","idle")
                if visible and owner in ("idle","countdown"):
                    display_label=countdown_label(remaining)
                    if (display_label!=last_label or resolved_mode!=last_mode
                            or owner!="countdown" or cfg is not last_config):
                        self.state.update(frames=(render(cfg,remaining,game_title,activity),),fps=12.,
                                          loop=True,start=time.monotonic(),
                                          expires=time.monotonic()+3,owner="countdown")
                        last_label=display_label
                        last_mode=resolved_mode
                        last_config=cfg
                    else:
                        self.state["expires"]=time.monotonic()+3
                elif not visible and owner=="countdown":
                    self.state.update(frames=(),expires=0.,owner="idle")
                    last_label=None
                    last_mode=None
                    last_config=None
            time.sleep(.15)
