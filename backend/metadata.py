"""Curated display metadata; never modifies club submissions or the seed catalog."""
import json
from pathlib import Path

METADATA = json.loads(Path(__file__).with_name('school-metadata.json').read_text(encoding='utf-8'))


def enrich_schools(published):
    result = []
    for club in published:
        item = dict(club)
        metadata = METADATA.get(club['id'])
        if metadata and metadata['name'] == club['name']:
            item.update({key: metadata[key] for key in ('en', 'region', 'alias')})
        result.append(item)
    return result
