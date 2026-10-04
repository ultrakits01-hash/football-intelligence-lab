from __future__ import annotations
import argparse, subprocess, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LEAGUES=['premier-league','la-liga','bundesliga','serie-a','ligue-1']

def main():
    ap=argparse.ArgumentParser(description='Build a complete FIL Big-5 season: players, fixtures/results, team analytics, player-match output and shot XY.')
    ap.add_argument('season',type=int,help='season start year, e.g. 2025 = 2025/26')
    ap.add_argument('--no-shots',action='store_true',help='skip match-detail/shot requests')
    a=ap.parse_args()
    failures=[]
    for i,league in enumerate(LEAGUES,1):
        print(f'\n=== FIL BIG 5 {i}/5 · {league} · {a.season}/{str(a.season+1)[-2:]} ===')
        cmd=[sys.executable,str(ROOT/'scripts'/'ingest_understat.py'),league,str(a.season)]
        if not a.no_shots: cmd.append('--shots')
        r=subprocess.run(cmd,cwd=ROOT)
        if r.returncode: failures.append(league)
    if failures:
        raise SystemExit('Big-5 import incomplete: '+', '.join(failures)+'. Re-run the command; completed league folders are safe to refresh.')
    print(f'\nFIL Big-5 {a.season}/{str(a.season+1)[-2:]} complete. All five leagues are now available in the dataset selector.')
if __name__=='__main__': main()
