"""Restore FIL World Cup 2026 files from OpenFootball's public-domain dataset."""
from pathlib import Path
import json, urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"worldcup2026"; OUT.mkdir(parents=True,exist_ok=True)
BASE="https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/"
FILES={"worldcup.json":"worldcup.json","worldcup.squads.json":"worldcup.squads.json","worldcup-full.json":"worldcup-full.json"}
for local,remote in FILES.items():
    url=BASE+remote
    print(f"[FIL] World Cup 2026: {remote}")
    req=urllib.request.Request(url,headers={"User-Agent":"FootballIntelligenceLab/1.0"})
    with urllib.request.urlopen(req,timeout=60) as r: raw=r.read()
    json.loads(raw.decode("utf-8-sig"))
    (OUT/local).write_bytes(raw)
    print(f"[FIL]   {local}: {len(raw)/1024:.1f} KB")
print("[FIL] World Cup 2026 public-domain files ready")
