from __future__ import annotations
import argparse,csv,hashlib,json,urllib.request
from collections import defaultdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; OUT=ROOT/'data'/'raw'/'statsbomb'/'datasets'
DEFAULT='https://raw.githubusercontent.com/m-mahadi/top5-football-dataset/master/data/master/player_seasons.csv'
def num_opt(r,*keys):
    """Return a real source value or None. Missing is never converted to zero."""
    for k in keys:
        try:
            v=r.get(k)
            if v not in (None,'','nan','NaN'):
                return float(v)
        except (TypeError,ValueError):
            pass
    return None
def num(r,*keys):
    v=num_opt(r,*keys)
    return 0.0 if v is None else v
def pick(r,*keys):
    for k in keys:
        if r.get(k) not in (None,''): return r[k]
    return None
def gid(name):return int('7'+hashlib.sha1(('mahadi:'+name).encode()).hexdigest()[:8],16)
def group(p):
    p=(p or '').upper()
    if 'GK' in p:return 'GK'
    if 'DF' in p or p.startswith('D'):return 'DEF'
    if 'FW' in p or p.startswith('F'):return 'FWD'
    return 'MID'
def norm_league(v):
    s=str(v or '').lower().replace('_',' ').replace('-',' ')
    tests=[('premier','Premier League','premier-league'),('la liga','La Liga','la-liga'),('bundesliga','Bundesliga','bundesliga'),('serie a','Serie A','serie-a'),('ligue 1','Ligue 1','ligue-1')]
    for needle,label,slug in tests:
        if needle in s:return label,slug
    return None,None
def player_row(r):
    name=pick(r,'player','Player','name'); mins=num(r,'playing_time_min','minutes','Min','fb_minutes','fb_min'); xg=num(r,'us_xg','xg','xG'); shots=num(r,'us_shots','shots','Sh','fb_shots'); kp=num(r,'us_key_passes','key_passes','ss_keyPasses','ss_keyPass','ss_key_passes')
    def opt(*keys): return num_opt(r,*keys)
    p={'id':gid(name),'provider':'m-mahadi baseline','name':name,'team':pick(r,'team','Squad','squad'),'position':pick(r,'pos','Pos','position'),'position_group':group(pick(r,'pos','Pos','position')),'minutes':mins,'appearances':num(r,'playing_time_mp','games','MP','fb_matches'),'goals':num(r,'fb_gls','goals','Gls','fb_goals'),'assists':num(r,'fb_ast','assists','Ast','fb_assists'),'xg':xg,'xa':num(r,'us_xa','xa','xA'),'npxg':num(r,'us_np_xg','npxg','npxG'),'shots':shots,'shot_assists':kp,'xg_chain':num(r,'us_xg_chain','xg_chain','xGChain'),'xg_buildup':num(r,'us_xg_buildup','xg_buildup','xGBuildup'),
       'passes':opt('ss_totalPasses','passes'),'passes_completed':opt('ss_accuratePasses','passes_completed'),'carries':opt('carries','ss_successfulDribbles'),
       'pressures':opt('ss_possessionWonAttThird','pressures'),'interceptions':opt('ss_interceptions','interceptions'),'duels':opt('ss_totalDuels','duels'),'recoveries':opt('ss_ballRecovery','ss_ballRecoveries','recoveries'),
       'tackles':opt('ss_tackles','ss_totalTackle','tackles'),'tackles_won':opt('ss_tacklesWon','ss_wonTackle','tackles_won'),'clearances':opt('ss_clearances','ss_clearance','clearances'),'blocks':opt('ss_blockedShots','ss_blockedScoringAttempt','ss_blocks','blocks'),
       'aerial_duels':opt('ss_aerialDuelsTotal','ss_totalAerialDuels','aerial_duels'),'aerial_duels_won':opt('ss_aerialDuelsWon','aerial_duels_won'),'aerial_duels_won_pct':opt('ss_aerialDuelsWonPercentage','aerial_duels_won_pct'),
       'ground_duels':opt('ss_groundDuelsTotal','ss_totalGroundDuels','ground_duels'),'ground_duels_won':opt('ss_groundDuelsWon','ground_duels_won'),'ground_duels_won_pct':opt('ss_groundDuelsWonPercentage','ground_duels_won_pct'),
       'dribbled_past':opt('ss_dribbledPast','dribbled_past'),'errors_shot':opt('ss_errorLeadToShot','errors_shot'),'errors_goal':opt('ss_errorLeadToGoal','errors_goal'),'pass_accuracy_pct':opt('ss_accuratePassesPercentage','pass_accuracy_pct'),
       'final_third_passes':opt('ss_accurateFinalThirdPasses','ss_finalThirdPasses','final_third_passes'),'long_balls':opt('ss_totalLongBalls','ss_longBalls','long_balls'),'long_balls_completed':opt('ss_accurateLongBalls','long_balls_completed'),
       'saves':opt('ss_saves','saves'),'save_pct':opt('ss_savePercentage','save_pct'),'goals_against':opt('ss_goalsConceded','ss_goalsAgainst','goals_against'),'clean_sheets':opt('ss_cleanSheet','ss_cleanSheets','clean_sheets'),
       'psxg':opt('ss_expectedGoalsOnTargetFaced','ss_xgotFaced','psxg'),'goals_prevented':opt('ss_goalsPrevented','goals_prevented'),'claims':opt('ss_goodHighClaim','ss_highClaims','claims'),'punches':opt('ss_punches','punches'),'sweeper_actions':opt('ss_totalKeeperSweeper','ss_sweeperKeeper','ss_successfulRunsOut','sweeper_actions'),
       'top_speed':opt('ss_topSpeed','top_speed'),'distance_covered':opt('ss_kilometersCovered','distance_covered'),'sprints':opt('ss_numberOfSprints','sprints'),'age':opt('age','Age','ss_age'),'nationality':pick(r,'nationality','nation','Nation','country'),'market_value_eur':opt('fifa_value_eur','market_value_eur','value_eur','sofifa_value_eur','sf_value_eur','value'),'market_value_source':('SoFIFA-derived research field' if opt('fifa_value_eur','market_value_eur','value_eur','sofifa_value_eur','sf_value_eur','value') is not None else None),'market_value_date':pick(r,'value_date','sofifa_date','snapshot_date','date'),'events':0}
    volume=['xg','xa','shots','shot_assists','xg_chain','xg_buildup','passes','passes_completed','carries','pressures','interceptions','duels','recoveries','tackles','tackles_won','clearances','blocks','aerial_duels','aerial_duels_won','ground_duels','ground_duels_won','dribbled_past','errors_shot','errors_goal','final_third_passes','long_balls','long_balls_completed','saves','goals_against','clean_sheets','psxg','goals_prevented','claims','punches','sweeper_actions']
    for k in volume:
        v=p.get(k); p[k+'_p90']=round(v*90/mins,3) if mins and v is not None else None
    p['available_metrics']=[k for k,v in p.items() if k not in {'id','provider','name','team','position','position_group','nationality','market_value_source','market_value_date','available_metrics'} and v is not None]
    return p

def write_ds(folder,label,competition,slug,season,rows):
    ds=OUT/folder;ds.mkdir(parents=True,exist_ok=True)
    metrics=['goals','assists','shots','xg','xa','npxg','key_passes','xg_chain','xg_buildup','passes','passes_completed','carries','pressures','interceptions','duels','recoveries','tackles','tackles_won','clearances','blocks','aerial_duels','aerial_duels_won','aerial_duels_won_pct','ground_duels','ground_duels_won','ground_duels_won_pct','dribbled_past','errors_shot','errors_goal','pass_accuracy_pct','final_third_passes','long_balls','long_balls_completed','saves','save_pct','goals_against','clean_sheets','psxg','goals_prevented','claims','punches','sweeper_actions','top_speed','distance_covered','sprints']
    meta={'competition_id':f'mahadi-{slug}','season_id':2025,'competition':competition,'season':season,'label':label,'provider':'m-mahadi research baseline','kind':'normalized','capabilities':{'player_season_stats':True,'xg':True,'xa':True,'advanced_passing':True,'defending':True,'physical':True,'shot_xy':False,'event_xy':False,'pass_xy':False,'pressure_xy':False,'lineups':False,'substitutions':False,'metrics':metrics},'provenance_note':'Merged research dataset with per-column source provenance (FBref, Understat, SofaScore, SoFIFA). Source rights remain with original providers; research/private prototype use unless terms are cleared.'}
    (ds/'dataset.json').write_text(json.dumps(meta,indent=2));(ds/'players_normalized.json').write_text(json.dumps(rows));(ds/'matches_normalized.json').write_text('[]');(ds/'player_maps.json').write_text('{}')
def main():
    ap=argparse.ArgumentParser(description='Import FIL 2025/26 Top-5 player analytics baseline.');ap.add_argument('--source',default=DEFAULT);ap.add_argument('--season',default='2025/26');a=ap.parse_args()
    if a.source.startswith('http'):
        req=urllib.request.Request(a.source,headers={'User-Agent':'FIL/1.0'});raw=urllib.request.urlopen(req,timeout=90).read().decode('utf-8-sig').splitlines()
    else:raw=Path(a.source).read_text(encoding='utf-8-sig').splitlines()
    source=list(csv.DictReader(raw)); selected=[]; by_league=defaultdict(list)
    for r in source:
        season=str(pick(r,'season_label','season') or '')
        if a.season not in season and not (a.season=='2025/26' and ('2025' in season or season=='')):continue
        if not pick(r,'player','Player','name'):continue
        p=player_row(r);selected.append(p)
        label,slug=norm_league(pick(r,'league','League','competition','Comp','comp'))
        if slug:by_league[(label,slug)].append(p)
    write_ds('baseline_top5_2025','Top 5 Leagues · 2025/26','Top 5 Leagues','top5',a.season,selected)
    for (label,slug),rows in by_league.items():write_ds(f'baseline_{slug}_2025',f'{label} · 2025/26',label,slug,a.season,rows)
    extra=', '.join(f'{label}: {len(rows)}' for (label,_),rows in by_league.items())
    print(f'FIL baseline: {len(selected)} player-season rows -> {OUT/"baseline_top5_2025"}')
    if extra:print('League datasets:',extra)
    print('Analytics imported: xG/xA/xGChain/xGBuildup + passing/duels/defending/recoveries + 2025/26 physical fields where source supplies them.')
if __name__=='__main__':main()
