from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from feature_combinations import FEATURES, blocks, fit, evaluate, search, score, replay_candidate


def row(first,second=0):
    return dict.fromkeys(FEATURES,0) | {'band_60_120':first,'autocorr_periodicity':second}


class CombinationTests(unittest.TestCase):
    def test_two_features_can_separate_classes_that_overlap_in_either_feature(self):
        positive={'motor':[row(1,-1),row(3,1)]*20}
        negative={'off':[row(-1,1),row(1,3)]*20}
        alone=fit(positive,negative,['band_60_120'])
        combined=fit(positive,negative,['band_60_120','autocorr_periodicity'])
        self.assertLess(evaluate(alone,positive,negative)['balanced_accuracy'],1)
        self.assertEqual(evaluate(combined,positive,negative)['balanced_accuracy'],1)

    def test_test_block_does_not_change_fitting_or_selection(self):
        positive={'motor':[row(1)]*24+[row(0)]*6}
        negative={'off':[row(0)]*30}
        result=search(positive,negative)
        self.assertEqual(result['candidate_count'],298)
        winner=result['winners_by_size'][0]
        self.assertEqual(winner['model']['features'],['band_60_120'])
        self.assertEqual(winner['selection']['balanced_accuracy'],1)
        self.assertEqual(winner['test']['balanced_accuracy'],.5)

    def test_recordings_are_equally_weighted_independent_of_duration(self):
        negative={'quiet':[row(0)]*10,'disturbance':[row(2)]*10}
        positive={'motor':[row(4)]*10}
        short=fit(positive,negative,['band_60_120'])
        negative['quiet'] *= 20
        long=fit(positive,negative,['band_60_120'])
        self.assertAlmostEqual(short['centre'][0],long['centre'][0])
        self.assertAlmostEqual(short['weight'][0],long['weight'][0])

    def test_correlated_and_constant_features_are_finite(self):
        positive={'motor':[row(1,1)]*20}
        negative={'off':[row(0,0)]*20}
        model=fit(positive,negative,['band_60_120','autocorr_periodicity','spectral_flux'])
        self.assertGreater(score(model,row(1,1)),.99)
        self.assertLess(score(model,row(0,0)),.01)

    def test_short_blocks_fail_explicitly(self):
        with self.assertRaisesRegex(ValueError,'Not enough rows'):
            blocks({'motor':[row(1)]*4})

    def test_invalid_prototype_blend_cannot_run(self):
        with self.assertRaisesRegex(ValueError,'Blend'):
            replay_candidate({}, {}, {}, 2)
