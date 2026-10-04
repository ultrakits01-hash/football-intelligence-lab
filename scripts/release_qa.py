from fastapi.testclient import TestClient
from apps.api.app.main import app, FIL_VERSION
import math
c=TestClient(app)
checks=[]
def check(name, fn):
    try: fn(); checks.append((name,'PASS'))
    except Exception as e: checks.append((name,'FAIL '+repr(e)))
check('health',lambda: (_ for _ in ()).throw(AssertionError()) if c.get('/health').status_code!=200 else None)
check('transfer endpoint isolated',lambda: (_ for _ in ()).throw(AssertionError()) if c.get('/live-transfers').status_code!=200 else None)
check('catalogue',lambda: (_ for _ in ()).throw(AssertionError()) if c.get('/dataset-catalogue').status_code!=200 else None)
check('release audit',lambda: (_ for _ in ()).throw(AssertionError()) if c.get('/release-audit').status_code!=200 else None)
check('version',lambda: (_ for _ in ()).throw(AssertionError(FIL_VERSION)) if FIL_VERSION!='1.6.0-final-rc' else None)
for n,r in checks: print(f'{n}: {r}')
if any(r.startswith('FAIL') for _,r in checks): raise SystemExit(1)
