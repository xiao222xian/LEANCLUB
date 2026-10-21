import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from metadata import METADATA, enrich_schools


class MetadataTests(unittest.TestCase):
    def test_metadata_preserves_submissions_and_does_not_resurrect_records(self):
        school_id, metadata = next(iter(METADATA.items()))
        original = {'id': school_id, 'name': metadata['name'], 'count': 42, 'updated': 1}
        result = enrich_schools([original])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['en'], metadata['en'])
        self.assertEqual({k: result[0][k] for k in original}, original)
        self.assertNotIn('en', original)
        self.assertEqual(enrich_schools([]), [])

    def test_unknown_or_renamed_school_is_not_misidentified(self):
        school_id = next(iter(METADATA))
        records = [{'id': 'new-school', 'name': '新学校'}, {'id': school_id, 'name': '其他学校'}]
        self.assertEqual(enrich_schools(records), records)

    def test_curated_records_have_required_metadata(self):
        self.assertEqual(len(METADATA), 51)
        self.assertTrue(all(all(m.get(k) for k in ('name', 'en', 'region')) for m in METADATA.values()))
