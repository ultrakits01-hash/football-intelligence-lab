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
COMPETITIONS={"PL":"Premier League","PD":"La Liga","BL1":"Bundesliga","SA":"Serie A","FL1":"Ligue 1","CL":"UEFA Champions League","WC":"FIFA World Cup","EC":"UEFA European Championship"}
INTERNATIONAL_CANDIDATES={"WC":"FIFA World Cup","EC":"UEFA European Championship","UNL":"UEFA Nations League","ACN":"Africa Cup of Nations","CA":"Copa América","QCAF":"CAF World Cup Qualification","QAFC":"AFC World Cup Qualification","QCONCACAF":"CONCACAF World Cup Qualification","QSA":"CONMEBOL World Cup Qualification","QEU":"UEFA World Cup Qualification"}

def _key():
    return os.getenv("FOOTBALL_DATA_API_KEY","").strip()

def _get(path, params=None, ttl=TTL):
    token=_key()
    if not token:
        raise HTTPException(503,"FOOTBALL_DATA_API_KEY is not configured")
    qs=urllib.parse.urlencode({k:v for k,v in (params or {}).items() if v not in (None,"")})
    url=BASE+path+("?" + qs if qs else "")
    now=time.time(); hit=CACHE.get(url)
    if hit and now-hit[0] < ttl: return hit[1], True
    req=urllib.request.Request(url,headers={"X-Auth-Token":token,"User-Agent":"Football-Intelligence-Lab/1.7","Accept":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=15) as r: data=json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail=e.read().decode("utf-8","replace")[:500]
        raise HTTPException(e.code,f"football-data.org: {detail}")
    except Exception as e:
        if hit: return hit[1], True
        raise HTTPException(502,f"football-data.org unavailable: {type(e).__name__}")
    CACHE[url]=(now,data); return data, False

def _team(t):
    t=t or {}
    return {"id":t.get("id"),"name":t.get("name"),"short_name":t.get("shortName"),"tla":t.get("tla"),"crest":t.get("crest")}

def _match(m):
    score=m.get("score") or {}; ft=score.get("fullTime") or {}; ht=score.get("halfTime") or {}
    return {"id":m.get("id"),"match_id":m.get("id"),"utc_date":m.get("utcDate"),"date":m.get("utcDate"),"home_name":(m.get("homeTeam") or {}).get("name"),"away_name":(m.get("awayTeam") or {}).get("name"),"status":m.get("status"),"matchday":m.get("matchday"),"stage":m.get("stage"),"group":m.get("group"),
            "home":_team(m.get("homeTeam")),"away":_team(m.get("awayTeam")),"home_score":ft.get("home"),"away_score":ft.get("away"),"ht_home":ht.get("home"),"ht_away":ht.get("away"),
            "winner":score.get("winner"),"duration":score.get("duration"),"last_updated":m.get("lastUpdated")}

@router.get("/health")
def provider_health(): return {"configured":bool(_key()),"provider":"football-data.org","cache_seconds":TTL,"competitions":COMPETITIONS}


def _public_competition_bundle(code,season=None):
    params={"season":int(season)} if season is not None else None
    comp,_=_get(f"/competitions/{code}",params,ttl=21600)
    matches,_=_get(f"/competitions/{code}/matches",params)
    standings,_=_get(f"/competitions/{code}/standings",params)
    scorers,_=_get(f"/competitions/{code}/scorers",params)
    teams,_=_get(f"/competitions/{code}/teams",params)
    return {
        "provider":"football-data.org","competition":comp,
        "matches":[_match(x) for x in matches.get("matches",[])],
        "standings":standings.get("standings",[]),
        "scorers":scorers.get("scorers",[]),
        "teams":teams.get("teams",[]),
        "capabilities":{"matches":True,"standings":True,"teams":True,"scorers":True,"xg":False,"shot_xy":False,"event_xy":False}
    }

@router.get("/international/{code}")
def international_competition(code:str,season:int|None=None):
    code=code.upper()
    allowed={str(x.get("code") or "").upper() for x in (_get("/competitions",ttl=21600)[0].get("competitions",[]))}
    if code not in allowed: raise HTTPException(404,"International competition is not available to this provider account")
    return _public_competition_bundle(code,season)

@router.get("/international/catalogue")
def international_catalogue():
    """Return only international competitions the configured account can actually see."""
    data,cached=_get("/competitions",ttl=21600); rows=[]
    for x in data.get("competitions",[]):
        name=str(x.get("name") or ""); area=(x.get("area") or {}).get("name")
        hay=(name+" "+str(area or "")).lower()
        if not any(k in hay for k in ("world cup","european championship","nations league","africa cup","copa am","qualification")): continue
        rows.append({"id":x.get("id"),"code":x.get("code"),"name":name,"area":area,"emblem":x.get("emblem"),"plan":x.get("plan"),"type":x.get("type"),"current_season":x.get("currentSeason")})
    return {"provider":"football-data.org","cached":cached,"competitions":rows}

@router.get("/competitions")
def competitions():
    data,cached=_get("/competitions",ttl=21600); rows=[]
    for c in data.get("competitions",[]):
        if c.get("code") in COMPETITIONS: rows.append({"id":c.get("id"),"code":c.get("code"),"name":c.get("name"),"emblem":c.get("emblem"),"plan":c.get("plan"),"current_season":c.get("currentSeason")})
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
    data,cached=_get(f"/competitions/{code}/standings",{"season":season}); blocks=[]
    for s in data.get("standings",[]):
        table=[]
        for r in s.get("table",[]): table.append({"position":r.get("position"),"team":_team(r.get("team")),"played":r.get("playedGames"),"won":r.get("won"),"draw":r.get("draw"),"lost":r.get("lost"),"points":r.get("points"),"gf":r.get("goalsFor"),"ga":r.get("goalsAgainst"),"gd":r.get("goalDifference")})
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
    data,cached=_get(f"/competitions/{code}/scorers",{"season":season,"limit":max(1,min(limit,50))}); rows=[]
    for x in data.get("scorers",[]):
        p=x.get("player") or {}; rows.append({"player_id":p.get("id"),"name":p.get("name"),"nationality":p.get("nationality"),"position":p.get("position"),"team":_team(x.get("team")),"goals":x.get("goals"),"assists":x.get("assists"),"penalties":x.get("penalties")})
    return {"provider":"football-data.org","competition":code,"cached":cached,"scorers":rows}

def public_catalogue_rows():
    season_label="2026/27"; rows=[]
    for code,name in COMPETITIONS.items():
        if code=="WC": continue
        rows.append({"dataset_id":f"fd-{code.lower()}-2026","competition_id":code,"season_id":"2026","competition":name,"season":season_label,"label":f"{name} · {season_label}","provider":"football-data.org","installed":True,"virtual":True,"matches":0,"capabilities":{"fixtures":True,"results":True,"standings":True,"teams":True,"scorers":True,"player_advanced":False,"event_xy":False}})
    return rows

def dataset_code(dataset_id):
    s=str(dataset_id or "")
    if s.startswith("fd-") and s.endswith("-2026"): return s[3:-5].upper()
    return None

def league_overview_for(code):
    matches,_=_get(f"/competitions/{code}/matches",{"season":2026}); teams,_=_get(f"/competitions/{code}/teams",{"season":2026},ttl=21600); scorers,_=_get(f"/competitions/{code}/scorers",{"season":2026,"limit":10})
    try: standings,_=_get(f"/competitions/{code}/standings",{"season":2026})
    except HTTPException: standings={}
    ms=[_match(x) for x in matches.get("matches",[])]; played=[x for x in ms if x.get("status")=="FINISHED"]; recent=sorted(played,key=lambda x:str(x.get("utc_date") or ""),reverse=True)[:8]; leaders=[]
    for x in scorers.get("scorers",[]):
        p=x.get("player") or {}; leaders.append({"id":p.get("id"),"name":p.get("name"),"team":(x.get("team") or {}).get("name"),"position":p.get("position"),"goals":x.get("goals"),"assists":x.get("assists")})
    table=[]
    for block in standings.get("standings",[]):
        if block.get("type")=="TOTAL":
            for r in block.get("table",[]): table.append({"position":r.get("position"),"team":_team(r.get("team")),"played":r.get("playedGames"),"won":r.get("won"),"draw":r.get("draw"),"lost":r.get("lost"),"gf":r.get("goalsFor"),"ga":r.get("goalsAgainst"),"gd":r.get("goalDifference"),"points":r.get("points")})
            if table: break
    return {"players":0,"team_count":len(teams.get("teams",[])),"matches":len(ms),"played":len(played),"leaders":{"goals":leaders,"assists":sorted(leaders,key=lambda x:x.get("assists") or 0,reverse=True),"xg":[],"xa":[]},"teams":[{"team":x.get("name"),"name":x.get("name"),"id":x.get("id"),"crest":x.get("crest")} for x in teams.get("teams",[])],"standings":table,"recent":recent,"provider":"football-data.org","public_dataset":True}

def matches_for(code, season=2026):
    data,_=_get(f"/competitions/{code}/matches",{"season":int(season)}); return [_match(x) for x in data.get("matches",[])]

def match_centre_for(code, match_id):
    """Basic public Match Centre. Only fields actually returned by football-data.org are exposed."""
    data,_=_get(f"/matches/{int(match_id)}",ttl=300)
    comp=data.get("competition") or {}
    if code and comp.get("code") and str(comp.get("code")).upper()!=str(code).upper(): raise HTTPException(404,"Match is not in the active FIL competition")
    m=_match(data); home=m.get("home") or {}; away=m.get("away") or {}
    return {"match_id":m.get("match_id"),"match":{"date":m.get("utc_date"),"utc_date":m.get("utc_date"),"status":m.get("status"),"matchday":m.get("matchday"),"stage":m.get("stage"),"group":m.get("group"),"home":home.get("name"),"away":away.get("name"),"home_team":home,"away_team":away,"home_score":m.get("home_score"),"away_score":m.get("away_score"),"ht_home":m.get("ht_home"),"ht_away":m.get("ht_away"),"winner":m.get("winner"),"duration":m.get("duration"),"competition":comp.get("name") or COMPETITIONS.get(code),"competition_code":comp.get("code") or code},"goals":[],"shots":[],"xg_timeline":[],"passing_network":[],"located_events":[],"starting_xi":{},"substitutions":[],"contributions":{},"player_locations":[],"rosters":[],"provider":"football-data.org","public_match_centre":True,"capabilities":{"basic_match":True,"score":True,"half_time_score":m.get("ht_home") is not None,"shot_xy":False,"event_xy":False,"pass_xy":False,"lineups":False,"substitutions":False,"match_player_stats":False},"availability_note":"Basic public Match Centre. Advanced xG, shots, events, lineups and player-match statistics unlock only when a provider supplies them."}
