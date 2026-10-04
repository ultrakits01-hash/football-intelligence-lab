from __future__ import annotations
import argparse, gzip, hashlib, json, urllib.error, urllib.request, zlib
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'raw'/'statsbomb'/'datasets'
LEAGUES={'premier-league':'EPL','la-liga':'La_Liga','bundesliga':'Bundesliga','serie-a':'Serie_A','ligue-1':'Ligue_1'}
HEADERS={'User-Agent':'Mozilla/5.0 FIL/1.0','X-Requested-With':'XMLHttpRequest','Referer':'https://understat.com/','Accept':'application/json, text/javascript, */*; q=0.01','Accept-Encoding':'gzip, deflate'}

def get(path):
    url='https://understat.com/'+path.lstrip('/')
    req=urllib.request.Request(url,headers=HEADERS)
    try:
        with urllib.request.urlopen(req,timeout=45) as r:
            raw=r.read()
            encoding=(r.headers.get('Content-Encoding') or '').lower()
            content_type=(r.headers.get('Content-Type') or '').lower()
    except urllib.error.HTTPError as e:
        body=e.read(300).decode('utf-8','replace')
        raise RuntimeError(f'Understat HTTP {e.code} for {path}: {body}') from e
    except urllib.error.URLError as e:
        raise RuntimeError(f'Understat connection failed for {path}: {e.reason}') from e

    # urllib does not automatically decompress HTTP response bodies. Understat
    # currently serves these JSON endpoints gzip-compressed (1f 8b magic).
    try:
        if encoding == 'gzip' or raw[:2] == b'\x1f\x8b':
            raw=gzip.decompress(raw)
        elif encoding == 'deflate':
            try: raw=zlib.decompress(raw)
            except zlib.error: raw=zlib.decompress(raw,-zlib.MAX_WBITS)
        text=raw.decode('utf-8-sig')
        return json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, zlib.error) as e:
        preview=raw[:160].decode('utf-8','replace')
        raise RuntimeError(f'Understat returned an unreadable response for {path} (content-type={content_type!r}, encoding={encoding!r}): {preview!r}') from e
def stable_id(provider_id): return int('8'+hashlib.sha1(('understat:'+str(provider_id)).encode()).hexdigest()[:8],16)
def f(v):
    try:return float(v or 0)
    except:return 0.0
def pos_group(p):
    p=(p or '').upper()
    if 'GK' in p:return 'GK'
    if 'D' in p:return 'DEF'
    if 'F' in p:return 'FWD'
    return 'MID'
def main():
    ap=argparse.ArgumentParser(description='Ingest current Big-5 Understat data into FIL without fabricating unsupported metrics.')
    ap.add_argument('league',choices=LEAGUES); ap.add_argument('season',type=int,help='start year: 2026 = 2026/27')
    ap.add_argument('--shots',action='store_true',help='also fetch completed match shot XY (more requests)')
    a=ap.parse_args(); slug=LEAGUES[a.league]; data=get(f'getLeagueData/{slug}/{a.season}')
    ds=OUT/f'understat_{slug}_{a.season}'; ds.mkdir(parents=True,exist_ok=True)
    players=[]
    for x in data.get('players',[]):
        pid=stable_id(x.get('id')); mins=f(x.get('time')); games=f(x.get('games'))
        row={'id':pid,'provider_id':str(x.get('id')),'provider':'Understat','name':x.get('player_name') or x.get('player'),'team':x.get('team_title') or x.get('team_name'),'position':x.get('position'),'position_group':pos_group(x.get('position')),
             'minutes':mins,'appearances':games,'goals':f(x.get('goals')),'assists':f(x.get('assists')),'shots':f(x.get('shots')),'xg':f(x.get('xG')),'xa':f(x.get('xA')),'npxg':f(x.get('npxG')),'key_passes':f(x.get('key_passes')),'xg_chain':f(x.get('xGChain')),'xg_buildup':f(x.get('xGBuildup')),
             'shot_assists':f(x.get('key_passes')),'passes':0,'passes_completed':0,'carries':0,'pressures':0,'interceptions':0,'duels':0,'events':0}
        for k in ['xg','shots','shot_assists','xg_chain','xg_buildup','passes','passes_completed','carries','pressures','interceptions','duels']:
            row[k+'_p90']=round(row[k]*90/mins,3) if mins else 0
        row['xa_p90']=round(row['xa']*90/mins,3) if mins else 0
        players.append(row)
    # Understat league data also carries team match histories. Preserve them so
    # Team Intelligence can use real current-season xG/xGA, npxG, PPDA, deep
    # completions and expected-points data rather than event-derived placeholders.
    teams=[]
    raw_teams=data.get('teams') or {}
    team_iter=raw_teams.values() if isinstance(raw_teams,dict) else raw_teams
    for t in team_iter:
        hist=[]
        for h in t.get('history',[]) or []:
            pp=h.get('ppda') or {}; opp=h.get('ppda_allowed') or {}
            def ratio(x):
                att=f(x.get('att')); de=f(x.get('def'))
                return round(att/de,3) if de else None
            hist.append({'date':h.get('date'),'home_away':h.get('h_a'),'xg':f(h.get('xG')),'xga':f(h.get('xGA')),
                         'npxg':f(h.get('npxG')),'npxga':f(h.get('npxGA')),'npxgd':f(h.get('npxGD')),
                         'ppda':ratio(pp),'oppda':ratio(opp),'deep':f(h.get('deep')),'deep_allowed':f(h.get('deep_allowed')),
                         'goals':f(h.get('scored')),'conceded':f(h.get('missed')),'xpts':f(h.get('xpts')),
                         'points':f(h.get('pts')),'result':h.get('result')})
        teams.append({'provider_id':str(t.get('id')),'team':t.get('title'),'history':hist})
    matches=[]
    for m in data.get('dates',[]):
        matches.append({'match_id':int(m.get('id')),'date':(m.get('datetime') or '')[:10],'home':(m.get('h') or {}).get('title'),'away':(m.get('a') or {}).get('title'),'home_score':(m.get('goals') or {}).get('h'),'away_score':(m.get('goals') or {}).get('a'),'is_result':bool(m.get('isResult'))})
    maps={}
    match_details={}
    # A lightweight team/player refresh must not erase previously downloaded shot maps.
    if not a.shots:
        old_maps=ds/'player_maps.json'; old_details=ds/'match_details.json'
        if old_maps.exists(): maps=json.loads(old_maps.read_text(encoding='utf-8'))
        if old_details.exists(): match_details=json.loads(old_details.read_text(encoding='utf-8'))
    if a.shots:
        for i,m in enumerate([x for x in matches if x['is_result']],1):
            try: md=get(f"getMatchData/{m['match_id']}")
            except Exception as e: print('shot fetch skipped',m['match_id'],e); continue
            # Preserve the provider's match roster/stat rows as well as shots. Understat
            # supplies player match output (minutes, goals, assists, xG/xA etc.) here.
            roster_rows=[]
            for side in ('h','a'):
                for rr in (md.get('rosters') or {}).get(side,{}).values():
                    row=dict(rr)
                    row['team']=m['home'] if side=='h' else m['away']
                    row['side']=side
                    roster_rows.append(row)
                for sh in (md.get('shots') or {}).get(side,[]):
                    sid=stable_id(sh.get('player_id')); mm=maps.setdefault(str(sid),{'player_id':sid,'events':[],'shots':[],'capabilities':{'shot_xy':True,'event_xy':False}})
                    mm['shots'].append({'x':f(sh.get('X'))*120,'y':f(sh.get('Y'))*80,'player':sh.get('player'),'player_id':sid,'xg':f(sh.get('xG')),'outcome':sh.get('result'),'minute':sh.get('minute'),'team':sh.get('h_team') if side=='h' else sh.get('a_team'),'match_id':m['match_id'],'match_date':m['date'],'home':m['home'],'away':m['away'],'home_score':m['home_score'],'away_score':m['away_score'],'opponent':m['away'] if side=='h' else m['home'],'body_part':sh.get('shotType'),'assist':sh.get('player_assisted')})
            match_details[str(m['match_id'])]={'rosters':roster_rows}
            print(f'shots {i}/{sum(x["is_result"] for x in matches)}',end='\r')
    meta={'competition_id':f'understat-{slug}','season_id':a.season,'competition':a.league.replace('-',' ').title(),'season':f'{a.season}/{str(a.season+1)[-2:]}','label':f'{a.league.replace("-"," ").title()} · {a.season}/{str(a.season+1)[-2:]}','provider':'Understat','kind':'normalized','match_count':len(matches),'capabilities':{'player_season_stats':True,'xg':True,'xa':True,'shot_xy':bool(a.shots),'event_xy':False,'pass_xy':False,'pressure_xy':False,'lineups':False,'substitutions':False,'match_player_stats':bool(a.shots),'match_shots':bool(a.shots),'team_season_stats':True,'team_match_stats':True,'metrics':['goals','assists','shots','xg','xa','npxg','key_passes','xg_chain','xg_buildup','team_xga','team_ppda','team_deep','team_xpts']}}
    (ds/'dataset.json').write_text(json.dumps(meta,indent=2)); (ds/'players_normalized.json').write_text(json.dumps(players)); (ds/'teams_normalized.json').write_text(json.dumps(teams)); (ds/'matches_normalized.json').write_text(json.dumps(matches)); (ds/'player_maps.json').write_text(json.dumps(maps)); (ds/'match_details.json').write_text(json.dumps(match_details))
    print(f'\nFIL ingested {len(players)} players, {len(matches)} matches -> {ds}')
if __name__=='__main__': main()
