"""Cheap, conservative invalidation across data edits and screening revisions.

Versions are global per dataset: an edit anywhere may invalidate more analyses
than strictly necessary. This is intentional until per-municipality provenance is
needed; no same-count edit or road/SVI refresh can silently reuse old results.
"""
import hashlib
import json

from sqlalchemy import text

from app.config import settings
from app.services.methodology import METHOD_VERSION, MIN_ANALYSIS_LENGTH_MILES, CORRIDOR_ENDPOINT_TOLERANCE_METERS


def current_input_version(db):
    revisions = dict(db.execute(text(
        'SELECT dataset, revision FROM dataset_revisions ORDER BY dataset'
    )).all())
    if set(revisions) != {'crashes', 'roads', 'boundaries', 'svi'}:
        raise RuntimeError('Dataset version registry is incomplete; run schema initialization')
    method = {
        'version': METHOD_VERSION,
        'weights': settings.severity_weights,
        'minimum_segment_miles': MIN_ANALYSIS_LENGTH_MILES,
        'corridor_endpoint_tolerance_meters': CORRIDOR_ENDPOINT_TOLERANCE_METERS,
    }
    revisions['method'] = hashlib.sha256(
        json.dumps(method, sort_keys=True).encode()
    ).hexdigest()
    return revisions
