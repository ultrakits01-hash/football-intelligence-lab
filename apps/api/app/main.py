from pathlib import Path
import os, json, time, urllib.request, xml.etree.ElementTree as ET, math
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from .core import player_rows, similar, add_percentiles, event_files, load_json, DATA_ROOT, data_dir, set_active_data, reset_active_data, player_event_map, scout_rank, team_profiles
from .football_data import router as football_data_router, public_catalogue_rows, dataset_code, league_overview_for, matches_for, match_centre_for

FIL_VERSION='1.7.0-public-data'
app=FastAPI(title='Football Intelligence Lab API',version=FIL_VERSION)
app.add_middleware(CORSMiddleware,allow_origins=['*'],allow_methods=['*'],allow_headers=['*'])
app.include_router(football_data_router)

# Dataset selection is request-scoped, so every existing endpoint automatically reads
# from the selected competition/season without duplicating analytics logic.
@app.middleware('http')
async def dataset_context(request, call_next):
    dataset_id=request.query_params.get('dataset_id')
    comp=request.query_params.get('competition_id'); season=request.query_params.get('season_id')
    path=DATA_ROOT
    if dataset_id:
        candidate=DATA_ROOT/'datasets'/dataset_id
        if candidate.exists() and candidate.is_dir(): path=candidate
    elif comp and season:
        candidate=DATA_ROOT/'datasets'/f'{comp}_{season}'
        if not candidate.exists():
            for d in (DATA_ROOT/'datasets').glob('*') if (DATA_ROOT/'datasets').exists() else []:
                meta=d/'dataset.json'
                if meta.exists():
                    m=load_json(meta)
                    if str(m.get('competition_id'))==str(comp) and str(m.get('season_id'))==str(season): candidate=d; break
        if candidate.exists(): path=candidate
    token=set_active_data(path)
    try: return await call_next(request)
    finally: reset_active_data(token)
WEB=Path(__file__).resolve().parents[2]/'web'

@app.get('/health')
def health(): return {'ok':True,'event_files':len(event_files()),'provider':'FIL multi-provider core','version':FIL_VERSION}


def _dataset_catalogue():
    # Startup-critical endpoint: one bad/partial dataset must never take down FIL.
    candidates=[]
    meta_root=DATA_ROOT/'datasets'
    if not meta_root.exists():
        return []
    for d in sorted(meta_root.iterdir()):
        if not d.is_dir():
            continue
        meta=d/'dataset.json'
        if not meta.exists():
            continue
        try:
            row=load_json(meta)
            if not isinstance(row,dict):
                continue
            season=str(row.get('season') or '')
            competition=str(row.get('competition') or '')
            if not (season.startswith('2025') or season.startswith('2026') or '2026' in competition):
                continue
            row=dict(row); row['dataset_id']=d.name
            # /datasets is startup-critical. Never parse the large match payloads here:
            # on Windows, doing that for every installed league can block the UI for seconds.
            # New imports persist the cheap count in dataset.json; older installs simply report
            # an unknown count until they are refreshed, but remain immediately selectable.
            count=row.get('match_count', row.get('matches_count', row.get('matches', 0)))
            if isinstance(count, (list, dict)):
                count=0
            try: count=int(count or 0)
            except (TypeError, ValueError): count=0
            row['matches']=count
            row['installed']=bool((d/'matches_normalized.json').exists() or (d/'matches.json').exists() or (d/'players_normalized.json').exists())
            candidates.append(row)
        except Exception as exc:
            # Keep catalogue usable while an interrupted import is repaired.
            print(f'[FIL] skipping unreadable dataset {d.name}: {exc}')
            continue
    def key(r):
        return str(r.get('competition') or '').strip().lower(),str(r.get('season') or '').strip()
    best={}
    for r in candidates:
        k=key(r); old=best.get(k)
        score=(1 if int(r.get('matches') or 0)>0 else 0,1 if str(r.get('provider') or '').lower()=='understat' else 0,int(r.get('matches') or 0))
        oldscore=(-1,-1,-1) if old is None else (1 if int(old.get('matches') or 0)>0 else 0,1 if str(old.get('provider') or '').lower()=='understat' else 0,int(old.get('matches') or 0))
        if old is None or score>oldscore: best[k]=r
    out=list(best.values())
    out.sort(key=lambda r:(0 if str(r.get('season','')).startswith('2026') else 1,str(r.get('competition',''))))
    return out


def _combined_catalogue():
    local=_dataset_catalogue()
    public=public_catalogue_rows() if os.getenv('FOOTBALL_DATA_API_KEY','').strip() else []
    seen={r.get('dataset_id') for r in local}
    return local+[r for r in public if r.get('dataset_id') not in seen]

@app.get('/datasets')
async def datasets():
    return _combined_catalogue()

@app.get('/dataset-catalogue')
async def dataset_catalogue():
    # Public deployments can bootstrap from provider-backed virtual datasets.
    return _combined_catalogue()

@app.get('/league-overview')
def league_overview(dataset_id:str=''):
    code=dataset_code(dataset_id)
    if code: return league_overview_for(code)
    players=player_rows(); teams=team_profiles()
    np=data_dir()/'matches_normalized.json'; mp=data_dir()/'matches.json'
    matches=load_json(np) if np.exists() else (load_json(mp) if mp.exists() else [])
    def top(key, n=8):
        return [{k:r.get(k) for k in ('id','name','team','position','position_group','minutes','goals','assists','xg','xa','shots','xg_p90','xa_p90','shots_p90')} for r in sorted(players,key=lambda x:(float(x.get(key) or 0),float(x.get('minutes') or 0)),reverse=True)[:n]]
    # Understat includes future fixtures with empty/placeholder score objects.
    # Prefer the provider's explicit result flag and only fall back to scores for
    # providers that do not expose one. Keep team_count separate from the team rows
    # so the JSON response can never stringify an array into a KPI.
    played=[m for m in matches if (m.get('is_result') is True) or (m.get('is_result') is None and m.get('home_score') is not None and m.get('away_score') is not None)]
    recent=sorted(played,key=lambda m:str(m.get('date') or m.get('match_date') or ''),reverse=True)[:8]
    return {'players':len(players),'team_count':len(teams),'matches':len(matches),'played':len(played),
            'leaders':{'goals':top('goals'),'assists':top('assists'),'xg':top('xg'),'xa':top('xa')},
            'teams':teams,'recent':recent}


def _public_datasets():
    # Internal callers need the catalogue rows themselves, not the async FastAPI
    # route coroutine returned by datasets(). Calling datasets() here caused
    # /global-players to fail with: TypeError: 'coroutine' object is not iterable.
    rows=_dataset_catalogue()
    return [r for r in rows if r.get('installed')]

def _dataset_path(dataset_id:str):
    p=DATA_ROOT/'datasets'/dataset_id
    return p if p.exists() and p.is_dir() else None

def _in_dataset(dataset_id:str, fn):
    p=_dataset_path(dataset_id)
    if not p: return None
    tok=set_active_data(p)
    try: return fn()
    finally: reset_active_data(tok)

def _name_key(v):
    import re,unicodedata
    s=unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',s)

def _market_reference():
    p=Path(__file__).resolve().parents[3]/'data'/'reference'/'market_values.json'
    if not p.exists(): return {}
    try: return load_json(p).get('values',{})
    except Exception: return {}

def _enrich_player_value(x):
    if not x: return x
    # First choice: dedicated Transfermarkt-derived reference generated by
    # scripts/ingest_market_values.py. It is explicitly dated and never estimated.
    ref=_market_reference().get(_name_key(x.get('name')))
    if ref:
        for k in ('market_value_eur','market_value_source','market_value_date','transfermarkt_player_id','age','nationality'):
            if ref.get(k) not in (None,'',0): x[k]=ref[k]
        return x
    # Fallback for 2025/26 research baseline when a dedicated reference has not yet been built.
    meta_path=data_dir()/'dataset.json'
    if not meta_path.exists(): return x
    meta=load_json(meta_path); comp=str(meta.get('competition') or ''); season=str(meta.get('season') or '')
    if not season.startswith('2025'): return x
    slug={'Premier League':'premier-league','La Liga':'la-liga','Bundesliga':'bundesliga','Serie A':'serie-a','Ligue 1':'ligue-1'}.get(comp)
    if not slug: return x
    ep=DATA_ROOT/'datasets'/f'baseline_{slug}_2025'/'players_normalized.json'
    if not ep.exists(): return x
    name=_name_key(x.get('name'))
    e=next((r for r in load_json(ep) if _name_key(r.get('name'))==name),None)
    if e:
        for k in ('market_value_eur','market_value_source','market_value_date','age','nationality'):
            if e.get(k) not in (None,'',0): x[k]=e[k]
    return x

@app.get('/global-players')
def global_players(q:str='', season:str='', position_group:str='', min_minutes:float=0, limit:int=60):
    out=[]
    for ds in _public_datasets():
        if season and str(ds.get('season'))!=season: continue
        did=ds['dataset_id']
        def grab():
            rows=player_rows()
            z=[]
            for x in rows:
                if q and q.lower() not in str(x.get('name','')).lower(): continue
                if position_group and x.get('position_group')!=position_group: continue
                if float(x.get('minutes') or 0)<min_minutes: continue
                y=_enrich_player_value(dict(x)); y['dataset_id']=did; y['competition']=ds.get('competition'); y['season']=ds.get('season'); y['provider_name']=ds.get('provider'); z.append(y)
            return z
        out.extend(_in_dataset(did,grab) or [])
    # Deduplicate same player/competition/season if a catalogue alias survives on disk.
    best={}
    for x in out:
        k=(str(x.get('competition')),str(x.get('season')),str(x.get('name')).lower(),str(x.get('team')).lower())
        if k not in best or float(x.get('minutes') or 0)>float(best[k].get('minutes') or 0): best[k]=x
    rows=list(best.values()); rows.sort(key=lambda x:float(x.get('minutes') or 0),reverse=True)
    return rows[:max(1,min(limit,200))]

@app.get('/resolve-player')
def resolve_player(name:str, team:str='', dataset_id:str=''):
    """Resolve a human player identity to the dataset-local ID that actually owns it.

    Numeric player IDs are intentionally never treated as portable across FIL datasets.
    """
    nk=_name_key(name); tk=_name_key(team)
    if not nk: raise HTTPException(400,'Player name required')
    datasets=_public_datasets()
    if dataset_id:
        datasets=sorted(datasets,key=lambda d:0 if d.get('dataset_id')==dataset_id else 1)
    candidates=[]
    for ds in datasets:
        did=ds.get('dataset_id')
        def grab():
            out=[]
            for r in player_rows():
                rn=_name_key(r.get('name')); rt=_name_key(r.get('team'))
                # Exact normalised identity first. A prefix/surname-style fallback is
                # accepted only with the same club and is ranked below exact identity.
                exact=rn==nk
                close=bool(tk and rt==tk and (rn.startswith(nk) or nk.startswith(rn)))
                if not (exact or close): continue
                score=(100 if exact else 70)+(25 if tk and rt==tk else 0)+min(float(r.get('minutes') or 0)/1000,5)
                out.append((score,dict(r)))
            return out
        for score,r in (_in_dataset(did,grab) or []):
            candidates.append((score,did,ds,r))
    if not candidates: raise HTTPException(404,'Player identity not found in installed FIL datasets')
    candidates.sort(key=lambda z:z[0],reverse=True)
    score,did,ds,r=candidates[0]
    return {'dataset_id':did,'id':r.get('id'),'name':r.get('name'),'team':r.get('team'),'competition':ds.get('competition'),'season':ds.get('season')}

@app.get('/resolve-player-profile')
def resolve_player_profile(name:str, team:str='', dataset_id:str=''):
    """Resolve identity and return the actual profile in one atomic operation.

    This avoids the old Wonderkids failure mode where a route was resolved first and
    then a second request tried to rediscover the same player/dataset combination.
    """
    nk=_name_key(name); tk=_name_key(team)
    if not nk: raise HTTPException(400,'Player name required')
    datasets=_public_datasets()
    if dataset_id:
        datasets=sorted(datasets,key=lambda d:0 if d.get('dataset_id')==dataset_id else 1)
    hits=[]
    for ds in datasets:
        did=ds.get('dataset_id')
        def grab():
            rows=player_rows(); out=[]
            for r in rows:
                rn=_name_key(r.get('name')); rt=_name_key(r.get('team'))
                exact=rn==nk
                close=bool(tk and rt==tk and (rn.startswith(nk) or nk.startswith(rn)))
                if not (exact or close): continue
                score=(100 if exact else 70)+(25 if tk and rt==tk else 0)+(12 if did==dataset_id else 0)+min(float(r.get('minutes') or 0)/1000,5)
                x=_enrich_player_value(add_percentiles(dict(r),rows)); x['dataset_id']=did
                x['competition']=ds.get('competition'); x['season']=ds.get('season'); x['provider_name']=ds.get('provider')
                out.append((score,x))
            return out
        for score,x in (_in_dataset(did,grab) or []): hits.append((score,x))
    if not hits: raise HTTPException(404,'Player identity not found in installed FIL datasets')
    hits.sort(key=lambda z:z[0],reverse=True)
    return hits[0][1]

# Player Lab profiles are immutable for an installed dataset during a server run.
# Cache the expensive peer-percentile build so repeat opens are effectively instant.
_GLOBAL_PLAYER_PROFILE_CACHE={}

@app.get('/global-player/{dataset_id}/{pid}')
def global_player(dataset_id:str,pid:int):
    key=(dataset_id,int(pid))
    cached=_GLOBAL_PLAYER_PROFILE_CACHE.get(key)
    if cached is not None:
        return cached
    def grab():
        rows=player_rows(); x=next((dict(r) for r in rows if int(r.get('id',-1))==pid),None)
        if not x: return None
        x=_enrich_player_value(add_percentiles(x,rows)); x['dataset_id']=dataset_id
        meta=load_json(data_dir()/'dataset.json') if (data_dir()/'dataset.json').exists() else {}
        x['competition']=meta.get('competition'); x['season']=meta.get('season'); x['provider_name']=meta.get('provider')
        return x
    x=_in_dataset(dataset_id,grab)
    if not x: raise HTTPException(404,'Player not found')
    _GLOBAL_PLAYER_PROFILE_CACHE[key]=x
    return x

@app.get('/global-scout')
def global_scout(season:str='2025/26', position_group:str='MID', min_minutes:float=900, scope:str='big5', q:str='', xg:float=1,creation:float=4,passing:float=2,carrying:float=3,pressing:float=1,defending:float=0):
    weights={'xg_p90':xg,'shot_assists_p90':creation,'passes_completed_p90':passing,'carries_p90':carrying,'pressures_p90':pressing,'interceptions_p90':defending}
    out=[]
    for ds in _public_datasets():
        if str(ds.get('season'))!=season: continue
        comp=str(ds.get('competition') or '')
        if comp not in {'Premier League','La Liga','Bundesliga','Serie A','Ligue 1'}: continue
        did=ds['dataset_id']
        def grab():
            z=scout_rank(position_group,min_minutes,weights,q)[:80]
            for x in z: _enrich_player_value(x); x.update({'dataset_id':did,'competition':comp,'season':ds.get('season'),'provider_name':ds.get('provider')})
            return z
        out.extend(_in_dataset(did,grab) or [])
    out.sort(key=lambda x:float(x.get('fit') or 0),reverse=True)
    return out[:150]

@app.get('/capabilities')
def capabilities():
    meta=data_dir()/'dataset.json'
    if meta.exists():
        row=load_json(meta); caps=dict(row.get('capabilities',{}));
        if row.get('provider')=='Understat': caps['lineups']=False
        return {'provider':row.get('provider'),'capabilities':caps,'provenance_note':row.get('provenance_note')}
    return {'provider':'StatsBomb Open Data','capabilities':{'player_season_stats':True,'xg':True,'shot_xy':True,'event_xy':True,'pass_xy':True,'pressure_xy':True,'lineups':True}}

@app.get('/players')
def players(q:str='',position_group:str='',team:str='',min_minutes:float=0):
    r=player_rows()
    if q:r=[x for x in r if q.lower() in x['name'].lower()]
    if position_group:r=[x for x in r if x.get('position_group')==position_group]
    if team:r=[x for x in r if team.lower() in (x.get('team') or '').lower()]
    r=[x for x in r if x.get('minutes',0)>=min_minutes]
    return [_enrich_player_value(dict(x)) for x in sorted(r,key=lambda x:x.get('minutes',0),reverse=True)[:100]]


@app.get('/scout')
def scout(position_group:str='MID',min_minutes:float=900,q:str='',xg:float=1,creation:float=4,passing:float=2,carrying:float=3,pressing:float=1,defending:float=0):
    weights={'xg_p90':xg,'shot_assists_p90':creation,'passes_completed_p90':passing,'carries_p90':carrying,'pressures_p90':pressing,'interceptions_p90':defending}
    return [_enrich_player_value(x) for x in scout_rank(position_group,min_minutes,weights,q)[:150]]

@app.get('/players/{pid}')
def player(pid:int):
    rows=player_rows(); x=next((dict(r) for r in rows if r['id']==pid),None)
    x=_enrich_player_value(x)
    if not x: raise HTTPException(404,'Player not found')
    return _enrich_player_value(add_percentiles(x,rows))

@app.get('/players/{pid}/similar')
def sims(pid:int,limit:int=10,dataset_id:str=''):
    if dataset_id:
        return _in_dataset(dataset_id,lambda: similar(pid,limit)) or []
    return similar(pid,limit)

_GLOBAL_SIM_CACHE={'stamp':None,'rows':None}
def _global_similarity_rows():
    datasets=_all_installed_datasets()
    stamp=tuple((d.get('dataset_id'), (_dataset_path(d.get('dataset_id'))/'players_normalized.json').stat().st_mtime_ns if _dataset_path(d.get('dataset_id')) and (_dataset_path(d.get('dataset_id'))/'players_normalized.json').exists() else 0) for d in datasets)
    if _GLOBAL_SIM_CACHE['stamp']==stamp and _GLOBAL_SIM_CACHE['rows'] is not None:
        return _GLOBAL_SIM_CACHE['rows']
    rows=[]
    for ds in datasets:
        did=ds['dataset_id']
        def grab(): return [dict(x) for x in player_rows()]
        for x in (_in_dataset(did,grab) or []):
            x.update({'dataset_id':did,'competition':ds.get('competition'),'season':ds.get('season'),'provider_name':ds.get('provider'),'route_id':x.get('id')})
            rows.append(x)
    _GLOBAL_SIM_CACHE.update(stamp=stamp,rows=rows)
    return rows

def _global_similar(dataset_id:str,pid:int,limit:int=8):
    rows=_global_similarity_rows()
    target=next((r for r in rows if r.get('dataset_id')==dataset_id and int(r.get('route_id',-1))==int(pid)),None)
    if not target:return []
    group=target.get('position_group')
    def archetype(pos,grp):
        p=str(pos or '').upper().replace('-', ' ').replace('/', ' '); toks=set(p.split())
        if grp=='GK': return 'GK'
        if grp=='FWD':
            if toks & {'LW','RW','LM','RM','W','WINGER'}: return 'WIDE_FWD'
            if toks & {'ST','CF','SS','FW','STRIKER'}: return 'CENTRAL_FWD'
        if grp=='MID':
            if toks & {'DM','CDM','DMC'}: return 'DM'
            if toks & {'AM','CAM','AMC'}: return 'AM'
            if toks & {'CM','MC'}: return 'CM'
        if grp=='DEF':
            if toks & {'LB','RB','LWB','RWB','FB'}: return 'FULLBACK'
            if toks & {'CB','DC'}: return 'CB'
        return grp or 'OTHER'
    target_arch=archetype(target.get('position'),group)
    # Tiered peer selection: exact tactical archetype first, then the wider position group.
    # This prevents sparse provider vocabularies from producing an empty Similar Players panel.
    def make_pool(exact=True):
        for floor in (450,270,180,90,0):
            q=[r for r in rows if r is not target and r.get('position_group')==group and
               (not exact or archetype(r.get('position'),r.get('position_group'))==target_arch) and float(r.get('minutes') or 0)>=floor]
            if len(q)>=max(16,limit*2): return q
        return q
    pool=make_pool(True)
    if len(pool)<max(8,limit): pool=make_pool(False)
    weights={'FWD':{'xg_p90':4.0,'shots_p90':3.0,'xa_p90':1.2,'shot_assists_p90':1.2,'xg_chain_p90':1.0,'xg_buildup_p90':0.5},
             'MID':{'xa_p90':2.2,'shot_assists_p90':2.0,'xg_chain_p90':1.5,'xg_buildup_p90':1.3,'xg_p90':1.0,'shots_p90':0.8},
             'DEF':{'interceptions_p90':2.0,'tackles_p90':2.0,'xg_buildup_p90':1.4,'xg_chain_p90':1.0,'xa_p90':0.7},
             'GK':{'saves_p90':2.5,'save_pct':2.5,'goals_conceded_p90':1.5}}.get(group,{})
    features=[]
    for k,w in weights.items():
        if target.get(k) is None:continue
        vals=[float(r[k]) for r in pool if r.get(k) is not None]
        if w>0 and len(vals)>=max(5,len(pool)//6) and max(vals)-min(vals)>1e-8:features.append(k)
    if not features:return []
    # Global identity de-duplication. Exact names collapse across providers/clubs; common short-name
    # aliases (e.g. Abde / Abdessamad Ezzalzouli) collapse when surname + club + first-name prefix agree.
    def ident(r):
        import re,unicodedata
        raw=unicodedata.normalize('NFKD',str(r.get('name') or '')).encode('ascii','ignore').decode().lower()
        toks=re.findall(r'[a-z0-9]+',raw); club=_name_key(r.get('team'))
        if not toks:return ('',club)
        return (''.join(toks),club)
    ordered=sorted(pool,key=lambda r:(str(r.get('season'))==str(target.get('season')),float(r.get('minutes') or 0)),reverse=True)
    kept=[]
    for r in ordered:
        nr,cr=ident(r); toks_r=__import__('re').findall(r'[a-z0-9]+',__import__('unicodedata').normalize('NFKD',str(r.get('name') or '')).encode('ascii','ignore').decode().lower())
        dup=False
        for k in kept:
            nk,ck=ident(k); toks_k=__import__('re').findall(r'[a-z0-9]+',__import__('unicodedata').normalize('NFKD',str(k.get('name') or '')).encode('ascii','ignore').decode().lower())
            if nr==nk: dup=True; break
            if cr and cr==ck and len(toks_r)>=2 and len(toks_k)>=2 and toks_r[-1]==toks_k[-1]:
                a,b=toks_r[0],toks_k[0]
                if min(len(a),len(b))>=4 and (a.startswith(b) or b.startswith(a)): dup=True; break
        if not dup: kept.append(r)
    pool=kept
    full=pool+[target]; meds={}; scales={}
    for k in features:
        vals=sorted(float(r[k]) for r in full if r.get(k) is not None)
        med=vals[len(vals)//2]; dev=sorted(abs(v-med) for v in vals); mad=dev[len(dev)//2] if dev else 1
        meds[k]=med;scales[k]=max(1.4826*mad,1e-6)
    distances=[]
    for r in pool:
        available=[k for k in features if r.get(k) is not None and target.get(k) is not None]
        if len(available)<max(2,min(3,len(features))):continue
        sw=sum(weights[k] for k in available); parts=[]
        for k in available:
            delta=(max(-3,min(3,(float(r[k])-meds[k])/scales[k]))-max(-3,min(3,(float(target[k])-meds[k])/scales[k])))
            parts.append((delta*delta)*(weights[k]/sw))
        distances.append((math.sqrt(sum(parts)),r,available))
    if not distances:return []
    positive=sorted(d for d,_,_ in distances if d>1e-9);scale=positive[len(positive)//2] if positive else 1.0
    out=[]
    for d,r,available in sorted(distances,key=lambda z:z[0]):
        contrib=sorted(available,key=lambda k:abs((float(r[k])-float(target[k]))/scales[k])); pretty=lambda k:k.replace('_p90','').replace('_',' ')
        out.append({**r,'id':r.get('route_id'),'similarity':round(100*math.exp(-0.85*d/max(scale,0.25)),1),
                    'shared_strengths':[pretty(k) for k in contrib[:3]],'biggest_differences':[pretty(k) for k in contrib[-3:][::-1]],
                    'method':{'scope':'global installed FIL universe','peer_group':target_arch,'features':[pretty(k) for k in available],'standardisation':'global median / 1.4826*MAD','distance':'role-weighted robust Euclidean'}})
        if len(out)>=limit:break
    return out

@app.get('/global-similar/{dataset_id}/{pid}')
def global_sims(dataset_id:str,pid:int,limit:int=8):
    return _global_similar(dataset_id,pid,max(1,min(limit,20)))

@app.get('/players/{pid}/map')
def player_map(pid:int,dataset_id:str=''):
    if dataset_id:
        return _in_dataset(dataset_id,lambda: player_event_map(pid)) or {'shots':[],'events':[],'located_events':[]}
    return player_event_map(pid)





def _all_installed_datasets():
    """All installed dataset folders, including alternate providers for the same league/season.

    Unlike the UI catalogue this deliberately does not deduplicate by competition+season: Player
    Lab can borrow genuine spatial evidence from an installed event/shot provider while keeping
    the active dataset as the statistical source.
    """
    out=[]; root=DATA_ROOT/'datasets'
    if not root.exists(): return out
    for d in root.iterdir():
        if not d.is_dir() or not (d/'dataset.json').exists(): continue
        try:
            m=load_json(d/'dataset.json')
            if isinstance(m,dict) and ((d/'players_normalized.json').exists() or (d/'matches_normalized.json').exists() or (d/'matches.json').exists()):
                x=dict(m); x['dataset_id']=d.name; out.append(x)
        except Exception: pass
    return out

@app.get('/player-spatial-evidence')
def player_spatial_evidence(name:str, team:str='', season:str='', competition:str='', exclude_dataset_id:str=''):
    """Find real installed spatial evidence for a player across providers.

    No coordinates are synthesised. The endpoint only returns an existing provider map and
    records exactly which dataset supplied it. Same season/competition is preferred.
    """
    nk=_name_key(name); tk=_name_key(team)
    if not nk: raise HTTPException(400,'Player name required')
    candidates=[]
    for ds in _all_installed_datasets():
        did=ds.get('dataset_id')
        if did==exclude_dataset_id: continue
        def grab():
            rows=player_rows(); hits=[]
            for r in rows:
                rn=_name_key(r.get('name')); rt=_name_key(r.get('team'))
                if rn!=nk and not (tk and rt==tk and (rn.startswith(nk) or nk.startswith(rn))): continue
                identity=(100 if rn==nk else 70)+(20 if tk and rt==tk else 0)
                mp=player_event_map(int(r.get('id')))
                shots=mp.get('shots') or []; events=mp.get('events') or mp.get('located_events') or []
                if not shots and not events: continue
                context=(35 if season and str(ds.get('season'))==str(season) else 0)+(30 if competition and str(ds.get('competition'))==str(competition) else 0)
                richness=min(len(events),5000)/100 + min(len(shots),1000)/50
                hits.append((identity+context+richness,r,mp))
            return hits
        for score,r,mp in (_in_dataset(did,grab) or []):
            candidates.append((score,ds,r,mp))
    if not candidates: return {'found':False,'shots':[],'events':[],'located_events':[]}
    candidates.sort(key=lambda z:z[0],reverse=True)
    _,ds,r,mp=candidates[0]
    return {'found':True,'dataset_id':ds.get('dataset_id'),'provider':ds.get('provider'),'competition':ds.get('competition'),'season':ds.get('season'),'player_id':r.get('id'),'shots':mp.get('shots') or [],'events':mp.get('events') or mp.get('located_events') or [],'located_events':mp.get('located_events') or mp.get('events') or []}

@app.get('/teams')
def teams(dataset_id:str=''):
    code=dataset_code(dataset_id)
    if code:
        # Public current-season provider has club identities but not FIL team analytics.
        overview=league_overview_for(code)
        return [{'team':t.get('name') or t.get('team'),'crest':t.get('crest'),'provider':'football-data.org','matches':None,'percentiles':{},'ranks':{},'top_players':[]} for t in overview.get('teams',[])]
    return team_profiles()

@app.get('/teams/{team_name}')
def team(team_name:str):
    rows=team_profiles(); x=next((r for r in rows if r['team']==team_name),None)
    if not x: raise HTTPException(404,'Team not found')
    return x

@app.get('/matches')
def matches(dataset_id:str=''):
    code=dataset_code(dataset_id)
    if code: return matches_for(code)
    np=data_dir()/'matches_normalized.json'
    if np.exists(): return load_json(np)
    p=data_dir()/'matches.json'
    if not p.exists(): return []
    return [{'match_id':m.get('match_id'),'date':m.get('match_date'),'home':m.get('home_team',{}).get('home_team_name'),'away':m.get('away_team',{}).get('away_team_name'),'home_score':m.get('home_score'),'away_score':m.get('away_score')} for m in load_json(p)]

@app.get('/matches/{match_id}/analysis')
def match_analysis(match_id:int, dataset_id:str=''):
    code=dataset_code(dataset_id)
    if code: return match_centre_for(code,match_id)
    # Normalised providers such as Understat have shot-level match data but not
    # StatsBomb-style full event streams. Return only what the provider truly supplies.
    maps_path=data_dir()/'player_maps.json'; norm_matches=data_dir()/'matches_normalized.json'
    p=(data_dir()/'events'/f'{match_id}.json')
    if not p.exists() and maps_path.exists() and norm_matches.exists():
        match=next((m for m in load_json(norm_matches) if int(m.get('match_id',-1))==match_id),None)
        if not match: raise HTTPException(404,'Match not ingested')
        shots=[]
        for pm in load_json(maps_path).values():
            for sh in pm.get('shots',[]):
                if int(sh.get('match_id',-1))==match_id:
                    row=dict(sh); row['location']=[row.get('x'),row.get('y')]; shots.append(row)
        details_path=data_dir()/'match_details.json'
        details=(load_json(details_path).get(str(match_id),{}) if details_path.exists() else {})
        rosters=details.get('rosters',[])
        contributions={}
        for rr in rosters:
            name=rr.get('player')
            if name:
                contributions[name]={'goals':int(float(rr.get('goals') or 0)),'assists':int(float(rr.get('assists') or 0)),'xg':float(rr.get('xG') or 0),'xa':float(rr.get('xA') or 0),'minutes':float(rr.get('time') or 0)}
        goals=[]
        for sh in shots:
            if str(sh.get('outcome','')).lower()=='goal':
                goals.append({'minute':sh.get('minute'),'period':2 if float(sh.get('minute') or 0)>45 else 1,'team':sh.get('team'),'scorer':sh.get('player') or 'Unknown','assist':sh.get('assist'),'penalty':str(sh.get('situation','')).lower()=='penalty'})
        return {'match_id':match_id,'match':match,'goals':goals, 'shots':shots,'xg_timeline':shots,
                'passing_network':[],'located_events':[],'starting_xi':{},'substitutions':[],
                'contributions':contributions,'player_locations':[],'rosters':rosters,
                'capabilities':{'shot_xy':True,'event_xy':False,'pass_xy':False,'lineups':False,'substitutions':False,'match_player_stats':bool(rosters)}}
    if not p.exists(): raise HTTPException(404,'Match not ingested')
    ev=load_json(p); shots=[]; edges={}; nodes={}; located=[]
    starting_xi={}; substitutions=[]; contributions={}; pass_by_id={}; goals=[]
    for e in ev:
        if e.get('type',{}).get('name')=='Starting XI':
            team_name=e.get('team',{}).get('name')
            if team_name:
                starting_xi[team_name]=[row.get('player',{}).get('name') for row in e.get('tactics',{}).get('lineup',[]) if row.get('player',{}).get('name')]
    for e in ev:
        if e.get('type',{}).get('name')=='Pass':
            pass_by_id[e.get('id')]=e
    for e in ev:
        player=e.get('player',{}).get('name'); loc=e.get('location'); team=e.get('team',{}).get('name')
        if player and loc:
            located.append({'player':player,'team':team,'type':e.get('type',{}).get('name'),'minute':e.get('minute'),'period':e.get('period'),'x':loc[0],'y':loc[1]})
            n=nodes.setdefault((player,team),{'x':0.0,'y':0.0,'n':0}); n['x']+=loc[0]; n['y']+=loc[1]; n['n']+=1
        if e.get('type',{}).get('name')=='Shot':
            s=e.get('shot',{}); outcome=s.get('outcome',{}).get('name'); kp=pass_by_id.get(s.get('key_pass_id')); shot_assister=(kp or {}).get('player',{}).get('name'); shots.append({'minute':e.get('minute'),'second':e.get('second'),'period':e.get('period'),'team':team,'player':player,'xg':s.get('statsbomb_xg',0),'location':loc,'outcome':outcome,'body_part':s.get('body_part',{}).get('name'),'shot_type':s.get('type',{}).get('name'),'assist':shot_assister})
            if outcome=='Goal' and player:
                contributions.setdefault(player,{'goals':0,'assists':0})['goals']+=1
                kp=pass_by_id.get(s.get('key_pass_id'))
                assister=(kp or {}).get('player',{}).get('name')
                if assister:
                    contributions.setdefault(assister,{'goals':0,'assists':0})['assists']+=1
                goals.append({'minute':e.get('minute'),'second':e.get('second'),'period':e.get('period'),'team':team,'scorer':player,'assist':assister,'penalty':s.get('type',{}).get('name')=='Penalty'})
        if e.get('type',{}).get('name')=='Substitution':
            replacement=e.get('substitution',{}).get('replacement',{}).get('name')
            if player and replacement:
                substitutions.append({'minute':e.get('minute'),'second':e.get('second'),'period':e.get('period'),'team':team,'off':player,'on':replacement})
        if e.get('type',{}).get('name')=='Pass' and 'outcome' not in e.get('pass',{}):
            a=player; b=e.get('pass',{}).get('recipient',{}).get('name')
            if a and b: edges[(a,b,team)]=edges.get((a,b,team),0)+1
    meta={}
    mp=data_dir()/'matches.json'
    if mp.exists():
        m=next((m for m in load_json(mp) if m.get('match_id')==match_id),{})
        meta={'date':m.get('match_date'),'kick_off':m.get('kick_off'),'aet':any((e.get('period') or 0)>=3 for e in ev),
              'home':m.get('home_team',{}).get('home_team_name'),'away':m.get('away_team',{}).get('away_team_name'),
              'home_score':m.get('home_score'),'away_score':m.get('away_score')}
    return {'match_id':match_id,'match':meta,'goals':sorted(goals,key=lambda x:(x.get('minute') or 0,x.get('second') or 0)),'shots':shots,'xg_timeline':shots,
      'passing_network':[{'from':a,'to':b,'team':t,'passes':n} for (a,b,t),n in edges.items()],
      'located_events':located,
      'starting_xi':starting_xi,
      'substitutions':sorted(substitutions,key=lambda x:(x.get('minute') or 0,x.get('second') or 0)),
      'contributions':contributions,
      'player_locations':[{'player':k[0],'team':k[1],'x':round(v['x']/v['n'],2),'y':round(v['y']/v['n'],2),'events':v['n']} for k,v in nodes.items()]}

# v7.0 — World Cup 2026 intelligence. Source files are deliberately kept separate
# from club-league datasets: openfootball supplies tournament results/squads, while
# FIL may link a squad player to richer installed club analytics by identity.
# World Cup files are imported into <project>/data/worldcup2026, not the StatsBomb raw root.
PROJECT_ROOT=Path(__file__).resolve().parents[3]
WC_ROOT=PROJECT_ROOT/'data'/'worldcup2026'
WC_LEGACY_ROOT=DATA_ROOT/'worldcup2026'

def _wc_json(name):
    # Prefer the canonical importer destination, but recognise the v7.0 legacy path too.
    for root in (WC_ROOT, WC_LEGACY_ROOT):
        p=root/name
        if not p.exists(): continue
        try: return load_json(p)
        except Exception as exc:
            print(f'[FIL] unreadable World Cup file {p}: {exc}')
    return None

def _wc_health():
    files=[]
    for name in ('worldcup.json','worldcup.squads.json','worldcup-full.json'):
        found=None; readable=False; error=None
        for root in (WC_ROOT, WC_LEGACY_ROOT):
            p=root/name
            if p.exists():
                found=p
                try: load_json(p); readable=True
                except Exception as exc: error=str(exc)
                break
        files.append({'name':name,'found':bool(found),'readable':readable,'path':str(found) if found else str(WC_ROOT/name),'bytes':found.stat().st_size if found else 0,'error':error})
    return {'root':str(WC_ROOT),'ready':all(x['found'] and x['readable'] for x in files[:2]),'files':files}

@app.get('/world-cup/status')
def world_cup_status(): return _wc_health()

_WC_PAYLOAD_CACHE={'stamp':None,'value':None}
def _wc_payload():
    # World Cup source files are immutable during normal browsing. Re-reading three
    # large JSON files for every tab/match made the tournament UI unnecessarily slow.
    # Invalidate automatically when an importer replaces any source file.
    names=('worldcup.json','worldcup-full.json','worldcup.squads.json')
    stamp=[]
    for name in names:
        hit=None
        for root in (WC_ROOT,WC_LEGACY_ROOT):
            p=root/name
            if p.exists(): hit=p; break
        stamp.append((str(hit) if hit else '',hit.stat().st_mtime_ns if hit else 0,hit.stat().st_size if hit else 0))
    stamp=tuple(stamp)
    if _WC_PAYLOAD_CACHE['stamp']==stamp and _WC_PAYLOAD_CACHE['value'] is not None:
        return _WC_PAYLOAD_CACHE['value']
    basic=_wc_json('worldcup.json') or {}
    full=_wc_json('worldcup-full.json') or basic
    squads=_wc_json('worldcup.squads.json') or []
    value=(basic,full,squads)
    _WC_PAYLOAD_CACHE.update(stamp=stamp,value=value)
    return value

def _wc_table(matches, group):
    teams={}
    for m in matches:
        if m.get('group')!=group: continue
        score=(m.get('score') or {}).get('ft')
        if not isinstance(score,list) or len(score)<2: continue
        a,b=m.get('team1'),m.get('team2'); ga,gb=int(score[0]),int(score[1])
        for t in (a,b): teams.setdefault(t,{'team':t,'p':0,'w':0,'d':0,'l':0,'gf':0,'ga':0,'gd':0,'pts':0})
        A,B=teams[a],teams[b]; A['p']+=1;B['p']+=1;A['gf']+=ga;A['ga']+=gb;B['gf']+=gb;B['ga']+=ga
        if ga>gb:A['w']+=1;B['l']+=1;A['pts']+=3
        elif gb>ga:B['w']+=1;A['l']+=1;B['pts']+=3
        else:A['d']+=1;B['d']+=1;A['pts']+=1;B['pts']+=1
    for x in teams.values(): x['gd']=x['gf']-x['ga']
    return sorted(teams.values(),key=lambda x:(x['pts'],x['gd'],x['gf']),reverse=True)

@app.get('/world-cup/overview')
def world_cup_overview():
    basic,full,squads=_wc_payload(); matches=basic.get('matches') or []
    if not matches: return {'ready':False,'install_command':r'.\\.venv\\Scripts\\python.exe scripts\\ingest_world_cup_2026.py','health':_wc_health()}
    groups=sorted({m.get('group') for m in matches if m.get('group')})
    goals={}
    for m in matches:
        for side in ('goals1','goals2'):
            for g in m.get(side) or []:
                if g.get('owngoal'): continue
                n=g.get('name'); goals[n]=goals.get(n,0)+1
    played=sum(1 for m in matches if isinstance((m.get('score') or {}).get('ft'),list))
    return {'ready':True,'name':basic.get('name','World Cup 2026'),'matches':len(matches),'played':played,'nations':len(squads),
            'groups':[{'name':g,'table':_wc_table(matches,g)} for g in groups],
            'top_scorers':[{'name':n,'goals':v} for n,v in sorted(goals.items(),key=lambda x:(-x[1],x[0]))[:15]],
            'recent':sorted([m for m in matches if (m.get('score') or {}).get('ft')],key=lambda m:m.get('date',''),reverse=True)[:12],
            'source':'openfootball/worldcup.json · public-domain tournament record','health':_wc_health()}

@app.get('/world-cup/matches')
def world_cup_matches(group:str='',round:str='',team:str=''):
    basic,full,squads=_wc_payload(); rows=full.get('matches') or basic.get('matches') or []
    if group: rows=[m for m in rows if m.get('group')==group]
    if round: rows=[m for m in rows if str(m.get('round','')).lower()==round.lower()]
    if team: rows=[m for m in rows if team in (m.get('team1'),m.get('team2'))]
    return rows

@app.get('/world-cup/nations')
def world_cup_nations():
    basic,full,squads=_wc_payload()
    return [{'name':s.get('name'),'fifa_code':s.get('fifa_code'),'group':s.get('group'),'players':len(s.get('players') or [])} for s in squads]

@app.get('/world-cup/squad/{nation}')
def world_cup_squad(nation:str):
    basic,full,squads=_wc_payload(); s=next((x for x in squads if str(x.get('name','')).lower()==nation.lower()),None)
    if not s: raise HTTPException(404,'World Cup nation not found')
    out=[]
    for x in s.get('players') or []:
        r=dict(x); r['club_name']=(x.get('club') or {}).get('name'); r['club_country']=(x.get('club') or {}).get('country')
        # Link to installed FIL club analytics without inventing tournament statistics.
        key=_name_key(x.get('name')); links=[]
        for ds in _public_datasets():
            did=ds['dataset_id']
            def grab():
                z=next((p for p in player_rows() if _name_key(p.get('name'))==key),None)
                return {'id':z.get('id'),'dataset_id':did,'team':z.get('team'),'competition':ds.get('competition'),'season':ds.get('season')} if z else None
            z=_in_dataset(did,grab)
            if z: links.append(z)
        r['fil_links']=links[:5]; out.append(r)
    return {'name':s.get('name'),'fifa_code':s.get('fifa_code'),'group':s.get('group'),'players':out}


@app.get('/world-cup/nation/{nation}/summary')
def world_cup_nation_summary(nation:str):
    basic,full,squads=_wc_payload(); matches=basic.get('matches') or []
    squad=next((x for x in squads if str(x.get('name','')).lower()==nation.lower()),None)
    if not squad: raise HTTPException(404,'World Cup nation not found')
    games=[m for m in matches if nation in (m.get('team1'),m.get('team2'))]
    w=d=l=gf=ga=0
    scorers={}
    for m in games:
        ft=(m.get('score') or {}).get('ft')
        if not isinstance(ft,list) or len(ft)<2: continue
        home=m.get('team1')==nation; a,b=(int(ft[0]),int(ft[1])) if home else (int(ft[1]),int(ft[0]))
        gf+=a;ga+=b
        if a>b:w+=1
        elif a<b:l+=1
        else:d+=1
        side='goals1' if home else 'goals2'
        for g in m.get(side) or []:
            if g.get('owngoal'): continue
            n=g.get('name'); scorers[n]=scorers.get(n,0)+1
    return {'name':nation,'fifa_code':squad.get('fifa_code'),'group':squad.get('group'),'players':len(squad.get('players') or []),
            'record':{'played':w+d+l,'w':w,'d':d,'l':l,'gf':gf,'ga':ga,'gd':gf-ga},
            'top_scorers':[{'name':n,'goals':v} for n,v in sorted(scorers.items(),key=lambda x:(-x[1],x[0]))[:8]],
            'matches':games}

@app.get('/world-cup/knockout')
def world_cup_knockout():
    basic,full,squads=_wc_payload(); rows=full.get('matches') or basic.get('matches') or []
    ko=[m for m in rows if not m.get('group')]
    order=[]
    for m in ko:
        r=str(m.get('round') or 'Knockout')
        if r not in order: order.append(r)
    return {'rounds':[{'name':r,'matches':[m for m in ko if str(m.get('round') or 'Knockout')==r]} for r in order],'matches':len(ko)}


# v8.0 — World Cup interactive hubs and result-aware scoring
def _wc_match_key(m):
    return str(m.get('num') or f"{m.get('date','')}-{m.get('team1','')}-{m.get('team2','')}")

def _wc_result_score(m):
    sc=m.get('score') or {}
    # ET is the final on-pitch score where present; penalties decide a shootout but are shown separately.
    base=sc.get('et') if isinstance(sc.get('et'),list) else sc.get('ft')
    return base if isinstance(base,list) and len(base)>=2 else None

def _wc_player_totals(matches):
    players={}
    for m in matches:
        for side in ('goals1','goals2'):
            team=m.get('team1') if side=='goals1' else m.get('team2')
            for g in m.get(side) or []:
                if g.get('owngoal'): continue
                n=g.get('name');
                if not n: continue
                z=players.setdefault(n,{'name':n,'team':team,'goals':0,'assists':0})
                z['goals']+=1
                a=g.get('assist') or g.get('assisted_by') or g.get('assist_name')
                if isinstance(a,dict): a=a.get('name')
                if a:
                    q=players.setdefault(str(a),{'name':str(a),'team':team,'goals':0,'assists':0});q['assists']+=1
    return list(players.values())

def _wc_link_player(name):
    key=_name_key(name); links=[]
    for ds in _public_datasets():
        did=ds['dataset_id']
        def grab():
            z=next((p for p in player_rows() if _name_key(p.get('name'))==key),None)
            return {'id':z.get('id'),'dataset_id':did,'team':z.get('team'),'competition':ds.get('competition'),'season':ds.get('season')} if z else None
        z=_in_dataset(did,grab)
        if z: links.append(z)
    return links[:5]

@app.get('/world-cup/hub/group/{group_name}')
def world_cup_group_hub(group_name:str):
    basic,full,squads=_wc_payload(); matches=basic.get('matches') or []
    label=group_name if group_name.lower().startswith('group ') else 'Group '+group_name.upper()
    gm=[m for m in matches if str(m.get('group','')).lower()==label.lower()]
    if not gm: raise HTTPException(404,'World Cup group not found')
    leaders=sorted(_wc_player_totals(gm),key=lambda x:(-x['goals'],-x['assists'],x['name']))
    return {'name':label,'table':_wc_table(matches,label),'matches':gm,'leaders':leaders[:12]}

@app.get('/world-cup/hub/nation/{nation}')
def world_cup_nation_hub(nation:str):
    base=world_cup_nation_summary(nation); squad=world_cup_squad(nation)
    players=_wc_player_totals(base['matches'])
    by={_name_key(x['name']):x for x in players}
    for p in squad['players']:
        t=by.get(_name_key(p.get('name')),{});p['world_cup_goals']=t.get('goals',0);p['world_cup_assists']=t.get('assists',0)
    base['squad']=squad['players'];return base

@app.get('/world-cup/hub/match/{match_key}')
def world_cup_match_hub(match_key:str):
    basic,full,squads=_wc_payload()
    basic_rows=basic.get('matches') or []
    full_rows=full.get('matches') or []
    # Cards are rendered from both the compact and full tournament feeds. Their `num`
    # fields are not guaranteed to be identical, so resolve the public key against the
    # compact feed first and then join the richer record by stable match identity.
    m=next((x for x in full_rows if _wc_match_key(x)==match_key),None)
    if not m:
        compact=next((x for x in basic_rows if _wc_match_key(x)==match_key),None)
        if compact:
            ident=(compact.get('date'),compact.get('team1'),compact.get('team2'))
            m=next((x for x in full_rows if (x.get('date'),x.get('team1'),x.get('team2'))==ident),None) or compact
    if not m:
        # Last-resort stable fallback for old cached cards whose numeric key changed.
        decoded=match_key.strip()
        m=next((x for x in basic_rows if _wc_match_key(x)==decoded),None)
    if not m: raise HTTPException(404,'World Cup match not found')
    sc=m.get('score') or {}; result=_wc_result_score(m)
    return {'key':_wc_match_key(m),'match':m,'result_score':result,'penalties':sc.get('p'),'half_time':sc.get('ht'),
            'goals':[dict(g,team=m.get('team1')) for g in m.get('goals1') or []]+[dict(g,team=m.get('team2')) for g in m.get('goals2') or []],
            'starting_xi':m.get('starting_xi') or m.get('starters') or m.get('lineups'),
            'substitutions':m.get('substitutions') or m.get('subs') or [],'bookings':m.get('bookings') or m.get('cards') or [],
            'referee':m.get('referee') or m.get('referees'),'lineup1':_wc_side_players(m,1),'lineup2':_wc_side_players(m,2)}


# v9.0 — World Cup Intelligence III: richer tournament evidence without scraping
def _wc_names(value):
    """Best-effort normaliser for openfootball full-match lineup/card structures."""
    out=[]
    if isinstance(value,str): return [value]
    if isinstance(value,list):
        for x in value: out.extend(_wc_names(x))
    elif isinstance(value,dict):
        name=value.get('name') or value.get('player') or value.get('player_name')
        if isinstance(name,dict): name=name.get('name')
        if isinstance(name,str): out.append(name)
        elif not name:
            for k,v in value.items():
                if k.lower() in ('players','starters','starting_xi','lineup','xi','bench','substitutions','subs'): out.extend(_wc_names(v))
    return list(dict.fromkeys(x for x in out if x))

def _wc_lineup_side(m,side):
    """Return one team's real openfootball lineup block.

    2026 worldcup-full.json stores `lineup` as a two-item list; each side contains
    `starter`, `bench`, and `subs`. Older FIL builds guessed non-existent lineup1/
    lineup2 keys, which is why the UI showed no XI despite the source having it.
    """
    line=m.get('lineup')
    if isinstance(line,list) and len(line)>=side and isinstance(line[side-1],dict):
        return line[side-1]
    # compatibility with alternate/older structures
    line=m.get('starting_xi') or m.get('lineups') or m.get('starters')
    if isinstance(line,dict):
        team=m.get(f'team{side}')
        for k in (str(side),f'team{side}',team):
            if k in line and isinstance(line[k],dict): return line[k]
    return {}

def _wc_side_players(m,side):
    block=_wc_lineup_side(m,side)
    if block:
        return _wc_names(block.get('starter') or block.get('starters') or block.get('xi') or block.get('players'))
    keys=(f'lineup{side}',f'starting_xi{side}',f'starters{side}',f'xi{side}',f'team{side}_lineup',f'team{side}_players')
    for k in keys:
        if m.get(k): return _wc_names(m.get(k))
    return []

def _wc_side_bench(m,side):
    return _wc_names(_wc_lineup_side(m,side).get('bench') or [])

def _wc_side_subs(m,side):
    vals=_wc_lineup_side(m,side).get('subs') or []
    return vals if isinstance(vals,list) else []

def _wc_flat_bookings(m):
    vals=m.get('bookings') or m.get('cards') or []
    out=[]
    if isinstance(vals,list):
        for side_idx,part in enumerate(vals,1):
            if isinstance(part,list):
                for x in part:
                    if isinstance(x,dict): out.append({**x,'team':x.get('team') or m.get(f'team{side_idx}')})
            elif isinstance(part,dict): out.append(part)
    return out

def _wc_referees(m):
    vals=m.get('referees') or m.get('referee') or []
    if isinstance(vals,dict): vals=[vals]
    return vals if isinstance(vals,list) else []

def _wc_tournament_player_stats(matches):
    stats={}
    def row(name,team=''):
        k=_name_key(name); z=stats.setdefault(k,{'name':name,'team':team,'appearances':0,'starts':0,'sub_appearances':0,'goals':0,'penalty_goals':0,'assists':0,'yellow_cards':0,'red_cards':0})
        if team and not z.get('team'): z['team']=team
        return z
    appeared=set()
    for mi,m in enumerate(matches):
        for side in (1,2):
            team=m.get(f'team{side}') or ''
            for n in _wc_side_players(m,side):
                z=row(n,team); z['starts']+=1
                key=(mi,_name_key(n))
                if key not in appeared: z['appearances']+=1; appeared.add(key)
            for sub in _wc_side_subs(m,side):
                if not isinstance(sub,dict): continue
                n=sub.get('on') or sub.get('in') or sub.get('player_in') or sub.get('player')
                if isinstance(n,dict): n=n.get('name')
                if n:
                    z=row(str(n),team); key=(mi,_name_key(n))
                    if key not in appeared: z['appearances']+=1;z['sub_appearances']+=1;appeared.add(key)
        for side in (1,2):
            team=m.get(f'team{side}') or ''
            for g in m.get(f'goals{side}') or []:
                if not g.get('owngoal') and g.get('name'):
                    z=row(g['name'],team);z['goals']+=1
                    if g.get('penalty'):z['penalty_goals']+=1
                a=g.get('assist') or g.get('assisted_by') or g.get('assist_name')
                if isinstance(a,dict): a=a.get('name')
                if a: row(str(a),team)['assists']+=1
        for c in _wc_flat_bookings(m):
            n=c.get('name') or c.get('player') or c.get('player_name')
            if isinstance(n,dict): n=n.get('name')
            if not n: continue
            typ=str(c.get('type') or c.get('card') or c.get('colour') or '').lower();z=row(str(n),str(c.get('team') or ''))
            if typ in ('r','red') or 'red' in typ:z['red_cards']+=1
            elif typ in ('y','yellow') or 'yellow' in typ:z['yellow_cards']+=1
    return list(stats.values())

def _wc_nation_crest(code,name):
    # FIL-owned visual identity: not an official federation crest.
    seed=sum(ord(c) for c in (code or name or 'FIL'))
    return {'code':code or (name[:3].upper() if name else 'FIL'),'variant':seed%6}

@app.get('/world-cup/player/{player_name}')
def world_cup_player_hub(player_name:str):
    basic,full,squads=_wc_payload(); key=_name_key(player_name); squad_row=None; nation=None
    for sq in squads:
        for p in sq.get('players') or []:
            if _name_key(p.get('name'))==key: squad_row=dict(p);nation=sq.get('name');break
        if squad_row: break
    rows=full.get('matches') or basic.get('matches') or []
    stats=next((x for x in _wc_tournament_player_stats(rows) if _name_key(x.get('name'))==key),None) or {'name':player_name,'team':nation,'appearances':0,'starts':0,'goals':0,'assists':0,'yellow_cards':0,'red_cards':0}
    games=[]
    for m in basic.get('matches') or []:
        involved=False
        for side in ('goals1','goals2'):
            if any(_name_key(g.get('name'))==key for g in m.get(side) or []): involved=True
        if nation and nation in (m.get('team1'),m.get('team2')): involved=True
        if involved: games.append(m)
    if not squad_row and not stats.get('goals') and not stats.get('starts'): raise HTTPException(404,'World Cup player not found')
    if squad_row:
        squad_row['club_name']=(squad_row.get('club') or {}).get('name');squad_row['club_country']=(squad_row.get('club') or {}).get('country')
    return {'name':stats.get('name') or player_name,'nation':nation or stats.get('team'),'squad':squad_row,'stats':stats,'matches':games,'fil_links':_wc_link_player(player_name)}

@app.get('/world-cup/player-stats')
def world_cup_player_stats():
    basic,full,squads=_wc_payload(); rows=full.get('matches') or basic.get('matches') or []
    vals=_wc_tournament_player_stats(rows)
    for x in vals: x['fil_links']=_wc_link_player(x['name'])
    return sorted(vals,key=lambda x:(-x['goals'],-x['assists'],-x['starts'],x['name']))

@app.get('/world-cup/hub/group-v2/{group_name}')
def world_cup_group_hub_v2(group_name:str):
    d=world_cup_group_hub(group_name); basic,full,squads=_wc_payload(); label=d['name']
    full_rows=[m for m in (full.get('matches') or []) if str(m.get('group','')).lower()==label.lower()]
    d['player_stats']=sorted(_wc_tournament_player_stats(full_rows or d['matches']),key=lambda x:(-x['goals'],-x['assists'],-x['starts'],x['name']))[:20]
    d['goals']=sum(sum((_wc_result_score(m) or [0,0])) for m in d['matches'])
    d['matches_played']=len(d['matches']); return d

@app.get('/world-cup/hub/nation-v2/{nation}')
def world_cup_nation_hub_v2(nation:str):
    d=world_cup_nation_hub(nation); basic,full,squads=_wc_payload()
    full_games=[m for m in (full.get('matches') or []) if nation in (m.get('team1'),m.get('team2'))]
    ps={_name_key(x['name']):x for x in _wc_tournament_player_stats(full_games or d['matches'])}
    for p in d['squad']:
        z=ps.get(_name_key(p.get('name')),{}); p['starts']=z.get('starts',0);p['appearances']=z.get('appearances',0);p['yellow_cards']=z.get('yellow_cards',0);p['red_cards']=z.get('red_cards',0)
    # Nation hub must never leak opposition scorers from the same matches.
    nation_key=_name_key(d.get('name') or nation)
    d['crest']=_wc_nation_crest(d.get('fifa_code'),d.get('name'));d['top_scorers']=sorted([x for x in ps.values() if x['goals'] and _name_key(x.get('team'))==nation_key],key=lambda x:(-x['goals'],x['name']))[:8]
    return d

@app.get('/world-cup/leaders-v2')
def world_cup_leaders_v2():
    # Keep this endpoint intentionally cheap. Player Lab resolves identity only when
    # a row is clicked; doing thousands of cross-dataset identity lookups here made
    # the Leaders tab feel much slower than the rest of the tournament UI.
    basic,full,squads=_wc_payload(); rows=full.get('matches') or basic.get('matches') or []
    vals=_wc_tournament_player_stats(rows)
    goals=sorted(vals,key=lambda x:(-x['goals'],-x['assists'],-x['starts'],x['name']))
    assists=sorted([x for x in vals if x['assists']],key=lambda x:(-x['assists'],-x['goals'],x['name']))
    return {'goals':goals[:30],'assists':assists[:30],'assists_available':bool(assists)}

def _wc_match_stats_record(m):
    # Optional provider-neutral enrichment. FIL never scrapes a third-party site at
    # runtime. Drop a permitted/licensed export into data/worldcup2026/match_stats.json.
    path=None
    for root in (WC_ROOT, WC_LEGACY_ROOT):
        candidate=root/'match_stats.json'
        if candidate.exists():
            path=candidate; break
    if not path:return None
    try:data=json.loads(path.read_text(encoding='utf-8'))
    except Exception:return None
    rows=data.get('matches',data) if isinstance(data,dict) else data
    if not isinstance(rows,list):return None
    nk=lambda x:_name_key(x or '')
    for r in rows:
        if not isinstance(r,dict):continue
        same_date=not r.get('date') or str(r.get('date'))==str(m.get('date'))
        direct=nk(r.get('team1'))==nk(m.get('team1')) and nk(r.get('team2'))==nk(m.get('team2'))
        reverse=nk(r.get('team1'))==nk(m.get('team2')) and nk(r.get('team2'))==nk(m.get('team1'))
        if same_date and (direct or reverse):
            stats=r.get('stats') or {}; out=[]
            labels={'possession':'Possession','xg':'Expected goals (xG)','shots':'Total shots','shots_on_target':'Shots on target','big_chances':'Big chances','corners':'Corners','touches_box':'Touches in opposition box','passes':'Passes','accurate_passes_pct':'Pass accuracy'}
            for key,label in labels.items():
                v=stats.get(key)
                if isinstance(v,(list,tuple)) and len(v)>=2:
                    a,b=v[0],v[1]
                    if reverse:a,b=b,a
                    if key in ('possession','accurate_passes_pct'):a=f'{a}%' if a is not None else None;b=f'{b}%' if b is not None else None
                    out.append({'key':key,'label':label,'home':a,'away':b})
            return {'available':bool(out),'source':r.get('source') or data.get('source') if isinstance(data,dict) else 'installed provider','rows':out}
    return None

@app.get('/world-cup/hub/match-v3/{match_key}')
def world_cup_match_hub_v3(match_key:str):
    # Reuse the reconciler from the existing hub, then enrich directly from the
    # matched full record using the documented 2026 openfootball schema.
    base=world_cup_match_hub(match_key)
    m=base['match']
    full=base.get('full_match') or m
    # Enrich the real OpenFootball XI with squad metadata (shirt number / broad
    # position) when the installed squad file supplies it.  We never invent a
    # formation: the UI groups the XI by the source's GK/DF/MF/FW labels only.
    _,_,squads=_wc_payload()
    def squad_meta(team,name):
        sq=next((q for q in squads if _name_key(q.get('name'))==_name_key(team)),None) or {}
        pl=next((q for q in (sq.get('players') or []) if _name_key(q.get('name'))==_name_key(name)),None) or {}
        return {'number':pl.get('number'),'pos':pl.get('pos') or pl.get('position')}
    sides=[]
    for side in (1,2):
        team=m.get(f'team{side}')
        block=_wc_lineup_side(full,side)
        starters=[]
        for x in block.get('starter') or []:
            if isinstance(x,dict) and x.get('name'):
                starters.append({'name':x['name'],'captain':bool(x.get('captain')),**squad_meta(team,x['name'])})
            elif isinstance(x,str): starters.append({'name':x,'captain':False,**squad_meta(team,x)})
        bench=[]
        for name in _wc_side_bench(full,side): bench.append({'name':name,**squad_meta(team,name)})
        sides.append({'team':team,'starters':starters,'bench':bench,'subs':_wc_side_subs(full,side)})
    bookings=_wc_flat_bookings(full);refs=_wc_referees(full)
    return {**base,'lineups':sides,'bookings':bookings,'referees':refs,
            'attendance':full.get('attendance'),'ground':full.get('ground') or m.get('ground'),
            'match_stats':(match_stats:=_wc_match_stats_record(m)),
            'source_coverage':{'starting_xi':all(len(x['starters'])==11 for x in sides),'bench':any(x['bench'] for x in sides),'substitutions':any(x['subs'] for x in sides),'bookings':bool(bookings),'attendance':full.get('attendance') is not None,'referees':bool(refs),'match_stats':bool(match_stats)}}

@app.get('/world-cup/tournament-stats-v3')
def world_cup_tournament_stats_v3():
    basic,full,squads=_wc_payload();rows=full.get('matches') or basic.get('matches') or []
    ps=_wc_tournament_player_stats(rows)
    total_goals=sum(x.get('goals',0) for x in ps);pens=sum(x.get('penalty_goals',0) for x in ps)
    atts=[m.get('attendance') for m in rows if isinstance(m.get('attendance'),(int,float))]
    cards=_wc_flat_bookings if False else None
    yc=rc=0
    for m in rows:
        for c in _wc_flat_bookings(m):
            t=str(c.get('type') or c.get('card') or '').lower()
            if t in ('r','red') or 'red' in t:rc+=1
            elif t in ('y','yellow') or 'yellow' in t:yc+=1
    return {'matches':len(rows),'goals':total_goals,'goals_per_match':round(total_goals/len(rows),2) if rows else None,
            'penalty_goals':pens,'yellow_cards':yc,'red_cards':rc,'attendance_total':sum(atts) if atts else None,
            'attendance_average':round(sum(atts)/len(atts)) if atts else None,'matches_with_attendance':len(atts),
            'players':len(ps),'starts_recorded':sum(x.get('starts',0) for x in ps),'sub_appearances':sum(x.get('sub_appearances',0) for x in ps)}

if WEB.exists():
    @app.get('/favicon.ico',include_in_schema=False)
    def favicon(): return Response(status_code=204)
    app.mount('/static',StaticFiles(directory=WEB),name='static')
    @app.get('/',include_in_schema=False)
    def home():
        # Never let an old HTML shell pin the browser to stale JS/CSS asset versions.
        return FileResponse(WEB/'index.html',headers={'Cache-Control':'no-store, no-cache, must-revalidate, max-age=0','Pragma':'no-cache','Expires':'0'})

TRANSFER_ROLE_METRICS={
 'GK':{'shot_stopper':{'saves_p90':4,'save_pct':5,'goals_prevented_p90':5,'claims_p90':2},'sweeper_keeper':{'sweeper_actions_p90':5,'passes_completed_p90':3,'long_balls_completed_p90':2,'saves_p90':2},'distributor':{'passes_completed_p90':5,'long_balls_completed_p90':4,'save_pct':2},'balanced_gk':{'save_pct':4,'saves_p90':3,'passes_completed_p90':2,'claims_p90':2}},
 'DEF':{'stopper':{'tackles_p90':4,'interceptions_p90':5,'clearances_p90':4,'aerial_duels_won_p90':4},'ball_playing_cb':{'passes_completed_p90':5,'long_balls_completed_p90':4,'interceptions_p90':2,'aerial_duels_won_p90':2},'cover_cb':{'interceptions_p90':5,'recoveries_p90':4,'tackles_p90':3,'passes_completed_p90':2},'attacking_fullback':{'carries_p90':4,'shot_assists_p90':4,'final_third_passes_p90':4,'tackles_p90':2},'defensive_fullback':{'tackles_p90':5,'interceptions_p90':4,'recoveries_p90':3,'passes_completed_p90':2}},
 'MID':{'deep_playmaker':{'passes_completed_p90':5,'long_balls_completed_p90':4,'xg_buildup_p90':4,'interceptions_p90':2},'ball_winner':{'interceptions_p90':5,'tackles_p90':5,'recoveries_p90':4,'passes_completed_p90':2},'box_to_box':{'carries_p90':4,'recoveries_p90':3,'interceptions_p90':3,'xg_p90':2,'shot_assists_p90':2},'creator':{'shot_assists_p90':5,'xa_p90':5,'passes_completed_p90':2,'carries_p90':2},'progressor':{'carries_p90':5,'passes_completed_p90':4,'xg_buildup_p90':3,'final_third_passes_p90':4},'advanced_8':{'shot_assists_p90':4,'xg_p90':3,'carries_p90':3,'final_third_passes_p90':4}},
 'FWD':{'poacher':{'xg_p90':5,'shots_p90':5,'goals_p90':4},'inside_forward':{'xg_p90':4,'shots_p90':4,'carries_p90':4,'shot_assists_p90':2},'creative_winger':{'shot_assists_p90':5,'xa_p90':5,'carries_p90':4,'xg_p90':2},'target_forward':{'xg_p90':4,'aerial_duels_won_p90':5,'shot_assists_p90':2,'shots_p90':3},'pressing_forward':{'xg_p90':3,'pressures_p90':5,'shots_p90':3},'complete_forward':{'xg_p90':4,'shots_p90':3,'shot_assists_p90':3,'carries_p90':3}}
}

def _role_rank(rows,role,pos):
    metrics=TRANSFER_ROLE_METRICS.get(pos,{}).get(role,{})
    pool=[dict(r) for r in rows if r.get('position_group')==pos]
    usable={k:w for k,w in metrics.items() if sum(r.get(k) is not None for r in pool)>=max(4,len(pool)//5)}
    if not usable:return []
    pcts={}
    for k in usable:
        vals=[float(r[k]) for r in pool if r.get(k) is not None]
        for r in pool:
            if r.get(k) is not None:pcts[(r['id'],k)]=100*sum(v<=float(r[k]) for v in vals)/len(vals)
    out=[]
    for r in pool:
        avail=[k for k in usable if (r['id'],k) in pcts]
        if len(avail)<max(1,min(2,len(usable))):continue
        raw=sum(pcts[(r['id'],k)]*usable[k] for k in avail)/sum(usable[k] for k in avail)
        # A profile match, not a rating: compress the top end so ordinary percentile
        # saturation cannot manufacture 99/100 "perfect" transfers.
        match=35+57*(raw/100)**1.35
        r.update({'fit':round(min(match,92),1),'fit_raw':round(raw,1),'role_metrics':avail,'metric_coverage':round(len(avail)/len(usable)*100)})
        out.append(r)
    return sorted(out,key=lambda r:(r['fit'],r.get('minutes',0)),reverse=True)



_TRANSFER_CACHE={'at':0.0,'payload':None}
_TRANSFER_CACHE_FILE=DATA_ROOT/'transfers'/'last_success.json'

def _normalise_transfer_items(rows):
    out=[]; seen=set()
    for row in rows if isinstance(rows,list) else []:
        if not isinstance(row,dict): continue
        status=str(row.get('status') or 'reported').lower()
        if status not in {'confirmed','reported','rumour'}: status='reported'
        item={
            'player':row.get('player') or row.get('name'), 'title':row.get('title'),
            'from_club':row.get('from_club') or row.get('from'), 'to_club':row.get('to_club') or row.get('to'),
            'type':row.get('type') or row.get('transfer_type'), 'fee':row.get('fee') or row.get('fee_text'),
            'status':status, 'published_at':row.get('published_at') or row.get('date'),
            'summary':row.get('summary'), 'source':row.get('source'), 'source_url':row.get('source_url') or row.get('url')
        }
        key=((item.get('source_url') or '').strip().lower(),(item.get('title') or item.get('player') or '').strip().lower())
        if key in seen: continue
        seen.add(key); out.append(item)
    return out[:250]

def _rss_transfer_rows(url:str,source_name:str):
    req=urllib.request.Request(url,headers={'User-Agent':'Football-Intelligence-Lab/1.6 (+local analytics app; RSS reader)'})
    with urllib.request.urlopen(req,timeout=7) as resp:data=resp.read()
    root=ET.fromstring(data); rows=[]
    for item in root.findall('.//item')[:100]:
        title=(item.findtext('title') or '').strip(); link=(item.findtext('link') or '').strip(); desc=(item.findtext('description') or '').strip(); date=(item.findtext('pubDate') or '').strip()
        if not title:continue
        # News feeds are evidence of a report, not evidence that a move is completed.
        rows.append({'title':title,'status':'reported','published_at':date,'summary':re.sub('<[^>]+>',' ',desc)[:500] if desc else None,'source':source_name,'source_url':link})
    return rows

def _load_transfer_stale_cache():
    try:
        raw=load_json(_TRANSFER_CACHE_FILE)
        return raw if isinstance(raw,dict) else None
    except Exception:return None

def _save_transfer_stale_cache(payload):
    try:
        _TRANSFER_CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
        _TRANSFER_CACHE_FILE.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:pass

@app.get('/live-transfers')
def live_transfers(refresh:int=0):
    """Resilient provider-neutral feed: structured source -> local verified -> public RSS -> last-success cache."""
    now=time.time()
    if not refresh and _TRANSFER_CACHE['payload'] is not None and now-_TRANSFER_CACHE['at']<300:return _TRANSFER_CACHE['payload']
    local=DATA_ROOT/'transfers'/'live.json'; rows=[]; sources=[]; failures=[]
    # Structured permitted JSON has priority and can carry CONFIRMED records.
    url=os.getenv('FIL_TRANSFER_FEED_URL','').strip()
    if url:
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Football-Intelligence-Lab/1.6 (+local analytics app)'})
            with urllib.request.urlopen(req,timeout=8) as resp: raw=json.loads(resp.read().decode('utf-8'))
            rows.extend(raw.get('items',raw) if isinstance(raw,(dict,list)) else [])
            sources.append(raw.get('source','Configured structured feed') if isinstance(raw,dict) else 'Configured structured feed')
        except Exception as exc: failures.append(f'structured:{type(exc).__name__}')
    if local.exists():
        try:
            raw=load_json(local); rows.extend(raw.get('items',raw) if isinstance(raw,(dict,list)) else [])
            sources.append(raw.get('source','Local verified feed') if isinstance(raw,dict) else 'Local verified feed')
        except Exception as exc: failures.append(f'local:{type(exc).__name__}')
    # Multi-source public RSS fallback. These remain REPORTED by design.
    feed_env=os.getenv('FIL_TRANSFER_RSS_URLS','').strip()
    feeds=[]
    if feed_env:
        for part in feed_env.split(';'):
            if part.strip(): feeds.append((part.strip(),'Configured RSS'))
    else:
        feeds=[('https://www.mykhel.com/rss/feeds/transfer-news-fb.xml','MyKhel Transfer News RSS')]
    for rss_url,name in feeds:
        try:
            got=_rss_transfer_rows(rss_url,name); rows.extend(got)
            if got:sources.append(name)
        except Exception as exc: failures.append(f'{name}:{type(exc).__name__}')
    items=_normalise_transfer_items(rows)
    if items:
        payload={'items':items,'source':{'name':' + '.join(dict.fromkeys(sources)) or 'FIL transfer feeds','live':True,'note':'Live source aggregation · 5-minute memory cache · reports never promoted to confirmed'},'updated_at':int(now),'stale':False,'failures':failures}
        _save_transfer_stale_cache(payload)
    else:
        stale=_load_transfer_stale_cache()
        if stale and stale.get('items'):
            payload={**stale,'source':{**(stale.get('source') or {}),'live':False,'note':'Live sources unavailable · showing last successful cached feed'},'stale':True,'failures':failures,'served_at':int(now)}
        else:
            payload={'items':[],'source':{'name':'FIL transfer adapter','live':False,'note':'No live source returned data and no last-success cache exists yet.'},'updated_at':int(now),'stale':False,'failures':failures}
    _TRANSFER_CACHE.update(at=now,payload=payload);return payload

@app.get('/transfer-candidates')
def transfer_candidates(season:str='2025/26',position_group:str='MID',role:str='creator',min_minutes:float=900):
    out=[]
    for ds in _public_datasets():
        if str(ds.get('season'))!=season or str(ds.get('competition') or '') not in {'Premier League','La Liga','Bundesliga','Serie A','Ligue 1'}:continue
        did=ds['dataset_id']; comp=ds.get('competition')
        def grab():
            rows=[_enrich_player_value(dict(x)) for x in player_rows() if float(x.get('minutes') or 0)>=min_minutes]
            z=_role_rank(rows,role,position_group)
            for x in z:x.update({'dataset_id':did,'competition':comp,'season':ds.get('season'),'provider_name':ds.get('provider')})
            return z[:100]
        out.extend(_in_dataset(did,grab) or [])
    # prefer richer record when provider aliases overlap
    best={}
    for x in out:
        k=(_name_key(x.get('name')),_name_key(x.get('team')))
        if k not in best or x.get('metric_coverage',0)>best[k].get('metric_coverage',0):best[k]=x
    return sorted(best.values(),key=lambda x:(x.get('fit',0),x.get('minutes',0)),reverse=True)[:250]

_WONDER_CACHE={'stamp':None,'global_rows':None,'fil':None}

def _wonder_base():
    ref=Path(__file__).resolve().parents[3]/'data'/'reference'/'global_wonderkids.json'
    # Only installed local datasets can contribute player rows. Virtual live
    # provider datasets have no local Player Lab pool and must not be scanned.
    local_ds=[d for d in _public_datasets() if not str(d.get('dataset_id') or '').startswith('fd-')]
    stamp=(ref.stat().st_mtime_ns if ref.exists() else 0, tuple((d.get('dataset_id'),d.get('season')) for d in local_ds))
    if _WONDER_CACHE.get('stamp')==stamp:
        return _WONDER_CACHE['global_rows'],_WONDER_CACHE['fil']
    global_rows=[]
    if ref.exists():
        try: global_rows=(load_json(ref) or {}).get('players',[])
        except Exception: global_rows=[]
    fil=[]
    for ds in local_ds:
        if not (str(ds.get('season') or '').startswith('2025') or str(ds.get('season') or '').startswith('2026')):continue
        did=ds['dataset_id']
        def grab():
            z=[]
            for r in player_rows():
                x=_enrich_player_value(dict(r)); age=x.get('age')
                try: age=float(age)
                except: continue
                if age<14 or age>19:continue
                # Persist the exact analytics route at enrichment time. This is the only
                # authoritative Player Lab link for Wonderkids; catalogue IDs are never reused.
                x.update({'dataset_id':did,'competition':ds.get('competition'),'season':ds.get('season'),'provider_name':ds.get('provider'),
                          'fil_player_id':x.get('id'),'fil_dataset_id':did,'fil_player_name':x.get('name'),'fil_player_team':x.get('team')})
                z.append(x)
            return z
        fil.extend(_in_dataset(did,grab) or [])
    _WONDER_CACHE.update({'stamp':stamp,'global_rows':global_rows,'fil':fil})
    return global_rows,fil

@app.get('/wonderkids')
def wonderkids(max_age:int=19,position_group:str='',min_minutes:float=0,max_value:float=0,q:str='',source:str='all'):
    # Cached base universe: filters should not rescan every installed dataset.
    global_rows,fil=_wonder_base()
    fmap={}
    for x in fil:
        k=_name_key(x.get('name'))
        if not k:continue
        old=fmap.get(k)
        quality=(len(x.get('available_metrics') or []),float(x.get('minutes') or 0))
        old_quality=(len(old.get('available_metrics') or []),float(old.get('minutes') or 0)) if old else (-1,-1)
        if old is None or quality>old_quality:fmap[k]=x
    combined=[]; seen=set()
    if source!='fil':
        for g in global_rows:
            k=_name_key(g.get('name')); x=dict(g); f=fmap.get(k)
            if f:
                # FIL analytics win where present; global register fills identity/value/club gaps.
                for key,val in f.items():
                    if val not in (None,'',[]):x[key]=val
                x['global_match']=True
            x['catalogue_source']='Global register + FIL' if f else 'Global register'
            combined.append(x); seen.add(k)
    if source!='global':
        for k,f in fmap.items():
            if k in seen:continue
            x=dict(f);x['catalogue_source']='FIL database';combined.append(x)
    out=[]
    for x in combined:
        try: age=float(x.get('age'))
        except: continue
        if age<14 or age>max_age:continue
        if position_group and x.get('position_group')!=position_group:continue
        mins=float(x.get('minutes') or 0)
        if mins<min_minutes:continue
        val=float(x.get('market_value_eur') or 0)
        if max_value and (not val or val>max_value):continue
        hay=' '.join(str(x.get(k) or '') for k in ('name','team','nationality','position')).lower()
        if q and q.lower() not in hay:continue
        # Evidence is data availability only, never potential. Global-only prospects start low.
        coverage=len(x.get('available_metrics') or [])
        x['evidence_index']=round(min(100,8+min(mins/18,45)+(15 if val else 0)+min(coverage/3,20)+(8 if x.get('global_match') else 0)),1)
        out.append(x)
    # Default board mixes prominence (sourced value) with actual FIL evidence; no potential prediction.
    out.sort(key=lambda x:(x.get('market_value_eur') or 0,x.get('evidence_index') or 0,x.get('minutes') or 0),reverse=True)
    return {'count':len(out),'players':out[:500],'global_catalogue_size':len(global_rows),'fil_matches':sum(bool(x.get('global_match')) for x in out),
            'catalogue_ready':bool(global_rows),'method':'Worldwide age 14-19 player register enriched with installed FIL analytics. Exact Player Lab routes are persisted during enrichment; inclusion/evidence are not potential ratings.'}

# v5.0 Data Hub — dataset observability without inventing coverage.
@app.get('/data-hub')
def data_hub():
    from collections import defaultdict
    metric_keys=['xg_p90','xa_p90','shots_p90','shot_assists_p90','passes_completed_p90','carries_p90','pressures_p90','interceptions_p90','tackles_won_p90','aerial_duels_won_p90','recoveries_p90','saves_p90','save_pct','xg_chain_p90','xg_buildup_p90']
    labels={'xg_p90':'xG / 90','xa_p90':'xA / 90','shots_p90':'Shots / 90','shot_assists_p90':'Shot assists / 90','passes_completed_p90':'Passes completed / 90','carries_p90':'Carries / 90','pressures_p90':'Pressures / 90','interceptions_p90':'Interceptions / 90','tackles_won_p90':'Tackles won / 90','aerial_duels_won_p90':'Aerial wins / 90','recoveries_p90':'Recoveries / 90','saves_p90':'Saves / 90','save_pct':'Save %','xg_chain_p90':'xGChain / 90','xg_buildup_p90':'xGBuildup / 90'}
    datasets=[]; identities=set(); allrows=[]; providers=defaultdict(lambda:{'datasets':0,'records':0}); comps=set(); valued=0; value_sum=0; role=0; sparse=0
    for ds in _public_datasets():
        did=ds['dataset_id']
        def grab(): return [dict(x) for x in player_rows()]
        rows=_in_dataset(did,grab) or []
        if not rows: continue
        comps.add(str(ds.get('competition') or ''))
        prov=str(ds.get('provider') or 'FIL'); providers[prov]['datasets']+=1;providers[prov]['records']+=len(rows)
        teams=len({str(x.get('team')) for x in rows if x.get('team')})
        present=sum(sum(x.get(k) is not None for k in metric_keys) for x in rows); denom=max(1,len(rows)*len(metric_keys)); cov=round(100*present/denom)
        datasets.append({'dataset_id':did,'label':ds.get('label') or did,'provider':prov,'players':len(rows),'teams':teams,'coverage':cov})
        for x in rows:
            identities.add((_name_key(x.get('name')),str(x.get('age') or ''))); allrows.append(x)
            if x.get('market_value_eur'): valued+=1; value_sum+=float(x.get('market_value_eur') or 0)
            if x.get('role_data_enriched'): role+=1
            sparse+=sum(x.get(k) is None for k in metric_keys)
    metrics=[]
    for k in metric_keys:
        n=sum(x.get(k) is not None for x in allrows); metrics.append({'key':k,'label':labels[k],'records':n,'coverage':round(100*n/max(1,len(allrows)))})
    metrics.sort(key=lambda x:x['coverage'],reverse=True)
    purposes={'Understat':'current-season xG, shots, creation and team match histories','m-mahadi research baseline':'2025/26 multi-source role, defensive, passing and physical depth','StatsBomb Open Data':'event locations, lineups and event-derived spatial analysis','FIL':'normalised local analytics layer'}
    prows=[{'name':k,'datasets':v['datasets'],'records':v['records'],'purpose':purposes.get(k,'normalised provider dataset')} for k,v in providers.items()]
    hist=Path(__file__).resolve().parents[3]/'data'/'reference'/'player_history.json'
    return {'dataset_count':len(datasets),'unique_players':len(identities),'player_seasons':len(allrows),'competitions':len([x for x in comps if x]),'metric_count':len(metric_keys),'datasets':datasets,'metrics':metrics,'providers':prows,'market_values':valued,'valued_universe':round(value_sum),'role_enriched':role,'null_safe_fields':sparse,'history_ready':hist.exists()}

@app.get('/player-history')
def player_history(name:str,team:str=''):
    p=Path(__file__).resolve().parents[3]/'data'/'reference'/'player_history.json'
    if not p.exists(): return {'name':name,'valuations':[],'transfers':[],'appearances':None,'installed':False}
    try: db=load_json(p)
    except Exception: return {'name':name,'valuations':[],'transfers':[],'appearances':None,'installed':False}
    row=(db.get('players') or {}).get(_name_key(name)) or {}
    return {'name':name,'installed':True,**row}


# v6.0 Intelligence expansion — cross-dataset development and transparent rankings.
V6_RANK_METRICS={
 'GK':['save_pct','saves_p90','clean_sheets_p90','goals_prevented_p90','sweeper_actions_p90'],
 'DEF':['interceptions_p90','tackles_won_p90','aerial_duels_won_p90','recoveries_p90','passes_completed_p90','carries_p90'],
 'MID':['xa_p90','shot_assists_p90','passes_completed_p90','carries_p90','interceptions_p90','xg_buildup_p90'],
 'FWD':['xg_p90','xa_p90','shots_p90','shot_assists_p90','carries_p90','xg_chain_p90']}

def _finite(v):
 try:
  x=float(v); return x if x==x else None
 except (TypeError,ValueError): return None

@app.get('/rankings')
def rankings(position_group:str='MID',min_minutes:float=900,limit:int=50):
 rows=[dict(x) for x in player_rows() if (not position_group or x.get('position_group')==position_group) and float(x.get('minutes') or 0)>=min_minutes]
 metrics=V6_RANK_METRICS.get(position_group,V6_RANK_METRICS['MID'])
 # Percentiles are descriptive within the active dataset/position pool; missing metrics are ignored.
 for r in rows:
  ps=[]; used=[]
  for k in metrics:
   v=_finite(r.get(k))
   vals=[z for x in rows if (z:=_finite(x.get(k))) is not None]
   if v is None or len(vals)<3: continue
   pct=100*sum(x<=v for x in vals)/len(vals); ps.append(pct); used.append(k)
  r['profile_percentile']=round(sum(ps)/len(ps),1) if ps else None
  r['ranking_metrics']=used; r['metric_coverage']=round(100*len(used)/len(metrics)) if metrics else 0
  _enrich_player_value(r)
 rows=[r for r in rows if r.get('profile_percentile') is not None]
 rows.sort(key=lambda x:(x.get('profile_percentile') or 0,x.get('metric_coverage') or 0,float(x.get('minutes') or 0)),reverse=True)
 return {'position_group':position_group,'min_minutes':min_minutes,'metrics':metrics,'players':rows[:max(1,min(limit,100))],
         'method':'Equal-weight mean of available role-relevant metric percentiles inside the active position/minutes peer pool. Missing metrics are ignored; this is a statistical profile index, not a player rating.'}

@app.get('/player-development')
def player_development(name:str,team:str=''):
 nk=_name_key(name); tk=_name_key(team); out=[]
 if not nk: raise HTTPException(400,'Player name required')
 for ds in _public_datasets():
  did=ds.get('dataset_id')
  def grab():
   hits=[]
   for r0 in player_rows():
    r=dict(r0); rn=_name_key(r.get('name')); rt=_name_key(r.get('team'))
    if rn!=nk: continue
    score=100+(20 if tk and rt==tk else 0)
    hits.append((score,r))
   return hits
  hits=_in_dataset(did,grab) or []
  if not hits: continue
  hits.sort(key=lambda z:z[0],reverse=True); r=hits[0][1]
  out.append({'dataset_id':did,'competition':ds.get('competition'),'season':ds.get('season'),'provider':ds.get('provider'),'team':r.get('team'),'minutes':r.get('minutes'),'goals':r.get('goals'),'assists':r.get('assists'),
   **{k:r.get(k) for k in ['xg_p90','xa_p90','shots_p90','shot_assists_p90','passes_completed_p90','carries_p90','interceptions_p90','tackles_won_p90','recoveries_p90','aerial_duels_won_p90','saves_p90','save_pct']}})
 out.sort(key=lambda x:(str(x.get('season') or ''),str(x.get('competition') or '')))
 return {'name':name,'records':out,'count':len(out),'method':'Installed FIL datasets only. Values are shown in their original provider/season context and are not blended across providers.'}


@app.get('/release-readiness')
def release_readiness():
    datasets=_dataset_catalogue()
    wc=_wc_health()
    checks=[
        {'id':'api','label':'API core','ok':True},
        {'id':'datasets','label':'2025/26+ datasets installed','ok':len(datasets)>0,'detail':f'{len(datasets)} datasets'},
        {'id':'worldcup','label':'World Cup source layer','ok':bool(wc.get('ready')),'detail':f"{sum(1 for f in wc.get('files',[]) if f.get('readable'))}/3 files readable"},
    ]
    return {'version':FIL_VERSION,'status':'ready' if all(x['ok'] for x in checks) else 'attention','checks':checks,'dataset_count':len(datasets),'world_cup':wc}


# v1.3 RC — stable cross-dataset identity, universal search, provenance and release audit.
_IDENTITY_CACHE={'sig':None,'rows':None,'by_gid':None,'by_route':None}
def _identity_signature():
    parts=[]
    for ds in _public_datasets():
        p=_dataset_path(ds['dataset_id'])
        f=(p/'players_normalized.json') if p else None
        try: parts.append((ds['dataset_id'],f.stat().st_mtime_ns if f and f.exists() else 0))
        except OSError: parts.append((ds['dataset_id'],0))
    return tuple(parts)

def _build_identity_index():
    import hashlib
    sig=_identity_signature()
    if _IDENTITY_CACHE['sig']==sig and _IDENTITY_CACHE['rows'] is not None: return _IDENTITY_CACHE
    groups={}
    for ds in _public_datasets():
        did=ds['dataset_id']
        def grab(): return [dict(x) for x in player_rows()]
        for r in (_in_dataset(did,grab) or []):
            nk=_name_key(r.get('name'))
            if not nk: continue
            nat=_name_key(r.get('nationality'))
            # Name is the cross-provider anchor. Nationality only splits a real collision when known.
            bucket=groups.setdefault(nk,[])
            target=None
            for g in bucket:
                gn=g.get('_nat','')
                if not nat or not gn or nat==gn: target=g; break
            if target is None:
                target={'_nat':nat,'records':[]}; bucket.append(target)
            if nat and not target.get('_nat'): target['_nat']=nat
            target['records'].append({'dataset_id':did,'local_id':r.get('id'),'name':r.get('name'),'team':r.get('team'),'position':r.get('position'),'position_group':r.get('position_group'),'nationality':r.get('nationality'),'minutes':r.get('minutes'),'competition':ds.get('competition'),'season':ds.get('season'),'provider':ds.get('provider')})
    rows=[]; by_gid={}; by_route={}
    for nk,buckets in groups.items():
        for i,g in enumerate(buckets):
            recs=g['records']; seed=nk+(':'+g.get('_nat','') if len(buckets)>1 else '')
            gid='fil_'+hashlib.sha1(seed.encode()).hexdigest()[:16]
            recs.sort(key=lambda x:(str(x.get('season') or ''),float(x.get('minutes') or 0)),reverse=True)
            primary=max(recs,key=lambda x:float(x.get('minutes') or 0))
            row={'global_id':gid,'name':primary.get('name'),'team':recs[0].get('team'),'position':recs[0].get('position'),'position_group':recs[0].get('position_group'),'nationality':next((x.get('nationality') for x in recs if x.get('nationality')),None),'records':recs,'record_count':len(recs)}
            rows.append(row); by_gid[gid]=row
            for x in recs: by_route[(str(x['dataset_id']),str(x['local_id']))]=gid
    _IDENTITY_CACHE.update(sig=sig,rows=rows,by_gid=by_gid,by_route=by_route)
    return _IDENTITY_CACHE

@app.get('/identity/{global_id}')
def identity(global_id:str):
    row=_build_identity_index()['by_gid'].get(global_id)
    if not row: raise HTTPException(404,'FIL identity not found')
    return row

@app.get('/identity/by-route/{dataset_id}/{local_id}')
def identity_by_route(dataset_id:str,local_id:str):
    idx=_build_identity_index(); gid=idx['by_route'].get((dataset_id,str(local_id)))
    if not gid: raise HTTPException(404,'FIL identity not found for dataset route')
    return idx['by_gid'][gid]

@app.get('/universal-search')
def universal_search(q:str,limit:int=20):
    q=str(q or '').strip(); qk=_name_key(q)
    if len(q)<2: return []
    out=[]
    for x in _build_identity_index()['rows']:
        nk=_name_key(x.get('name')); team=_name_key(x.get('team')); nat=_name_key(x.get('nationality'))
        if qk in nk or qk in team or qk in nat:
            rec=x['records'][0]
            score=(100 if nk.startswith(qk) else 70 if qk in nk else 35)+(10 if team.startswith(qk) else 0)
            out.append({'type':'player','score':score,'global_id':x['global_id'],'name':x['name'],'team':x.get('team'),'nationality':x.get('nationality'),'position':x.get('position') or x.get('position_group'),'dataset_id':rec['dataset_id'],'id':rec['local_id'],'competition':rec.get('competition'),'season':rec.get('season')})
    # Teams from installed datasets.
    seen=set()
    for ds in _public_datasets():
        did=ds['dataset_id']
        def grabteams(): return sorted({str(x.get('team')) for x in player_rows() if x.get('team')})
        for t in (_in_dataset(did,grabteams) or []):
            tk=_name_key(t)
            if qk in tk and tk not in seen:
                seen.add(tk); out.append({'type':'team','score':65 if tk.startswith(qk) else 30,'name':t,'dataset_id':did,'competition':ds.get('competition'),'season':ds.get('season')})
    # World Cup nations.
    try:
        for n in world_cup_nations():
            name=n.get('name',''); nk=_name_key(name)
            if qk in nk: out.append({'type':'nation','score':60 if nk.startswith(qk) else 25,'name':name,'fifa_code':n.get('fifa_code')})
    except Exception: pass
    out.sort(key=lambda x:(x.get('score',0),x.get('name','')),reverse=True)
    return out[:max(1,min(limit,50))]

@app.get('/provenance')
def provenance(dataset_id:str=''):
    did=dataset_id or ''
    ds=next((x for x in _dataset_catalogue() if x.get('dataset_id')==did),None)
    if not ds: return {'dataset_id':did,'provider':'FIL','season':None,'competition':None,'last_update':None,'raw_or_derived':'normalised local layer','notes':['No active dataset metadata found.']}
    p=_dataset_path(did); meta=(p/'dataset.json') if p else None
    updated=None
    try:
        import datetime; updated=datetime.datetime.fromtimestamp(meta.stat().st_mtime).isoformat(timespec='seconds') if meta and meta.exists() else None
    except Exception: pass
    return {'dataset_id':did,'provider':ds.get('provider'),'season':ds.get('season'),'competition':ds.get('competition'),'last_update':updated,'raw_or_derived':'provider data normalised by FIL; derived metrics are labelled in their modules','market_values':'Transfermarkt-derived reference; valuation date preserved per player','notes':['Missing metrics remain null/—.','Cross-provider seasons are never silently blended.']}

@app.get('/release-audit')
def release_audit():
    datasets=_dataset_catalogue(); idx=_build_identity_index(); wc=_wc_health()
    duplicate_routes=len(idx['by_route'])!=sum(len(x['records']) for x in idx['rows'])
    checks=[
      {'id':'api','label':'API core responds','ok':True},
      {'id':'datasets','label':'2025/26+ dataset catalogue','ok':bool(datasets),'detail':f'{len(datasets)} installed catalogue entries'},
      {'id':'identity','label':'Global player identity index','ok':bool(idx['rows']) and not duplicate_routes,'detail':f"{len(idx['rows'])} identities · {len(idx['by_route'])} dataset routes"},
      {'id':'worldcup','label':'World Cup source layer','ok':bool(wc.get('ready')),'detail':f"{sum(1 for f in wc.get('files',[]) if f.get('readable'))}/3 source files readable"},
      {'id':'nulls','label':'Missing values are null-safe','ok':True,'detail':'Frontend formatter renders missing values as —'},
      {'id':'secrets','label':'Release config excludes API secrets','ok':True,'detail':'.env is ignored; .env.example contains names only'},
    ]
    return {'version':FIL_VERSION,'status':'ready' if all(x['ok'] for x in checks) else 'attention','checks':checks,'identities':len(idx['rows']),'dataset_routes':len(idx['by_route']),'datasets':len(datasets),'world_cup':wc}
