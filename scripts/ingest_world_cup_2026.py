from pathlib import Path
import urllib.request, urllib.error, time
ROOT=Path(__file__).resolve().parents[1]/'data'/'worldcup2026'; ROOT.mkdir(parents=True,exist_ok=True)
BASE='https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/'
FILES=['worldcup.json','worldcup.squads.json','worldcup-full.json']
HEADERS={'User-Agent':'Football-Intelligence-Lab/7.0 (+local research app)','Accept':'application/json,text/plain,*/*'}
for name in FILES:
    print('Downloading',name+'...')
    err=None
    for attempt in range(4):
        try:
            req=urllib.request.Request(BASE+name,headers=HEADERS)
            with urllib.request.urlopen(req,timeout=120) as r:
                data=r.read()
            if len(data)<100: raise RuntimeError('download was unexpectedly small')
            (ROOT/name).write_bytes(data); print('  installed',f'{len(data):,}','bytes'); err=None; break
        except Exception as e:
            err=e; time.sleep(1.5*(attempt+1))
    if err:
        if name=='worldcup-full.json': print('  optional full-match file unavailable:',err); continue
        raise SystemExit(f'Could not download {name}: {err}')
print('World Cup 2026 intelligence installed. Restart/refresh FIL.')
