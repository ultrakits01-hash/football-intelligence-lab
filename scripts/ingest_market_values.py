from __future__ import annotations
import argparse,csv,gzip,io,json,re,unicodedata,urllib.request
from difflib import SequenceMatcher
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATASETS=ROOT/'data'/'raw'/'statsbomb'/'datasets'
REF=ROOT/'data'/'reference'
PLAYERS_URL='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/players.csv.gz'
SNAPSHOT_DATE='2026-06-12'

def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+','',s)

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Football-Intelligence-Lab/2.5 (+local research)','Accept-Encoding':'identity'})
    with urllib.request.urlopen(req,timeout=120) as r: raw=r.read()
    if raw[:2]==b'\x1f\x8b': raw=gzip.decompress(raw)
    return raw.decode('utf-8-sig',errors='replace')

def fnum(v):
    try:return int(float(str(v or 0).replace(',','')))
    except:return 0

def installed_players():
    out=[]
    if not DATASETS.exists(): return out
    for ds in DATASETS.iterdir():
        p=ds/'players_normalized.json'; m=ds/'dataset.json'
        if not p.exists() or not m.exists(): continue
        try:
            meta=json.loads(m.read_text(encoding='utf-8')); season=str(meta.get('season') or '')
            if not (season.startswith('2025') or season.startswith('2026')): continue
            for x in json.loads(p.read_text(encoding='utf-8')):
                if x.get('name'): out.append({'name':x['name'],'team':x.get('team'),'dataset_id':ds.name})
        except Exception: pass
    return out

def main():
    ap=argparse.ArgumentParser(description='Build FIL market-value reference from the public transfermarkt-datasets snapshot.')
    ap.add_argument('--source',default=PLAYERS_URL,help='players.csv.gz URL or local file')
    a=ap.parse_args()
    if a.source.startswith('http'): text=fetch(a.source)
    else:
        raw=Path(a.source).read_bytes(); raw=gzip.decompress(raw) if raw[:2]==b'\x1f\x8b' else raw; text=raw.decode('utf-8-sig',errors='replace')
    rows=list(csv.DictReader(io.StringIO(text)))
    vals=[]
    for r in rows:
        name=r.get('name') or r.get('player_name')
        value=fnum(r.get('market_value_in_eur') or r.get('market_value_eur'))
        if not name or not value: continue
        vals.append({'name':name,'key':norm(name),'market_value_eur':value,'market_value_source':'Transfermarkt dataset snapshot','market_value_date':r.get('last_season') and SNAPSHOT_DATE or SNAPSHOT_DATE,'transfermarkt_player_id':r.get('player_id'),'team':r.get('current_club_name') or r.get('club_name'),'age':r.get('age'),'nationality':r.get('country_of_citizenship') or r.get('country')})
    by={}
    for v in vals: by.setdefault(v['key'],[]).append(v)
    targets=installed_players(); matched={}; misses=[]
    for t in targets:
        k=norm(t['name']); cand=by.get(k,[])
        chosen=None
        if len(cand)==1: chosen=cand[0]
        elif len(cand)>1:
            tk=norm(t.get('team'))
            chosen=max(cand,key=lambda v:SequenceMatcher(None,tk,norm(v.get('team'))).ratio())
        else:
            # Conservative fuzzy fallback: only accept an extremely close unique name.
            close=[]
            for key,items in by.items():
                if abs(len(key)-len(k))>2 or not key or not k or key[0]!=k[0]: continue
                s=SequenceMatcher(None,k,key).ratio()
                if s>=.94: close.append((s,items))
            close.sort(key=lambda z:z[0],reverse=True)
            if close and (len(close)==1 or close[0][0]-close[1][0]>=.03): chosen=close[0][1][0]
        if chosen: matched[k]=chosen
        else: misses.append(t['name'])
    REF.mkdir(parents=True,exist_ok=True)
    payload={'source':'dcaribou/transfermarkt-datasets (Transfermarkt-derived public research dataset)','source_url':'https://github.com/dcaribou/transfermarkt-datasets','snapshot_date':SNAPSHOT_DATE,'note':'Latest available snapshot is historical. Display as sourced market value, not a transfer fee or FIL estimate.','values':matched}
    (REF/'market_values.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    unique_targets=len({norm(t['name']) for t in targets}); unique_misses=len({norm(x) for x in misses})
    print(f'FIL market values: {len(matched)}/{unique_targets} installed player names matched ({(100*len(matched)/unique_targets if unique_targets else 0):.1f}% coverage).')
    print(f'Wrote {REF/"market_values.json"}')
    if unique_misses: print(f'Unmatched: {unique_misses}. No value was invented for these players.')

if __name__=='__main__': main()
