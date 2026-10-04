"""Restore prebuilt Understat datasets during deployment.

Historical 2025/26 data should be generated deliberately, not scraped on every
Render deploy.  Set FIL_UNDERSTAT_CACHE_URL to a versioned .tar.gz artifact
containing the understat_*_2025 dataset directories.  Without a cache URL the
app still deploys normally; it simply does not perform network-heavy Understat
ingestion.

To rebuild locally/on a controlled job, run scripts/ingest_understat.py with
--shots for each league, archive the resulting directories, publish the
versioned artifact, then update FIL_UNDERSTAT_CACHE_URL.
"""
from pathlib import Path
import io, os, tarfile, urllib.request

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/"data"/"raw"/"statsbomb"/"datasets"
URL=(os.getenv("FIL_UNDERSTAT_CACHE_URL") or "").strip()

if not URL:
    print("[FIL] FIL_UNDERSTAT_CACHE_URL not set; skipping historical Understat restore.")
    print("[FIL] No live Understat crawl will run during this deploy.")
    raise SystemExit(0)

print("[FIL] downloading versioned Understat cache...")
req=urllib.request.Request(URL,headers={"User-Agent":"FootballIntelligenceLab/1.0"})
with urllib.request.urlopen(req,timeout=120) as r:
    payload=r.read()
print(f"[FIL] cache downloaded: {len(payload)/1024/1024:.1f} MB")

DEST.mkdir(parents=True,exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(payload),mode="r:gz") as tf:
    members=[]
    for m in tf.getmembers():
        p=Path(m.name)
        if p.is_absolute() or ".." in p.parts:
            raise RuntimeError(f"unsafe cache member: {m.name}")
        if not any(part.startswith("understat_") and part.endswith("_2025") for part in p.parts):
            continue
        members.append(m)
    if not members:
        raise RuntimeError("cache contains no understat_*_2025 datasets")
    tf.extractall(DEST,members=members,filter="data")
print(f"[FIL] restored {len({next((p for p in Path(m.name).parts if p.startswith('understat_') and p.endswith('_2025')), '') for m in members})} Understat datasets")
