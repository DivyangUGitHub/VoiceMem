"""search_with_graph used to reference classes that did not exist (NameError on a default path)."""

from types import SimpleNamespace

from voicemem.leftbrain.memory_repository import GraphSearchHit, LeftBrainMemoryRepository
from voicemem.utils.fusion.left_channel import _normalize_graph_search_hits


def _repo(graph_store):
    repo = object.__new__(LeftBrainMemoryRepository)
    repo._graph_store = graph_store
    hit = SimpleNamespace(memory_id="m1", text="likes tea", score=0.9, metadata={})
    repo.search = lambda *a, **k: [hit]
    return repo


def test_without_graph_store_returns_plain_hits():
    out = _repo(None).search_with_graph("tea", user_id="u")
    assert [type(h) for h in out] == [GraphSearchHit]
    hits, appendix = _normalize_graph_search_hits(out)
    assert [h.text for h in hits] == ["likes tea"] and appendix == ""


def test_graph_store_without_enrichment_does_not_crash():
    out = _repo(object()).search_with_graph("tea", user_id="u")
    assert out[0].graph.memory_id == "m1"


def test_graph_store_with_enrichment_is_used():
    store = SimpleNamespace(enrich_hits=lambda hits, **k: ["enriched"])
    assert _repo(store).search_with_graph("tea", user_id="u") == ["enriched"]
