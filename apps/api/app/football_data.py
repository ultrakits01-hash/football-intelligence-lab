"""football-data.org public provider adapter for FIL.
Uses the server-side FOOTBALL_DATA_API_KEY only; the key is never sent to browsers.
Responses are cached in-process to respect the free-plan request budget.
"""
import json, os, time, urllib.parse, urllib.request, urllib.error
from fastapi import APIRouter, HTTPException

router=APIRouter(prefix="/public-data", tags=["public-data"])
BASE="https://api.football-data.org/v4"
CACHE={}
TTL=int(os.getenv("FOOTBALL_DATA_CACHE_SECONDS","900"))
COMPETITIONS={"PL":"Premier League","PD":"La Liga","BL1":"Bundesliga","SA":"Serie A","FL1":"Ligue 1","CL":"UEFA Champions League","WC":"FIFA World Cup"}

def _key():
    return os.getenv("FOOTBALL_DATA_API_KEY","").strip()

def _get(path, params=None, ttl=TTL):
    token=_key()
    if not token:
        raise HTTPException(503,"FOOTBALL_DATA_API_KEY is not configured")
    qs=urllib.parse.urlencode({k:v for k,v in (params or {}).items() if v not in (None,"")})
    url=BASE+path+("?" + qs if qs else "")
    now=time.time()
    hit=CACHE.get(url)
    if hit and now-hit[0] < ttl:
        return hit[1], True
    req=urllib.request.Request(url,headers={"X-Auth-Token":token,"User-Agent":"Football-Intelligence-Lab/1.7","Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=15) as r:
            data=json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail=e.read().decode("utf-8","replace")[:500]
        raise HTTPException(e.code,f"football-data.org: {detail}")
    except Exception as e:
        if hit:
            return hit[1], True
        raise HTTPException(502,f"football-data.org unavailable: {type(e).__name__}")
    CACHE[url]=(now,data)
    return data, False

def _team(t):
    t=t or {}
    return {"id":t.get("id"),"name":t.get("name"),"short_name":t.get("shortName"),"tla":t.get("tla"),"crest":t.get("crest")}

def _match(m):
    score=m.get("score") or {}; ft=score.get("fullTime") or {}
    return {"id":m.get("id"),"utc_date":m.get("utcDate"),"status":m.get("status"),"matchday":m.get("matchday"),"stage":m.get("stage"),"group":m.get("group"),
            "home":_team(m.get("homeTeam")),"away":_team(m.get("awayTeam")),"home_score":ft.get("home"),"away_score":ft.get("away"),
            "winner":score.get("winner"),"last_updated":m.get("lastUpdated")}

@router.get("/health")
def provider_health():
    return {"configured":bool(_key()),"provider":"football-data.org","cache_seconds":TTL,"competitions":COMPETITIONS}

@router.get("/competitions")
def competitions():
    data,cached=_get("/competitions",ttl=21600)
    rows=[]
    for c in data.get("competitions",[]):
        if c.get("code") in COMPETITIONS:
            rows.append({"id":c.get("id"),"code":c.get("code"),"name":c.get("name"),"emblem":c.get("emblem"),"plan":c.get("plan"),"current_season":c.get("currentSeason")})
    return {"provider":"football-data.org","cached":cached,"competitions":rows}

@router.get("/{code}/matches")
def matches(code:str, season:int|None=None, status:str="", matchday:int|None=None):
    code=code.upper()
    if code not in COMPETITIONS: raise HTTPException(400,"Unsupported FIL competition code")
    data,cached=_get(f"/competitions/{code}/matches",{"season":season,"status":status,"matchday":matchday})
    return {"provider":"football-data.org","competition":code,"cached":cached,"result_set":data.get("resultSet"),"matches":[_match(x) for x in data.get("matches",[])]}

@router.get("/{code}/standings")
def standings(code:str, season:int|None=None):
    code=code.upper()
    if code not in COMPETITIONS: raise HTTPException(400,"Unsupported FIL competition code")
    data,cached=_get(f"/competitions/{code}/standings",{"season":season})
    blocks=[]
    for s in data.get("standings",[]):
        table=[]
        for r in s.get("table",[]):
            table.append({"position":r.get("position"),"team":_team(r.get("team")),"played":r.get("playedGames"),"won":r.get("won"),"draw":r.get("draw"),"lost":r.get("lost"),"points":r.get("points"),"gf":r.get("goalsFor"),"ga":r.get("goalsAgainst"),"gd":r.get("goalDifference")})
        blocks.append({"stage":s.get("stage"),"type":s.get("type"),"group":s.get("group"),"table":table})
    return {"provider":"football-data.org","competition":code,"cached":cached,"standings":blocks}

@router.get("/{code}/teams")
def teams(code:str, season:int|None=None):
    code=code.upper()
    if code not in COMPETITIONS: raise HTTPException(400,"Unsupported FIL competition code")
    data,cached=_get(f"/competitions/{code}/teams",{"season":season},ttl=21600)
    return {"provider":"football-data.org","competition":code,"cached":cached,"teams":[{**_team(x),"venue":x.get("venue"),"founded":x.get("founded")} for x in data.get("teams",[])]}

@router.get("/{code}/scorers")
def scorers(code:str, season:int|None=None, limit:int=20):
    code=code.upper()
    if code not in COMPETITIONS: raise HTTPException(400,"Unsupported FIL competition code")
    data,cached=_get(f"/competitions/{code}/scorers",{"season":season,"limit":max(1,min(limit,50))})
    rows=[]
    for x in data.get("scorers",[]):
        p=x.get("player") or {}
        rows.append({"player_id":p.get("id"),"name":p.get("name"),"nationality":p.get("nationality"),"position":p.get("position"),"team":_team(x.get("team")),"goals":x.get("goals"),"assists":x.get("assists"),"penalties":x.get("penalties")})
    return {"provider":"football-data.org","competition":code,"cached":cached,"scorers":rows}
