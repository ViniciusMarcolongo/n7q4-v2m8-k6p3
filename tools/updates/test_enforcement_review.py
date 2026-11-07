import copy
import datetime as dt
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from enforcement_review import IDENTITY, apply_reviews


class EnforcementReviewTest(unittest.TestCase):
    def setUp(self):
        self.point=dict(id='DER-test',source='DER',lat=-23.5,lon=-46.9,description='SP 280 km 35',scope='Oeste',segment=10,directions='+',offset=5,car=120,moto=120)
        self.evidence=dict(vehicles='CAR_ONLY',capture='FRONT',sourceName='Fonte de teste',sourceUrl='https://example.org/test-only',checkedOn='2026-11-07',note='Synthetic test evidence')
        self.ledger=dict(schema=1,reviews=[dict(identity={k:self.point[k] for k in IDENTITY},enforcement=self.evidence)])

    def test_exact_review_survives_daily_refresh_without_renewing_date(self):
        result=apply_reviews([self.point],self.ledger,'2026-11-08')[0]
        self.assertEqual(self.evidence,result['enforcement'])
        self.assertNotIn('enforcement',self.point)

    def test_changes_to_site_or_direction_remove_exclusion(self):
        for key,value in [('lat',-23.501),('directions','-'),('scope','Leste'),('segment',12),('description','SP 280 km 36')]:
            point=dict(self.point,**{key:value},enforcement=self.evidence)
            self.assertNotIn('enforcement',apply_reviews([point],self.ledger,'2026-11-07')[0])

    def test_unknown_capture_does_not_create_vehicle_exclusion(self):
        self.evidence.update(vehicles='UNKNOWN',capture='FRONT')
        self.assertEqual('UNKNOWN',apply_reviews([self.point],self.ledger,'2026-11-07')[0]['enforcement']['vehicles'])

    def test_expired_future_or_removed_review_cannot_suppress_motorcycle(self):
        for today in ['2026-11-06','2027-05-07']:
            value=apply_reviews([self.point],self.ledger,today)[0]['enforcement']
            self.assertEqual('UNKNOWN',value['vehicles'])
            self.assertEqual('UNKNOWN',value['capture'])
        self.assertNotIn('enforcement',apply_reviews([dict(self.point,enforcement=self.evidence)],dict(schema=1,reviews=[]),'2026-11-07')[0])

    def test_duplicate_or_unattributed_claim_is_rejected(self):
        bad=copy.deepcopy(self.ledger); bad['reviews'].append(bad['reviews'][0])
        with self.assertRaises(ValueError): apply_reviews([self.point],bad,'2026-11-07')
        self.evidence['sourceUrl']='http://example.org'
        with self.assertRaises(ValueError): apply_reviews([self.point],self.ledger,'2026-11-07')

    def test_user_provenance_survives_refresh_and_unknown_origin_is_rejected(self):
        self.evidence['reviewSource'] = 'USER'
        value = apply_reviews([self.point], self.ledger, '2026-11-08')[0]['enforcement']
        self.assertEqual('USER', value['reviewSource'])
        self.assertEqual('2026-11-07', value['checkedOn'])
        self.evidence['reviewSource'] = 'UNATTRIBUTED'
        with self.assertRaises(ValueError): apply_reviews([self.point],self.ledger,'2026-11-07')
