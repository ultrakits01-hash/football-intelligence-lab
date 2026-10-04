#!/usr/bin/env python3
"""FIL Facepack v3.

Sources:
  1) Wikimedia Commons: only files whose machine-readable licence is accepted.
  2) Local renders: copy PNG/JPG/WebP files the user has permission to use.

Examples:
  python scripts/ingest_player_images.py --from-installed --limit 250
  python scripts/ingest_player_images.py --names "Lamine Yamal" "Jude Bellingham"
  python scripts/ingest_player_images.py --folder "C:\\Games\\FIL Facepack"
"""
from __future__ import annotations
import argparse,csv,html,json,random,re,shutil,sqlite3,time,unicodedata,urllib.error,urllib.parse,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'apps/web/player-images'; MAN=OUT/'manifest.json'; OUT.mkdir(parents=True,exist_ok=True)
UA='Football-Intelligence-Lab/1.0.2 (local football research client; Wikimedia Commons attribution preserved)'
CACHE=ROOT/'data/cache/commons-facepack-v3.json'; CACHE.parent.mkdir(parents=True,exist_ok=True)
LAST_REQUEST=0.0; REQUEST_GAP=1.15
ALLOW=('cc by','cc-by','cc0','public domain','public-domain','pd-old','pd-self','cc by-sa','cc-by-sa')
DENY=('noncommercial','non-commercial','fair use','all rights reserved','copyrighted')
IMG_EXT={'.png','.jpg','.jpeg','.webp'}

def clean_html(v): return html.unescape(re.sub('<[^>]+>','',str(v or ''))).strip()
def norm(s): return re.sub(r'[^a-z0-9]+',' ',unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()).strip()
def slug(s): return re.sub(r'[^a-z0-9]+','-',norm(s)).strip('-')
def _pace(extra=0.0):
    global LAST_REQUEST
    wait=max(0.0, REQUEST_GAP-(time.monotonic()-LAST_REQUEST))+max(0.0,extra)
    if wait: time.sleep(wait)
    LAST_REQUEST=time.monotonic()

def api(params,retries=7):
    u='https://commons.wikimedia.org/w/api.php?'+urllib.parse.urlencode(params)
    last=None
    for attempt in range(retries):
        try:
            _pace(random.uniform(.05,.30))
            req=urllib.request.Request(u,headers={'User-Agent':UA,'Api-User-Agent':UA,'Accept':'application/json'})
            with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
        except urllib.error.HTTPError as e:
            last=e
            if e.code==429:
                ra=e.headers.get('Retry-After')
                try: wait=float(ra) if ra else min(90.0,5.0*(2**attempt))
                except Exception: wait=min(90.0,5.0*(2**attempt))
                wait+=random.uniform(.5,2.0)
                print(f'    Wikimedia rate limit; cooling down {wait:.1f}s (attempt {attempt+1}/{retries})')
                time.sleep(wait)
                continue
            if 500<=e.code<600:
                time.sleep(min(30.0,2.0*(2**attempt))+random.random())
                continue
            raise
        except Exception as e:
            last=e; time.sleep(min(20.0,1.5*(2**attempt))+random.random())
    raise last

def load_cache():
    try:return json.loads(CACHE.read_text(encoding='utf8')) if CACHE.exists() else {}
    except Exception:return {}

def save_cache(c):
    try:CACHE.write_text(json.dumps(c,indent=2,ensure_ascii=False),encoding='utf8')
    except Exception:pass

def licence_ok(em):
    vals=' '.join(clean_html(em.get(k,{}).get('value')) for k in ('LicenseShortName','UsageTerms','Copyrighted','Restrictions')).lower()
    return any(x in vals for x in ALLOW) and not any(x in vals for x in DENY)

def candidate_score(name,title,desc=''):
    nt=norm(name); hay=norm(title+' '+desc); toks=[t for t in nt.split() if len(t)>1]
    score=sum(5 for t in toks if re.search(rf'\b{re.escape(t)}\b',hay))
    if nt and nt in hay: score+=18
    for good in ('football','soccer','player','world cup','uefa','fifa'): score+=2 if good in hay else 0
    for bad in ('team photo','squad','stadium','logo','signature','autograph','kit','shirt'): score-=4 if bad in hay else 0
    return score

def _search_remote(name):
    # Search namespace 6 directly, then fetch rich metadata only for the best handful.
    queries=[f'intitle:"{name}" football',f'"{name}" football player',name]
    found={}
    for q in queries:
        d=api({'action':'query','list':'search','srsearch':q,'srnamespace':6,'srlimit':12,'format':'json','utf8':1})
        for x in d.get('query',{}).get('search',[]): found[x['title']]=max(found.get(x['title'],-999),candidate_score(name,x['title'],x.get('snippet','')))
        if len(found)>=8: break
    if not found:return None
    titles=[t for t,_ in sorted(found.items(),key=lambda kv:kv[1],reverse=True)[:8]]
    d=api({'action':'query','titles':'|'.join(titles),'prop':'imageinfo','iiprop':'url|mime|dimensions|extmetadata','iiurlwidth':1000,'iiextmetadatafilter':'LicenseShortName|UsageTerms|Copyrighted|Artist|Credit|ImageDescription|AttributionRequired','format':'json','utf8':1})
    candidates=[]
    for p in (d.get('query',{}).get('pages',{}) or {}).values():
        ii=(p.get('imageinfo') or [{}])[0]; em=ii.get('extmetadata') or {}
        if not licence_ok(em):continue
        title=p.get('title',''); desc=clean_html(em.get('ImageDescription',{}).get('value'))
        sc=candidate_score(name,title,desc)
        if sc<7:continue
        url=ii.get('thumburl') or ii.get('url')
        if not url:continue
        lic=clean_html(em.get('LicenseShortName',{}).get('value')) or clean_html(em.get('UsageTerms',{}).get('value'))
        candidates.append((sc,{'url':url,'page':'https://commons.wikimedia.org/wiki/'+urllib.parse.quote(title.replace(' ','_')),'author':clean_html(em.get('Artist',{}).get('value')),'license':lic,'title':title}))
    return max(candidates,key=lambda x:x[0])[1] if candidates else None

def search(name,cache):
    key=norm(name)
    if key in cache:
        return cache[key] or None
    hit=_search_remote(name)
    cache[key]=hit or False
    save_cache(cache)
    return hit

def extract_names_json(obj,out):
    if isinstance(obj,dict):
        for k,v in obj.items():
            if k.lower() in {'name','player_name','playername'} and isinstance(v,str) and 2<len(v)<80: out.append(v)
            else: extract_names_json(v,out)
    elif isinstance(obj,list):
        for v in obj: extract_names_json(v,out)

def installed_names():
    names=[]; data=ROOT/'data'
    if not data.exists():return names
    for db in list(data.rglob('*.db'))+list(data.rglob('*.sqlite'))+list(data.rglob('*.sqlite3')):
        try:
            con=sqlite3.connect(db); tabs={r[0] for r in con.execute("select name from sqlite_master where type='table'")}
            for t in tabs:
                cols={r[1] for r in con.execute(f'pragma table_info("{t}")')}
                for c in ('name','player_name','playerName'):
                    if c in cols:
                        names += [r[0] for r in con.execute(f'select distinct "{c}" from "{t}" where "{c}" is not null') if isinstance(r[0],str)]
                        break
            con.close()
        except Exception: pass
    for fp in data.rglob('*.csv'):
        try:
            with fp.open(encoding='utf-8-sig',errors='ignore',newline='') as f:
                rd=csv.DictReader(f); fields=rd.fieldnames or []; col=next((c for c in fields if c.lower() in {'name','player_name','playername'}),None)
                if col:
                    for i,row in enumerate(rd):
                        if row.get(col):names.append(row[col])
                        if i>15000:break
        except Exception: pass
    for fp in data.rglob('*.json'):
        try:
            if fp.stat().st_size>35_000_000:continue
            extract_names_json(json.loads(fp.read_text(encoding='utf-8',errors='ignore')),names)
        except Exception: pass
    return names

def load_manifest():
    try:return json.loads(MAN.read_text(encoding='utf8')) if MAN.exists() else {}
    except Exception:return {}

def import_folder(folder,manifest):
    folder=Path(folder); added=0
    if not folder.exists():print(f'Local facepack folder not found: {folder}');return 0
    existing={norm(k):k for k in manifest}
    for fp in folder.rglob('*'):
        if not fp.is_file() or fp.suffix.lower() not in IMG_EXT:continue
        name=re.sub(r'[_-]+',' ',fp.stem).strip(); key=existing.get(norm(name),name)
        dest=OUT/(slug(key)+fp.suffix.lower().replace('.jpeg','.jpg'))
        shutil.copy2(fp,dest); manifest[key]={'src':'/static/player-images/'+dest.name,'source':'Local user-supplied facepack','license':'User responsible for usage rights'}
        existing[norm(key)]=key;added+=1;print(f'[local] + {key}')
    return added

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--names',nargs='*',default=[]);ap.add_argument('--from-installed',action='store_true');ap.add_argument('--folder');ap.add_argument('--limit',type=int,default=250);ap.add_argument('--delay',type=float,default=1.25,help='extra pause after each successful player download');ap.add_argument('--refresh-cache',action='store_true');a=ap.parse_args()
    manifest=load_manifest(); cache={} if a.refresh_cache else load_cache(); local=import_folder(a.folder,manifest) if a.folder else 0
    names=list(a.names)
    if a.from_installed:names+=installed_names()
    seen=set(); names=[n.strip() for n in names if isinstance(n,str) and n.strip() and not (norm(n) in seen or seen.add(norm(n)))]
    # Existing images do not consume the requested download limit.
    pending=[n for n in names if norm(n) not in {norm(k) for k in manifest}][:a.limit]
    print(f'Discovered {len(names)} unique player names; {len(pending)} queued; {len(manifest)} already in manifest.')
    ok=nohit=failed=0
    for i,name in enumerate(pending,1):
        try:
            hit=search(name,cache)
            if not hit: nohit+=1; print(f'[{i}/{len(pending)}] - no suitable licensed match: {name}');continue
            path=urllib.parse.urlparse(hit['url']).path.lower(); ext='.png' if path.endswith('.png') else '.webp' if path.endswith('.webp') else '.jpg'; fn=slug(name)+ext
            data=None; last=None
            for attempt in range(6):
                try:
                    _pace(random.uniform(.10,.35))
                    req=urllib.request.Request(hit['url'],headers={'User-Agent':UA,'Referer':'https://commons.wikimedia.org/'})
                    with urllib.request.urlopen(req,timeout=40) as r:data=r.read()
                    break
                except urllib.error.HTTPError as e:
                    last=e
                    if e.code==429:
                        ra=e.headers.get('Retry-After')
                        try: wait=float(ra) if ra else min(90.0,6.0*(2**attempt))
                        except Exception: wait=min(90.0,6.0*(2**attempt))
                        wait+=random.uniform(.5,2.0); print(f'    image rate limit; cooling down {wait:.1f}s'); time.sleep(wait); continue
                    raise
                except Exception as e:
                    last=e; time.sleep(min(20.0,2.0*(2**attempt))+random.random())
            if data is None: raise last or RuntimeError('image download failed')
            (OUT/fn).write_bytes(data)
            manifest[name]={'src':'/static/player-images/'+fn,'source':hit['page'],'author':hit['author'],'license':hit['license'],'commons_title':hit['title']}
            ok+=1; print(f'[{i}/{len(pending)}] + {name} · {hit["license"]} · {hit["title"]}')
            MAN.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8'); time.sleep(max(0,a.delay))
        except Exception as e: failed+=1; print(f'[{i}/{len(pending)}] ! {name}: {type(e).__name__}: {e}')
    MAN.write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf8')
    print(f'Done: {ok} Commons + {local} local images added; {nohit} no-match; {failed} failed; {len(manifest)} total in manifest; {len(cache)} Commons searches cached.')
if __name__=='__main__':main()
