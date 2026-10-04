from __future__ import annotations
import csv,gzip,io,json,re,unicodedata,urllib.request
from datetime import date,datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REF=ROOT/'data'/'reference'; REF.mkdir(parents=True,exist_ok=True)
URL='https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/data/players.csv.gz'
SNAPSHOT='2026-06-12'
TODAY=date(2026,10,2)

def norm(s):
 s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
 return re.sub(r'[^a-z0-9]+','',s)
def num(v):
 try:return int(float(str(v or 0).replace(',','')))
 except:return 0
def age_from(d):
 try:
  b=datetime.strptime(str(d)[:10],'%Y-%m-%d').date(); return TODAY.year-b.year-((TODAY.month,TODAY.day)<(b.month,b.day))
 except:return None
def pos_group(p,sub=''):
 s=(str(p)+' '+str(sub)).lower()
 if 'goal' in s:return 'GK'
 if any(x in s for x in ('defender','back','centre-back','center-back')):return 'DEF'
 if any(x in s for x in ('midfield','midfielder')):return 'MID'
 return 'FWD'

def main():
 req=urllib.request.Request(URL,headers={'User-Agent':'Football-Intelligence-Lab/2.9 (+local research)','Accept-Encoding':'identity'})
 with urllib.request.urlopen(req,timeout=120) as r: raw=r.read()
 if raw[:2]==b'\x1f\x8b':raw=gzip.decompress(raw)
 rows=[]
 for r in csv.DictReader(io.StringIO(raw.decode('utf-8-sig',errors='replace'))):
  name=r.get('name') or r.get('player_name'); dob=r.get('date_of_birth'); age=age_from(dob)
  if not name or age is None or age<14 or age>19:continue
  value=num(r.get('market_value_in_eur') or r.get('market_value_eur'))
  # A global scouting universe, not a claim that every teenager is an elite prospect.
  rows.append({'name':name,'key':norm(name),'date_of_birth':dob,'age':age,
   'team':r.get('current_club_name') or r.get('club_name'),'nationality':r.get('country_of_citizenship') or r.get('country'),
   'position':r.get('sub_position') or r.get('position'),'position_group':pos_group(r.get('position'),r.get('sub_position')),
   'market_value_eur':value or None,'market_value_source':'Transfermarkt dataset snapshot' if value else None,
   'market_value_date':SNAPSHOT if value else None,'transfermarkt_player_id':r.get('player_id'),
   'source':'Transfermarkt-derived global player register','source_date':SNAPSHOT})
 rows.sort(key=lambda x:(x.get('market_value_eur') or 0,-x['age']),reverse=True)
 payload={'generated_for':'FIL v2.9.0','snapshot_date':SNAPSHOT,'method':'Global age-filtered player universe. Inclusion is not a FIL potential rating. Market values are historical sourced reference values, not transfer fees.','players':rows}
 (REF/'global_wonderkids.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
 print(f'Global youth universe: {len(rows):,} players aged 14-19')
 print(f'With sourced market value: {sum(bool(x.get("market_value_eur")) for x in rows):,}')
 print(f'Countries: {len({x.get("nationality") for x in rows if x.get("nationality")}):,}')
 print(f'Clubs: {len({x.get("team") for x in rows if x.get("team")}):,}')
 print(f'Wrote {REF/"global_wonderkids.json"}')
if __name__=='__main__':main()
