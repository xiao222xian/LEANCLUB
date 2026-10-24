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
            for key in ('en', 'region', 'alias'):
                if item.get(key) is None:
                    item[key] = metadata[key]
        result.append(item)
    return result
