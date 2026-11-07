import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from refresh import associate, antt_url, validate, deduplicate, parse_der


def point(code='DER-1', lat=-23.5):
    return dict(id=code, source='DER', lat=lat, lon=-46.9, description='SP 280 · km 35 · Itapevi',
        scope='O-1', directions='+', segment=10, offset=50.0, car=120, moto=120)


class RefreshTest(unittest.TestCase):
    def test_only_same_equipment_and_position_keeps_voice(self):
        old = point()
        changed = point(lat=-23.501)
        changed.update(segment=None, directions='', offset=0)
        self.assertEqual('', associate([changed], [old])[0]['directions'])
        fresh = point()
        fresh.update(segment=None, directions='', offset=0, car=100, moto=100, scope='Oeste')
        self.assertEqual('+', associate([fresh], [old])[0]['directions'])
        self.assertEqual(100, fresh['car'])

    def test_direction_change_clears_voice(self):
        fresh = point()
        fresh.update(scope='Leste', segment=None, directions='', offset=0)
        self.assertEqual('', associate([fresh], [point()])[0]['directions'])

    def test_partial_source_is_rejected(self):
        with self.assertRaises(ValueError):
            validate([], [point()])

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            validate([point(), point()], [point(), point('DER-2')])

    def test_month_selection_uses_numeric_year_and_month(self):
        resources = [{'url':'https://dados.antt.gov.br/download/dados-dos-radares'+m+'.csv'} for m in ('9_2026','10_2026','1_2027')]
        self.assertTrue(antt_url({'result':{'resources':resources}}).endswith('1_2027.csv'))

    def test_opposite_carriageway_is_not_deduplicated(self):
        artesp = point('ARTESP-1')
        artesp.update(source='ARTESP', directions='-')
        self.assertEqual(2, len(deduplicate([point(), artesp])))
        artesp['directions'] = '+'
        self.assertEqual(1, len(deduplicate([point(), artesp])))

    def test_der_retains_reviewed_position_and_updates_explicit_limit(self):
        row = dict(TipoRadar='Controlador', rad_ativo='Ativo', trc_em_operacao='true',
            rad_dt_cadastro='2026-01-01', tra_numero_der='1', Latitude='-23.5001', Longitude='-46.9',
            Rodovia='SP 280', KmInicial='35', Municipio='Itapevi', Orientacao='Oeste', rad_vel_regul='100/080 km/h')
        fresh = parse_der([row], [point()], '2026-11-07')
        self.assertEqual(-23.5, fresh[0]['lat'])
        self.assertEqual(100, fresh[0]['car'])
        self.assertEqual('+', associate(fresh, [point()])[0]['directions'])
        row['Latitude'] = '-21.0'
        review = []
        self.assertEqual(point(), parse_der([row], [point()], '2026-11-07', review)[0])
        self.assertEqual(1, len(review))

    def test_absence_does_not_mean_deactivation(self):
        self.assertEqual([point()], parse_der([], [point()], '2026-11-07'))

    def test_der_explicit_deactivation_removes_only_matching_point(self):
        row = dict(TipoRadar='Controlador', rad_ativo='Inativo', trc_em_operacao='false',
            tra_numero_der='1', Latitude='-23.5', Longitude='-46.9', Rodovia='SP 280', KmInicial='35', Orientacao='Oeste')
        self.assertEqual([], parse_der([row], [point()], '2026-11-07'))

    def test_deduplication_keeps_the_confirmed_voice_association(self):
        other = point('ARTESP-1')
        other['source'] = 'ARTESP'
        der = point()
        der.update(segment=None, directions='', offset=0)
        result = deduplicate([der, other])
        self.assertEqual(['ARTESP-1'], [p['id'] for p in result])


if __name__ == '__main__':
    unittest.main()
