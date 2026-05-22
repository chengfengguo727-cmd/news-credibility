from score.bayesian import summarize, update


def test_uniform_prior_is_half():
    s = summarize(1.0, 1.0, n=0)
    assert abs(s.score - 0.5) < 1e-9
    assert s.ci_low < s.score < s.ci_high


def test_hits_push_score_up():
    a, b = update(1.0, 1.0, hit=10, miss=0)
    s = summarize(a, b, n=10)
    assert s.score > 0.8


def test_misses_push_score_down():
    a, b = update(1.0, 1.0, hit=0, miss=10)
    s = summarize(a, b, n=10)
    assert s.score < 0.2


def test_partial_counts_as_half():
    a1, b1 = update(1.0, 1.0, partial=10)
    s = summarize(a1, b1, n=10)
    assert abs(s.score - 0.5) < 0.05


def test_ci_shrinks_with_more_data():
    s_small = summarize(*update(1.0, 1.0, hit=5, miss=5), n=10)
    s_large = summarize(*update(1.0, 1.0, hit=50, miss=50), n=100)
    assert (s_large.ci_high - s_large.ci_low) < (s_small.ci_high - s_small.ci_low)
