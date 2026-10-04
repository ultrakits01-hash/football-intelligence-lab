from __future__ import annotations
import json, math, re, unicodedata
from pathlib import Path
from collections import defaultdict, Counter
from contextvars import ContextVar
import numpy as np

ROOT=Path(__file__).resolve().parents[3]
DATA_ROOT=ROOT/'data'/'raw'/'statsbomb'
ACTIVE_DATA=ContextVar('ACTIVE_DATA', default=DATA_ROOT)
def set_active_data(path: Path): return ACTIVE_DATA.set(path)
def reset_active_data(token): ACTIVE_DATA.reset(token)
def data_dir(): return ACTIVE_DATA.get()

def _num(value, default=0.0):
    """Null/NaN-safe numeric conversion for sparse provider datasets."""
    try:
        if value is None or value == "": return default
        v=float(value)
        return default if np.isnan(v) or np.isinf(v) else v
    except (TypeError, ValueError):
        return default

def _real_numbers(rows, key):
    out=[]
    for r in rows:
        v=r.get(key)
        if v is None or v == "": continue
        try:
            f=float(v)
            if not np.isnan(f) and not np.isinf(f): out.append(f)
        except (TypeError, ValueError):
            pass
    return out

FEATURES=['xg','shots','shot_assists','xa','xg_chain','xg_buildup','passes','passes_completed','carries','pressures','interceptions','duels','recoveries','tackles','tackles_won','clearances','blocks','aerial_duels','aerial_duels_won','ground_duels','ground_duels_won','dribbled_past','errors_shot','errors_goal','final_third_passes','long_balls','long_balls_completed','saves','goals_against','clean_sheets','psxg','claims','punches','sweeper_actions']
ATTACK_FEATURES={'xg','shots','shot_assists','carries'}
DEF_FEATURES={'pressures','interceptions','duels'}


def load_json(path: Path):
    with path.open(encoding='utf-8') as f: return json.load(f)

def event_files():
    d=data_dir(); return sorted((d/'events').glob('*.json')) if (d/'events').exists() else []

def all_events():
    for p in event_files():
        for e in load_json(p): yield e

def _position_group(name:str|None):
    n=(name or '').lower()
    if 'goalkeeper' in n: return 'GK'
    if any(x in n for x in ['back','defender']): return 'DEF'
    if any(x in n for x in ['wing','forward','striker']): return 'FWD'
    if any(x in n for x in ['midfield','center midfield']): return 'MID'
    return 'UNK'

def _match_minutes_and_positions(events):
    """Derive minutes from Starting XI + Substitution events; position from tactical lineups.
    This stays provider-transparent and is intentionally event-derived rather than guessed."""
    match_end=max([float(e.get('minute') or 0) for e in events] or [90.0])
    starts={}; ends={}; pos_time=defaultdict(Counter); teams={}; names={}
    current_pos={}
    for e in events:
        typ=e.get('type',{}).get('name'); minute=float(e.get('minute') or 0)
        team=e.get('team',{}).get('name')
        if typ in ('Starting XI','Tactical Shift'):
            for row in e.get('tactics',{}).get('lineup',[]):
                p=row.get('player',{}); pos=row.get('position',{}).get('name')
                if not p.get('id'): continue
                pid=p['id']; names[pid]=p.get('name'); teams[pid]=team
                if typ=='Starting XI': starts.setdefault(pid,0.0)
                current_pos[pid]=(pos,minute)
        elif typ=='Substitution':
            p=e.get('player',{}); pid=p.get('id')
            if pid:
                names[pid]=p.get('name'); teams[pid]=team; ends[pid]=minute
                if pid in current_pos:
                    pos,st=current_pos.pop(pid); pos_time[pid][pos]+=max(0,minute-st)
            rep=e.get('substitution',{}).get('replacement',{})
            rid=rep.get('id')
            if rid:
                names[rid]=rep.get('name'); teams[rid]=team; starts.setdefault(rid,minute)
                # replacement position is the outgoing player's current role when available
                outpos=e.get('position',{}).get('name') or (current_pos.get(pid,(None,0))[0] if pid else None)
                current_pos[rid]=(outpos,minute)
    for pid,(pos,st) in list(current_pos.items()): pos_time[pid][pos]+=max(0,match_end-st)
    mins={pid:max(0.0,(ends.get(pid,match_end)-st)) for pid,st in starts.items()}
    positions={pid:(pos_time[pid].most_common(1)[0][0] if pos_time[pid] else None) for pid in starts}
    return mins,positions,names,teams

def player_rows():
    # Provider-normalised datasets can ship a ready player table. This keeps the
    # analytics/UI provider-agnostic while preserving StatsBomb event derivation.
    normalized=data_dir()/'players_normalized.json'
    if normalized.exists():
        rows=load_json(normalized)
        # 2025/26 Understat is excellent for attacking/xG data but intentionally thin
        # for defenders and goalkeepers. Enrich the SAME player-season from the
        # installed m-mahadi baseline when available; never fabricate or cross-season fill.
        meta_path=data_dir()/'dataset.json'
        if meta_path.exists():
            try:
                meta=load_json(meta_path); provider=str(meta.get('provider') or '').lower(); season=str(meta.get('season') or '')
                comp=str(meta.get('competition') or '')
                slug={'Premier League':'premier-league','La Liga':'la-liga','Bundesliga':'bundesliga','Serie A':'serie-a','Ligue 1':'ligue-1'}.get(comp)
                if provider=='understat' and season.startswith('2025') and slug:
                    bp=DATA_ROOT/'datasets'/f'baseline_{slug}_2025'/'players_normalized.json'
                    if bp.exists():
                        key=lambda v: re.sub(r'[^a-z0-9]+','',unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().lower())
                        baseline_rows=load_json(bp)
                        baseline={key(r.get('name')):r for r in baseline_rows}
                        by_team=defaultdict(list)
                        for br in baseline_rows: by_team[key(br.get('team'))].append(br)
                        depth=['passes','passes_completed','carries','pressures','interceptions','duels','recoveries','tackles','tackles_won','clearances','blocks','aerial_duels','aerial_duels_won','aerial_duels_won_pct','ground_duels','ground_duels_won','ground_duels_won_pct','dribbled_past','errors_shot','errors_goal','pass_accuracy_pct','final_third_passes','long_balls','long_balls_completed','saves','save_pct','goals_against','clean_sheets','psxg','goals_prevented','claims','punches','sweeper_actions','top_speed','distance_covered','sprints']
                        for r in rows:
                            b=baseline.get(key(r.get('name')))
                            # Understat sometimes uses a short display name (e.g. Gabriel).
                            # Resolve only when team + role makes the baseline candidate unique.
                            if not b:
                                rn=key(r.get('name')); rg=r.get('position_group')
                                cands=[x for x in by_team.get(key(r.get('team')),[]) if (not rg or x.get('position_group')==rg) and (key(x.get('name')).startswith(rn) or rn.startswith(key(x.get('name'))))]
                                if len(cands)==1: b=cands[0]
                            if not b: continue
                            # Team guard prevents a rare same-name collision.
                            if r.get('team') and b.get('team') and key(r.get('team'))!=key(b.get('team')): continue
                            used=[]
                            for k in depth:
                                if b.get(k) is not None and b.get(k) != '':
                                    r[k]=b[k]
                                    if k+'_p90' in b:r[k+'_p90']=b[k+'_p90']
                                    used.append(k)
                            if used:
                                r['role_data_enriched']=True
                                r['role_data_source']='m-mahadi 2025/26 baseline (FBref/SofaScore-derived fields)'
                                r['role_data_metrics']=used
            except Exception as exc:
                print(f'[FIL] role-depth enrichment skipped: {exc}')
        return rows
    agg=defaultdict(lambda: defaultdict(float)); names={}; teams={}; pos_counts=defaultdict(Counter)
    for path in event_files():
        events=load_json(path)
        mins,positions,match_names,match_teams=_match_minutes_and_positions(events)
        for pid,m in mins.items():
            agg[pid]['minutes']+=m
            if positions.get(pid): pos_counts[pid][positions[pid]]+=m
            if match_names.get(pid): names[pid]=match_names[pid]
            if match_teams.get(pid): teams[pid]=match_teams[pid]
        for e in events:
            p=e.get('player'); t=e.get('team'); typ=e.get('type',{}).get('name')
            if not p: continue
            pid=p['id']; names[pid]=p['name']; teams[pid]=t['name'] if t else teams.get(pid)
            a=agg[pid]; a['events']+=1
            if typ=='Shot':
                a['shots']+=1; a['xg']+=e.get('shot',{}).get('statsbomb_xg',0) or 0
                if e.get('shot',{}).get('outcome',{}).get('name')=='Goal': a['goals']+=1
            elif typ=='Pass':
                a['passes']+=1
                if 'outcome' not in e.get('pass',{}): a['passes_completed']+=1
                if e.get('pass',{}).get('shot_assist'): a['shot_assists']+=1
                if e.get('pass',{}).get('goal_assist'): a['assists']+=1
            elif typ=='Carry': a['carries']+=1
            elif typ=='Pressure': a['pressures']+=1
            elif typ=='Interception': a['interceptions']+=1
            elif typ=='Duel': a['duels']+=1
    rows=[]
    for pid,v in agg.items():
        r={'id':pid,'name':names.get(pid,str(pid)),'team':teams.get(pid),**dict(v)}
        r['minutes']=round(float(r.get('minutes',0)),1)
        r['position']=pos_counts[pid].most_common(1)[0][0] if pos_counts[pid] else None
        r['position_group']=_position_group(r['position'])
        for k in FEATURES:
            r[k+'_p90']=round(float(r.get(k,0))*90/r['minutes'],3) if r['minutes']>=1 else 0.0
        rows.append(r)
    return rows

def peer_rows(rows,target,min_minutes=450):
    pool=[r for r in rows if r.get('minutes',0)>=min_minutes and r.get('position_group')==target.get('position_group')]
    if len(pool)<8: pool=[r for r in rows if r.get('minutes',0)>=min_minutes]
    return pool

def robust_matrix(rows, features):
    X=np.array([[_num(r.get(k)) for k in features] for r in rows],dtype=float)
    med=np.median(X,axis=0); mad=np.median(np.abs(X-med),axis=0); scale=1.4826*np.where(mad==0,1,mad)
    return np.clip((X-med)/scale,-3,3)

def add_percentiles(target, rows):
    peers=peer_rows(rows,target)
    target['peer_group']={'position_group':target.get('position_group'),'minimum_minutes':450,'players':len(peers)}
    target['percentiles']={}
    for k in FEATURES:
        key=k+'_p90'; vals=_real_numbers(peers,key)
        raw=target.get(key); v=None if raw is None else _num(raw)
        target['percentiles'][key]=(None if v is None or not vals or max(vals)-min(vals)<1e-9 else round(100*sum(a<=v for a in vals)/max(len(vals),1)))
    return target

def similar(pid:int, limit=10):
    rows=player_rows(); target=next((r for r in rows if r['id']==pid),None)
    if not target:return []
    # Never widen a similarity pool across positions just to fill the UI. Early in
    # a season that produced absurd CF-vs-full-back matches. Prefer the provider's
    # exact role, then the coarse group, and lower the minutes floor if necessary.
    raw=(target.get('position') or '').strip().upper()
    def same_role(r):
        rr=(r.get('position') or '').strip().upper()
        return (raw and rr==raw) or (not raw and r.get('position_group')==target.get('position_group'))
    floors=[450,270,180,90]
    pool=[]
    for floor in floors:
        pool=[r for r in rows if r.get('id')!=pid and same_role(r) and r.get('minutes',0)>=floor]
        if len(pool)>=max(limit,5): break
    if not pool:
        pool=[r for r in rows if r.get('id')!=pid and r.get('position_group')==target.get('position_group') and r.get('minutes',0)>=90]
    group=target.get('position_group')
    weights={'FWD':{'xg_p90':4.0,'shots_p90':3.0,'xa_p90':1.2,'shot_assists_p90':1.2,'xg_chain_p90':1.0,'xg_buildup_p90':0.5},
             'MID':{'xa_p90':2.2,'shot_assists_p90':2.0,'xg_chain_p90':1.5,'xg_buildup_p90':1.3,'xg_p90':1.0,'shots_p90':0.8},
             'DEF':{'xg_buildup_p90':2.0,'xg_chain_p90':1.5,'xa_p90':1.0,'shot_assists_p90':1.0,'xg_p90':0.5,'shots_p90':0.5}}.get(group,{})
    features=[]
    for k,w in weights.items():
        vals=[float(r.get(k,0) or 0) for r in pool+[target]]
        if w>0 and max(vals)-min(vals)>1e-8: features.append(k)
    if not features:return []
    full=pool+[target]; X=robust_matrix(full,features); i=len(full)-1
    w=np.array([weights[k] for k in features],dtype=float); w=w/max(w.sum(),1e-9)
    d=np.sqrt(np.sum(((X-X[i])**2)*w,axis=1)); positive=d[d>1e-9]
    scale=float(np.median(positive)) if len(positive) else 1.0
    out=[]
    for j in np.argsort(d):
        if j==i: continue
        contrib=((X[j]-X[i])**2)*w; order=np.argsort(contrib)
        # Similarity is a calibrated profile-distance display, not a rating.
        sim=100*math.exp(-0.85*float(d[j])/max(scale,0.25))
        pretty=lambda k:k.replace('_p90','').replace('_',' ')
        out.append({**full[j],'similarity':round(sim,1),
          'shared_strengths':[pretty(features[k]) for k in order[:min(3,len(order))]],
          'biggest_differences':[pretty(features[k]) for k in order[-min(3,len(order)):][::-1]],
          'method':{'features':[pretty(k) for k in features],'peer_group':raw or group,'minimum_minutes':min(floors[-1], int(target.get('minutes',0) or 0)),'standardisation':'median / 1.4826*MAD','distance':'role-weighted robust Euclidean','transform':'peer-calibrated exponential'}})
        if len(out)>=limit: break
    return out

def player_event_map(pid:int):
    """Return located events when supplied by the active provider. Never synthesize XY data."""
    normalized=data_dir()/'player_maps.json'
    if normalized.exists():
        maps=load_json(normalized); return maps.get(str(pid),{'player_id':pid,'events':[],'shots':[],'capabilities':{'shot_xy':False,'event_xy':False}})
    located=[]; shots=[]
    match_meta={}
    mp=data_dir()/'matches.json'
    if mp.exists():
        match_meta={m.get('match_id'):m for m in load_json(mp)}
    for path in event_files():
        events=load_json(path)
        try: match_id=int(path.stem)
        except ValueError: match_id=None
        meta=match_meta.get(match_id,{})
        home=meta.get('home_team',{}).get('home_team_name'); away=meta.get('away_team',{}).get('away_team_name')
        pass_by_id={e.get('id'):e for e in events if e.get('type',{}).get('name')=='Pass'}
        for e in events:
            p=e.get('player') or {}
            if p.get('id')!=pid: continue
            loc=e.get('location'); typ=e.get('type',{}).get('name'); team=e.get('team',{}).get('name')
            if loc and len(loc)>=2:
                row={'x':loc[0],'y':loc[1],'type':typ,'minute':e.get('minute'),'period':e.get('period'),'team':team}
                located.append(row)
                if typ=='Shot':
                    sh=e.get('shot',{}); assist=None
                    kp=pass_by_id.get(sh.get('key_pass_id'))
                    if kp: assist=(kp.get('player') or {}).get('name')
                    opponent=away if team==home else home if team==away else None
                    shots.append({**row,'xg':sh.get('statsbomb_xg',0) or 0,'outcome':sh.get('outcome',{}).get('name'),'body_part':sh.get('body_part',{}).get('name'),'technique':sh.get('technique',{}).get('name'),'assist':assist,
                                  'match_id':match_id,'match_date':meta.get('match_date'),'opponent':opponent,'home':home,'away':away,'home_score':meta.get('home_score'),'away_score':meta.get('away_score')})
    return {'player_id':pid,'events':located,'shots':shots}

def team_profiles():
    # Normalised team histories (currently Understat) provide richer current-season
    # team analytics than the generic event aggregation path below.
    norm=data_dir()/'teams_normalized.json'
    if norm.exists():
        raw=load_json(norm); rows=[]
        metrics=['xg_pm','xga_pm','npxg_pm','npxga_pm','deep_pm','deep_allowed_pm','xpts_pm','goals_pm','conceded_pm','ppda','oppda']
        for t in raw:
            hist=t.get('history') or []; n=len(hist)
            if not n: continue
            avg=lambda k: sum(float(h.get(k) or 0) for h in hist)/n
            pp=[float(h['ppda']) for h in hist if h.get('ppda') is not None]
            op=[float(h['oppda']) for h in hist if h.get('oppda') is not None]
            r={'team':t.get('team'),'matches':n,'provider':'Understat','goals_pm':round(avg('goals'),2),'conceded_pm':round(avg('conceded'),2),
               'xg_pm':round(avg('xg'),2),'xga_pm':round(avg('xga'),2),'npxg_pm':round(avg('npxg'),2),'npxga_pm':round(avg('npxga'),2),
               'deep_pm':round(avg('deep'),2),'deep_allowed_pm':round(avg('deep_allowed'),2),'xpts_pm':round(avg('xpts'),2),
               'ppda':round(sum(pp)/len(pp),2) if pp else 0,'oppda':round(sum(op)/len(op),2) if op else 0,
               'history':hist,'percentiles':{}}
            rows.append(r)
        # For xGA/conceded/deep allowed/PPDA, lower raw values are the higher percentile.
        lower_better={'xga_pm','npxga_pm','deep_allowed_pm','conceded_pm','ppda'}
        for r in rows:
            r['ranks']={}
            for k in metrics:
                vals=_real_numbers(rows,k); v=_num(r.get(k)); n=max(1,len(vals))
                r['percentiles'][k]=round(100*sum(a>=v for a in vals)/n) if k in lower_better else round(100*sum(a<=v for a in vals)/n)
                # Competition rank: 1 is best. For defensive/pressing metrics
                # where lower is better, count clubs with a strictly LOWER value.
                # For attacking/output metrics, count clubs with a strictly HIGHER value.
                r['ranks'][k]=1+sum(1 for a in vals if (a < v if k in lower_better else a > v))
            r['league_team_count']=len(rows)
        players=player_rows()
        for r in rows:
            r['top_players']=sorted([p for p in players if p.get('team')==r['team']],key=lambda p:p.get('minutes',0),reverse=True)[:8]
        return sorted(rows,key=lambda r:r['team'])
    """Event-derived team profiles per match for the currently ingested competition/season."""
    # Player-season research baselines do not carry match event files. Build a
    # transparent squad-output profile from the real player rows instead of
    # returning an empty Team Lab.
    if not event_files():
        players=player_rows(); by=defaultdict(list)
        for p in players:
            if p.get('team'): by[p['team']].append(p)
        metrics=['goals','xg','shots','passes_completed','carries','pressures','interceptions','duels','recoveries','tackles_won']
        rows=[]
        for team,ps in by.items():
            mins=sum(float(p.get('minutes') or 0) for p in ps)
            if not mins: continue
            r={'team':team,'matches':None,'provider':'FIL 2025/26 player-season baseline','percentiles':{},'ranks':{}}
            for k in metrics:
                vals=[p.get(k) for p in ps if p.get(k) is not None]
                total=sum(float(v) for v in vals) if vals else None
                r[k+'_pm']=None
                r[k+'_p90']=round(total*90/mins,3) if total is not None else None
            r['goals_pm']=r.get('goals_p90'); r['xg_pm']=r.get('xg_p90'); r['shots_pm']=r.get('shots_p90'); r['passes_completed_pm']=r.get('passes_completed_p90')
            r['top_players']=sorted(ps,key=lambda p:float(p.get('minutes') or 0),reverse=True)[:8]
            rows.append(r)
        rank_keys=['goals_pm','xg_pm','shots_pm','passes_completed_pm','carries_p90','pressures_p90','interceptions_p90','duels_p90','recoveries_p90','tackles_won_p90']
        for r in rows:
            r['league_team_count']=len(rows)
            for k in rank_keys:
                vals=_real_numbers(rows,k); rv=r.get(k)
                if rv is None or not vals: r['percentiles'][k]=None; r['ranks'][k]=None
                else:
                    v=_num(rv); r['percentiles'][k]=round(100*sum(a<=v for a in vals)/len(vals)); r['ranks'][k]=1+sum(1 for a in vals if a>v)
        return sorted(rows,key=lambda r:r['team'])
    agg=defaultdict(lambda: defaultdict(float)); matches=Counter()
    for path in event_files():
        events=load_json(path); seen=set()
        for e in events:
            team=e.get('team',{}).get('name')
            if not team: continue
            seen.add(team); typ=e.get('type',{}).get('name'); a=agg[team]
            if typ=='Shot':
                a['shots']+=1; a['xg']+=e.get('shot',{}).get('statsbomb_xg',0) or 0
                if e.get('shot',{}).get('outcome',{}).get('name')=='Goal': a['goals']+=1
            elif typ=='Pass':
                a['passes']+=1
                if 'outcome' not in e.get('pass',{}): a['passes_completed']+=1
            elif typ=='Carry': a['carries']+=1
            elif typ=='Pressure': a['pressures']+=1
            elif typ=='Interception': a['interceptions']+=1
            elif typ=='Duel': a['duels']+=1
        for team in seen: matches[team]+=1
    metrics=['xg','shots','passes_completed','carries','pressures','interceptions','duels']
    rows=[]
    for team,a in agg.items():
        n=max(1,matches[team]); r={'team':team,'matches':matches[team],**dict(a)}
        for k in metrics: r[k+'_pm']=round(float(r.get(k,0))/n,2)
        r['goals_pm']=round(float(r.get('goals',0))/n,2)
        rows.append(r)
    for r in rows:
        r['percentiles']={}; r['ranks']={}; r['league_team_count']=len(rows)
        for k in metrics:
            key=k+'_pm'; vals=_real_numbers(rows,key); rv=r.get(key)
            r['percentiles'][key]=(None if rv is None or not vals else round(100*sum(v<=_num(rv) for v in vals)/max(1,len(vals))))
            if rv is not None and vals:
                v=_num(rv); r['ranks'][key]=1+sum(1 for a in vals if a>v)
    players=player_rows()
    for r in rows:
        squad=sorted([p for p in players if p.get('team')==r['team']],key=lambda p:p.get('minutes',0),reverse=True)
        r['top_players']=squad[:8]
    return sorted(rows,key=lambda r:r['team'])

def scout_rank(position_group='MID', min_minutes=900, weights=None, q=''):
    """Transparent weighted-percentile recruitment ranking over the current event-derived player pool."""
    rows=player_rows()
    pool=[r for r in rows if r.get('minutes',0)>=min_minutes and r.get('position_group')!='GK']
    if position_group: pool=[r for r in pool if r.get('position_group')==position_group]
    if q: pool=[r for r in pool if q.lower() in (r.get('name') or '').lower()]
    if not pool: return []
    weights=weights or {'xg_p90':1,'shot_assists_p90':1,'passes_completed_p90':1,'carries_p90':1,'pressures_p90':1,'interceptions_p90':1}
    metrics=[k for k,v in weights.items() if float(v)>0]
    pct={}
    for k in metrics:
        vals=_real_numbers(pool,k)
        for r in pool:
            rv=r.get(k)
            pct[(r['id'],k)]=(None if rv is None or not vals else round(100*sum(a<=_num(rv) for a in vals)/max(1,len(vals))))
    total_w=sum(float(weights[k]) for k in metrics) or 1
    out=[]
    for r in pool:
        available=[k for k in metrics if pct.get((r['id'],k)) is not None]
        player_w=sum(float(weights[k]) for k in available) or 1
        score=sum(pct[(r['id'],k)]*float(weights[k]) for k in available)/player_w if available else 0
        ranked=sorted(available,key=lambda k:pct[(r['id'],k)]*float(weights[k]),reverse=True)
        out.append({**r,'fit':round(score,1),'fit_percentiles':{k:pct[(r['id'],k)] for k in metrics},'fit_weights':{k:float(weights[k]) for k in metrics},'fit_drivers':ranked[:3],'peer_count':len(pool),'method':'weighted mean of empirical metric percentiles in the current filtered peer pool'})
    return sorted(out,key=lambda r:(r['fit'],r.get('minutes',0)),reverse=True)
