"""Build a compact FIL career/value-history reference from the public transfermarkt-datasets snapshot.
Snapshot is historical/reference data, not a live feed. Downloads only the rows needed for players
already known to FIL's market-value / wonderkid references.
"""
from pathlib import Path
import csv,gzip,json,urllib.request,urllib.error,unicodedata,re,io,time
ROOT=Path(__file__).resolve().parents[1]; REF=ROOT/'data'/'reference'; REF.mkdir(parents=True,exist_ok=True)
BASE='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/'
def key(v): return re.sub(r'[^a-z0-9]+','',unicodedata.normalize('NFKD',str(v or '')).encode('ascii','ignore').decode().lower())
def _open_public(url, retries=3):
    # R2 can reject Python's default urllib user-agent even though the object is public.
    # Send normal HTTP headers and retry transient CDN failures.
    headers={
        'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 FIL/5.0.1',
        'Accept':'text/csv,application/gzip,application/octet-stream,*/*',
        'Accept-Encoding':'identity',
        'Connection':'close',
    }
    last=None
    for attempt in range(1,retries+1):
        try:
            return urllib.request.urlopen(urllib.request.Request(url,headers=headers),timeout=180)
        except urllib.error.HTTPError as e:
            last=e
            # GitHub raw is a useful public fallback for the smaller players file.
            if e.code not in (403,429,500,502,503,504): raise
        except urllib.error.URLError as e:
            last=e
        if attempt<retries: time.sleep(attempt*2)
    raise RuntimeError(f'Could not download {url}: {last}')

def stream(name):
    print('Downloading',name+'...')
    url=BASE+name+'.csv.gz'
    r=_open_public(url)
    try:
        return csv.DictReader(io.TextIOWrapper(gzip.GzipFile(fileobj=r),encoding='utf-8-sig',newline=''))
    except Exception:
        r.close(); raise
known={}
for f in ['market_values.json','global_wonderkids.json']:
    p=REF/f
    if not p.exists(): continue
    d=json.loads(p.read_text(encoding='utf-8'))
    rows=(d.get('values') or {}).values() if f=='market_values.json' else d.get('players',[])
    for x in rows:
        n=x.get('name'); tid=x.get('transfermarkt_player_id') or x.get('player_id')
        if n: known[key(n)]={'name':n,'tid':str(tid) if tid else None}
ids={x['tid'] for x in known.values() if x['tid']}; byid={x['tid']:k for k,x in known.items() if x['tid']}
# Resolve IDs by exact normalised name for references without IDs.
for r in stream('players'):
    k=key(r.get('name'))
    if k in known and not known[k]['tid']:
        known[k]['tid']=str(r.get('player_id')); ids.add(str(r.get('player_id'))); byid[str(r.get('player_id'))]=k
out={k:{'name':x['name'],'valuations':[],'transfers':[],'appearances':{'appearances':0,'minutes':0,'goals':0,'assists':0}} for k,x in known.items() if x['tid']}
for r in stream('player_valuations'):
    pid=str(r.get('player_id')); k=byid.get(pid)
    if not k: continue
    try:v=float(r.get('market_value_in_eur') or 0)
    except:v=0
    out[k]['valuations'].append({'date':r.get('date'),'value':v,'club':r.get('current_club_name')})
for r in stream('transfers'):
    pid=str(r.get('player_id')); k=byid.get(pid)
    if not k: continue
    try:fee=float(r.get('transfer_fee') or 0)
    except:fee=0
    out[k]['transfers'].append({'date':r.get('transfer_date'),'from':r.get('from_club_name'),'to':r.get('to_club_name'),'fee':fee})
for r in stream('appearances'):
    pid=str(r.get('player_id')); k=byid.get(pid)
    if not k: continue
    a=out[k]['appearances'];a['appearances']+=1
    for src,dst in [('minutes_played','minutes'),('goals','goals'),('assists','assists')]:
        try:a[dst]+=int(float(r.get(src) or 0))
        except:pass
for x in out.values():
    x['valuations'].sort(key=lambda z:z.get('date') or '');x['transfers'].sort(key=lambda z:z.get('date') or '')
    x['max_value']=max([z['value'] for z in x['valuations']] or [0])
obj={'source':'dcaribou/transfermarkt-datasets public snapshot','snapshot_note':'Dataset updates paused; historical reference only.','players':out}
(REF/'player_history.json').write_text(json.dumps(obj,separators=(',',':')),encoding='utf-8')
print(f'Installed history for {len(out):,} FIL-known players -> data/reference/player_history.json')
