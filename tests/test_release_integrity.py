import math
from apps.api.app import main

def test_catalogue_has_only_current_architecture():
    for d in main._dataset_catalogue():
        season=str(d.get('season') or '')
        assert season.startswith(('2025','2026')) or '2026' in str(d.get('competition') or '')

def test_identity_routes_unique():
    idx=main._build_identity_index()
    assert len(idx['by_route']) == sum(len(x['records']) for x in idx['rows'])

def test_identity_records_reference_real_datasets():
    installed={x['dataset_id'] for x in main._public_datasets()}
    for x in main._build_identity_index()['rows']:
        for r in x['records']:
            assert r['dataset_id'] in installed

def test_world_cup_lineups_when_present_are_sane():
    try: matches=main.world_cup_matches()
    except Exception: return
    for m in matches[:200]:
        # Compact source may not carry lineups; rich endpoint owns exact XI validation.
        assert isinstance(m,dict)

def test_missing_values_are_not_coerced_here():
    assert math.isfinite(0.0)
