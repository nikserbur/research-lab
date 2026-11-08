"""skills/research/scripts/labkit/stats.py: the numbers the selection gates rest on.

    python3 -m unittest discover -s tests -v
"""
import math
import pathlib
import random
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "skills" / "research" / "scripts"))
from labkit import stats  # noqa: E402


class Moments(unittest.TestCase):
    def test_population_moments(self):
        mean, sd, skew, kurt = stats.moments([1, 2, 3, 4])
        self.assertEqual(mean, 2.5)
        self.assertAlmostEqual(sd, math.sqrt(1.25))
        self.assertAlmostEqual(skew, 0.0)
        self.assertAlmostEqual(kurt, 1.64)
        self.assertEqual(stats.moments([2, 2, 2]), (2, 0.0, 0.0, 3.0))


class DeflatedSharpe(unittest.TestCase):
    def test_expected_best_of_noise_grows_with_trials(self):
        self.assertEqual(stats.expected_max_sharpe(1, 1.0), 0.0)
        self.assertAlmostEqual(stats.expected_max_sharpe(100, 1.0), 2.53, delta=0.02)
        self.assertLess(stats.expected_max_sharpe(10, 1.0), stats.expected_max_sharpe(1000, 1.0))
        self.assertAlmostEqual(stats.expected_max_sharpe(100, 4.0), 2 * stats.expected_max_sharpe(100, 1.0))

    def test_psr_is_a_half_at_the_benchmark_and_falls_with_trials(self):
        self.assertAlmostEqual(stats.psr(0.1, 0.1, 250, 0.0, 3.0), 0.5)
        sr, n = 0.15, 250
        dsr = [stats.psr(sr, stats.expected_max_sharpe(k, 1 / n), n, 0.0, 3.0) for k in (1, 10, 100, 1000)]
        self.assertEqual(dsr, sorted(dsr, reverse=True))
        self.assertGreater(dsr[0], 0.98)
        self.assertLess(dsr[-1], 0.5)

    def test_fat_tails_and_negative_skew_lower_confidence(self):
        normal = stats.psr(0.1, 0.0, 250, 0.0, 3.0)
        self.assertLess(stats.psr(0.1, 0.0, 250, -1.0, 10.0), normal)

    def test_track_length_for_a_deflated_sharpe_of_95_percent(self):
        sr, trials = 0.167, 31
        n95 = stats.min_track_length_95(sr, trials)
        benchmark = lambda n: stats.expected_max_sharpe(trials, 1 / n)
        self.assertAlmostEqual(stats.psr(sr, benchmark(n95), round(n95), 0.0, 3.0), 0.95, delta=0.01)
        self.assertGreater(n95, stats.min_track_length(sr, trials))

    def test_minimum_track_length(self):
        self.assertAlmostEqual(stats.min_track_length(0.1, 100), (stats.expected_max_sharpe(100, 1.0) / 0.1) ** 2)
        self.assertEqual(stats.min_track_length(0.1, 1), 0.0)
        self.assertEqual(stats.min_track_length(-0.1, 10), math.inf)


class Bootstrap(unittest.TestCase):
    def series(self, mean, n=250, seed=1):
        rng = random.Random(seed)
        return [mean + rng.gauss(0, 1) for _ in range(n)]

    def test_p_value_separates_an_edge_from_noise(self):
        self.assertLess(stats.p_mean_above_zero(self.series(0.4)), 0.01)
        self.assertGreater(stats.p_mean_above_zero(self.series(0.0, seed=3)), 0.05)
        self.assertEqual(stats.p_mean_above_zero(self.series(0.1)), stats.p_mean_above_zero(self.series(0.1)))

    def test_interval_covers_the_mean_and_widens_with_dependence(self):
        xs = self.series(0.2)
        lo, hi = stats.interval(xs)
        self.assertLess(lo, sum(xs) / len(xs))
        self.assertGreater(hi, sum(xs) / len(xs))
        rng, level, walk = random.Random(5), 0.0, []
        for _ in range(250):  # strongly autocorrelated days
            level = 0.9 * level + rng.gauss(0, 1)
            walk.append(level)
        narrow = stats.interval(walk, block=1)
        wide = stats.interval(walk, block=20)
        self.assertGreater(wide[1] - wide[0], narrow[1] - narrow[0])

    def test_block_length_follows_the_autocorrelation(self):
        self.assertLess(stats.auto_block(self.series(0.0, n=1000)), 3)
        rng, level, walk = random.Random(5), 0.0, []
        for _ in range(1000):
            level = 0.9 * level + rng.gauss(0, 1)
            walk.append(level)
        self.assertGreater(stats.auto_block(walk), 10)
        self.assertLessEqual(stats.auto_block(walk), 250)
        self.assertEqual(stats.auto_block([1.0] * 50), 1.0)

    def test_student_tail(self):
        self.assertAlmostEqual(stats.t_upper(2.0, 10), 0.036694, places=5)
        self.assertAlmostEqual(stats.t_upper(1.812461, 10), 0.05, places=5)
        self.assertAlmostEqual(stats.t_upper(-1.0, 5), 1 - stats.t_upper(1.0, 5), places=12)
        self.assertAlmostEqual(stats.t_upper(1.644854, 1e7), 0.05, places=4)
        self.assertEqual(stats.t_upper(0.0, 3), 0.5)

    def test_noise_is_rejected_near_its_level(self):
        rng, hits = random.Random(42), 0
        for k in range(200):
            xs = [rng.gauss(0, 1) for _ in range(250)]
            hits += stats.bootstrap(xs, stats.auto_block(xs), draws=400, seed=k)["p"] <= 0.05
        self.assertLessEqual(hits, 18)

    def test_a_strong_edge_gets_a_tail_p_value_below_the_draw_floor(self):
        result = stats.bootstrap(self.series(1.0), 5)
        self.assertLess(result["p"], 1 / 2001)
        self.assertGreater(result["se"], 0)

    def test_long_series_are_fast(self):
        import time
        start = time.time()
        stats.bootstrap(self.series(0.1, n=20000), stats.auto_block(self.series(0.1, n=20000)))
        self.assertLess(time.time() - start, 30)  # about 2 s; the old index-by-index loop took 15 s here


class FalseDiscoveries(unittest.TestCase):
    def test_benjamini_yekutieli(self):
        p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216]
        self.assertEqual(stats.benjamini_yekutieli(p, 0.05), [True] + [False] * 9)
        self.assertEqual(stats.benjamini_yekutieli([0.0001, 0.0002], 0.05), [True, True])
        self.assertEqual(stats.benjamini_yekutieli([]), [])

    def test_lord_spends_and_earns_wealth_in_order(self):
        self.assertAlmostEqual(sum(stats.gamma(t) for t in range(1, 200000)), 1.0, places=4)
        levels = stats.lord_levels([0.001, 0.2, 0.2], 0.05)
        self.assertAlmostEqual(levels[0][0], stats.gamma(1) * 0.025)
        self.assertTrue(levels[0][1])
        quiet = stats.lord_levels([0.9, 0.2, 0.2], 0.05)
        self.assertGreater(levels[1][0], quiet[1][0])  # a discovery earns wealth for the next tests
        self.assertEqual(stats.lord_levels([0.001, 0.2], 0.05), levels[:2])  # later tests change nothing earlier

    def test_many_null_tests_rarely_discover(self):
        rng = random.Random(11)
        found = sum(f for _, f in stats.lord_levels([rng.random() for _ in range(200)], 0.05))
        self.assertLessEqual(found, 2)


class ELond(unittest.TestCase):
    def test_the_level_and_the_calibrated_e_value_decide_alike(self):
        rng = random.Random(4)
        for t in range(1, 40):
            for found in range(t):
                p = rng.random() ** 4
                test = stats.elond_test(p, t, found, 0.05)
                self.assertEqual(test["discovery"], p <= test["level"] * (1 + 1e-12))
                self.assertEqual(test["discovery"], test["e_value"] >= 1 / test["e_level"] * (1 - 1e-12))
        first = stats.elond_test(0.02, 1, 0, 0.05)
        self.assertAlmostEqual(first["level"], 0.025)  # alpha * gamma_1, gamma_t = 1 / (t (t + 1))
        self.assertTrue(first["discovery"])
        self.assertAlmostEqual(stats.elond_test(0.02, 2, 0, 0.05)["level"], 0.05 / 6 / 1.5)  # / the harmonic number

    def test_the_calibrator_is_an_e_value(self):
        for t, budget in ((1, None), (7, None), (30, None), (5, 50)):
            g, ell = stats.elond_gamma(t, budget), sum(1 / i for i in range(1, t + 1))
            step = 0.05 * g / ell  # the calibrated e-value is a step function of p: integrate it exactly
            total = sum(stats.elond_test(step * (k - 0.5), t, 0, 0.05, budget)["e_value"] * step for k in range(1, t + 3))
            self.assertAlmostEqual(total, 1.0)
            self.assertEqual(stats.elond_test(step * (t + 0.5), t, 0, 0.05, budget)["e_value"], 0.0)

    def test_a_declared_budget_spreads_the_level_evenly(self):
        self.assertEqual([stats.elond_gamma(t, 40) for t in (1, 40, 41)], [1 / 40, 1 / 40, 0.0])
        self.assertAlmostEqual(sum(stats.elond_gamma(t) for t in range(1, 100000)), 1.0, places=4)
        late = stats.elond_test(1e-4, 30, 0, 0.05, 40)
        self.assertTrue(late["discovery"])
        self.assertFalse(stats.elond_test(1e-4, 30, 0, 0.05)["discovery"])

    def test_levels_depend_only_on_earlier_discoveries(self):
        levels = stats.elond_levels([1e-5, 0.008, 0.3], 0.05)
        self.assertEqual(stats.elond_levels([1e-5, 0.008], 0.05), levels[:2])
        quiet = stats.elond_levels([0.9, 0.008], 0.05)
        self.assertGreater(levels[1][0], quiet[1][0])  # a discovery raises the next level
        self.assertTrue(levels[1][1])
        self.assertFalse(quiet[1][1])

    def test_false_discoveries_stay_below_alpha_when_tests_move_together(self):
        """Runs on the same days: after two real discoveries, eight null runs share one p-value."""
        rng, fdp = random.Random(9), []
        for _ in range(4000):
            shared = rng.random()
            pvalues = [1e-6, 1e-6] + [shared] * 8
            found = [f for _, f in stats.elond_levels(pvalues, 0.05)]
            fdp.append(sum(found[2:]) / max(1, sum(found)))
        self.assertLess(sum(fdp) / len(fdp), 0.05)

    def test_many_null_tests_rarely_discover(self):
        rng = random.Random(11)
        self.assertLessEqual(sum(f for _, f in stats.elond_levels([rng.random() for _ in range(200)], 0.05)), 2)


class Power(unittest.TestCase):
    def test_resampled_means_spread_like_the_standard_error(self):
        rng = random.Random(2)
        xs = [0.3 + rng.gauss(0, 1) for _ in range(400)]
        draws = stats.resampled_moments(xs, 1.0, draws=1000)
        means = [m for m, _ in draws]
        self.assertAlmostEqual(stats.statistics_stdev(means), 0.05, delta=0.006)  # 1 / sqrt(400)
        self.assertAlmostEqual(sum(means) / len(means), sum(xs) / len(xs), delta=0.01)
        self.assertAlmostEqual(sum(sd for _, sd in draws) / len(draws), stats.moments(xs)[1], delta=0.02)

    def test_james_stein_shrinks_toward_zero_more_when_the_others_are_noise(self):
        self.assertEqual(stats.shrink_factor(0.5), 0.0)  # inside one standard error: nothing to keep
        self.assertAlmostEqual(stats.shrink_factor(4.0), 1 - 1 / 16)
        alone = stats.shrink_factor(3.0, [3.0, 4.0, 2.5])
        among_noise = stats.shrink_factor(3.0, [3.0] + [0.3, -0.8, 1.1, -0.2, 0.5, -1.0, 0.9])
        self.assertLess(among_noise, alone)
        self.assertEqual(stats.shrink_factor(3.0, [0.1, 0.2, -0.1, 0.3]), 0.0)


class Overfitting(unittest.TestCase):
    def noise(self, n_cfg, periods, seed, edge=None):
        rng = random.Random(seed)
        cols = [[rng.gauss(0, 1) for _ in range(periods)] for _ in range(n_cfg)]
        if edge is not None:
            cols[edge] = [x + 0.6 for x in cols[edge]]
        return cols

    def test_noise_is_overfit_about_half_the_time(self):
        found = [stats.pbo(self.noise(10, 480, seed), 8)["pbo"] for seed in range(40)]
        self.assertAlmostEqual(sum(found) / len(found), 0.5, delta=0.1)  # one dataset alone varies widely
        result = stats.pbo(self.noise(10, 480, 1), 8)
        self.assertEqual(result["splits"], 70)  # C(8, 4)
        self.assertEqual(len(result["logits"]), 70)

    def test_a_real_edge_is_not_overfit(self):
        result = stats.pbo(self.noise(10, 480, 3, edge=4), 16)
        self.assertEqual(result["pbo"], 0.0)
        self.assertEqual(result["splits"], 12870)
        self.assertEqual(result["loss"], 0.0)
        self.assertGreater(min(result["logits"]), 2.0)  # top of eleven out of sample: ln(10/1)
        self.assertEqual(result["chosen"][4], 1.0)

    def test_configurations_fitted_to_their_own_blocks_are_overfit(self):
        rng, cols = random.Random(5), []
        for j in range(4):  # each earns in one block and gives it back in the others
            cols.append([(1.0 if i // 50 == j else -1 / 3) + rng.gauss(0, 0.1) for i in range(200)])
        result = stats.pbo(cols, 4, "mean")
        self.assertEqual(result["pbo"], 1.0)
        self.assertLess(result["slope"], 0)
        self.assertEqual(result["loss"], 1.0)
        self.assertGreater(result["is_mean"], 0)
        self.assertLess(result["oos_mean"], 0)

    def test_bad_input(self):
        cols = self.noise(3, 100, 1)
        for bad in ((cols, 5), (cols, 0), (cols[:1], 4), ([cols[0], cols[1][:90]], 4), (self.noise(3, 30, 1), 16)):
            with self.assertRaises(ValueError):
                stats.pbo(*bad)
        with self.assertRaises(ValueError):
            stats.pbo(cols, 4, "sortino")



class ConfidenceSet(unittest.TestCase):
    def test_a_worse_series_leaves_and_equals_stay(self):
        rng = random.Random(1)
        a = [0.3 + rng.gauss(0, 1) for _ in range(500)]
        worse = [x - 0.2 + rng.gauss(0, 0.3) for x in a]  # tied to a, so the gap is measured precisely
        noise = [rng.gauss(0, 1) for _ in range(500)]
        members, dropped = stats.confidence_set({"a": a, "worse": worse, "noise": noise}, 3.0)
        self.assertEqual(members, ["a"])
        self.assertEqual({d for d, _ in dropped}, {"worse", "noise"})
        twin = [x + rng.gauss(0, 0.3) for x in a]
        self.assertEqual(sorted(stats.confidence_set({"a": a, "twin": twin}, 3.0)[0]), ["a", "twin"])
        self.assertEqual(stats.confidence_set({"a": a}, 3.0), (["a"], []))

    def test_short_or_misaligned_series(self):
        rng = random.Random(3)
        a = [1.0 + rng.gauss(0, 1) for _ in range(60)]
        b = [rng.gauss(0, 1) for _ in range(60)]
        self.assertEqual(sorted(stats.confidence_set({"a": a, "b": b}, 15.0)[0]), ["a", "b"])  # 4 blocks: keep both
        with self.assertRaises(ValueError):
            stats.confidence_set({"a": a, "b": b[:50]}, 1.0)

    def test_equal_candidates_rarely_lose_one(self):
        lost = 0
        for seed in range(20):
            rng = random.Random(100 + seed)
            series = {k: [0.1 + rng.gauss(0, 1) for _ in range(300)] for k in "abc"}
            lost += len(stats.confidence_set(series, 1.0, 0.1, draws=300)[1]) > 0
        self.assertLessEqual(lost, 5)  # the set keeps all three at 90% about nine times in ten


if __name__ == "__main__":
    unittest.main()
