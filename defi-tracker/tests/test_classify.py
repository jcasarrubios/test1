from dft.news.classify import heuristic_classify


def test_exploit_keyword_gets_materiality_3():
    r = heuristic_classify({"title": "Protocol X hacked, $10M drained", "summary": ""}, ["AAVE"])
    assert r["category"] == "exploit"
    assert r["materiality"] == 3


def test_routine_news_gets_low_materiality():
    r = heuristic_classify({"title": "Aave posts routine governance update", "summary": ""}, ["AAVE"])
    assert r["materiality"] == 1
    assert "AAVE" in r["entities"]


def test_regulatory_keyword():
    r = heuristic_classify({"title": "SEC charges exchange with fraud", "summary": ""}, [])
    assert r["category"] == "regulatory"
