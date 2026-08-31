from evaluation.a2_benchmark import run_benchmark


def test_a2_benchmark_meets_all_contract_thresholds():
    scorecard = run_benchmark()

    assert scorecard["fixture_count"] == 14
    assert scorecard["passed"] is True
    assert all(
        scorecard["metrics"][key] >= threshold
        for key, threshold in scorecard["thresholds"].items()
    )
