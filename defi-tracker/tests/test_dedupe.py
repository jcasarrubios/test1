from dft.news.dedupe import dedupe, normalise_title, normalise_url


def test_normalise_url_strips_tracking_params():
    a = normalise_url("https://a.com/x/?utm_source=rss&ref=abc&real=1")
    b = normalise_url("https://a.com/x?utm_source=other&real=1")
    assert a == b == "https://a.com/x?real=1"


def test_normalise_title_drops_stopwords_and_case():
    assert normalise_title("The Aave DAO Approves a New Proposal") == \
        normalise_title("aave dao approves new proposal")


def test_dedupe_collapses_url_duplicates():
    items = [
        {"source": "A", "title": "Story one", "url": "https://a.com/1?utm_source=rss",
         "summary": "", "published_at": "2026-08-27T10:00:00+00:00"},
        {"source": "A", "title": "Story one (updated)", "url": "https://a.com/1?utm_source=other",
         "summary": "", "published_at": "2026-08-27T10:00:00+00:00"},
    ]
    out = dedupe(items, threshold=0.9)
    assert len(out) == 1


def test_dedupe_clusters_similar_titles_across_outlets():
    items = [
        {"source": "A", "title": "Aave DAO approves new fee switch proposal",
         "url": "https://a.com/x", "summary": "", "published_at": "2026-08-27T10:00:00+00:00"},
        {"source": "B", "title": "Aave DAO approves fee switch proposal",
         "url": "https://b.com/y", "summary": "", "published_at": "2026-08-27T10:05:00+00:00"},
    ]
    out = dedupe(items, threshold=0.7)
    assert len(out) == 1
    assert out[0]["outlet_count"] == 2
    assert out[0]["also_reported_by"] == ["B"]


def test_dedupe_keeps_distinct_stories():
    items = [
        {"source": "A", "title": "Solana outage hits mainnet", "url": "https://a.com/1",
         "summary": "", "published_at": "2026-08-27T10:00:00+00:00"},
        {"source": "B", "title": "Ethereum gas fees spike after NFT mint", "url": "https://b.com/2",
         "summary": "", "published_at": "2026-08-27T10:05:00+00:00"},
    ]
    out = dedupe(items, threshold=0.82)
    assert len(out) == 2
