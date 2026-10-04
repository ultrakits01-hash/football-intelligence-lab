import argparse, json, shutil, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; BASE='https://raw.githubusercontent.com/hudl/open-data/master/data'; DATA_ROOT=ROOT/'data/raw/statsbomb'
PRESETS={
 'bundesliga-2324':(9,281,'Bundesliga','2023/2024'),
 'ligue1-2223':(7,235,'Ligue 1','2022/2023'),
 'mls-2023':(44,107,'Major League Soccer','2023'),
 'premier-league-1516':(2,27,'Premier League','2015/2016'),
}
def get(url,p):
 p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists(): return
 print('GET',url); urllib.request.urlretrieve(url,p)
def ingest(key):
 if key not in PRESETS: raise SystemExit(f"Unknown dataset {key}. Choose: {', '.join(PRESETS)}")
 comp,season,name,season_name=PRESETS[key]; out=DATA_ROOT/'datasets'/f'{comp}_{season}'
 get(f'{BASE}/matches/{comp}/{season}.json',out/'matches.json'); matches=json.load(open(out/'matches.json',encoding='utf8'))
 (out/'dataset.json').write_text(json.dumps({'competition_id':comp,'season_id':season,'competition':name,'season':season_name,'label':f"{name} · {season_name.replace("/20", "/")[2:] if "/" in season_name else season_name}"},indent=2),encoding='utf8')
 for i,m in enumerate(matches,1):
  mid=m['match_id']; print(f'[{i}/{len(matches)}] {name} {season_name} · {mid}')
  get(f'{BASE}/events/{mid}.json',out/'events'/f'{mid}.json'); get(f'{BASE}/lineups/{mid}.json',out/'lineups'/f'{mid}.json')
  try:get(f'{BASE}/three-sixty/{mid}.json',out/'three-sixty'/f'{mid}.json')
  except Exception: pass
 print(f'DONE: {name} {season_name} · {len(matches)} matches -> {out}')
def main():
 ap=argparse.ArgumentParser(description='Install a StatsBomb Open Data competition/season into FIL v0.9')
 ap.add_argument('dataset',nargs='?',default='bundesliga-2324',choices=PRESETS); args=ap.parse_args(); ingest(args.dataset)
if __name__=='__main__': main()
