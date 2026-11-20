from agent.handbook import get_index, load_chunks
from agent.tools import execute_tool


def test_chunks_have_doc_and_section_ids():
    chunks = load_chunks()
    assert len(chunks) >= 30
    docs = {c.doc for c in chunks}
    assert docs == {"PTO", "Holidays", "Benefits", "Expenses", "Remote Work", "Onboarding"}
    assert all(c.section and c.title and c.text for c in chunks)
    pto_22 = next(c for c in chunks if c.doc == "PTO" and c.section == "2.2")
    assert pto_22.citation == "PTO §2.2"
    assert "carry over" in pto_22.text.lower()


def test_search_returns_three_cited_sections():
    results = get_index().search("carry over unused PTO into next year")
    assert len(results) == 3
    assert results[0]["citation"] == "PTO §2.2"
    for r in results:
        assert set(r) >= {"doc", "section", "citation", "title", "text", "score"}
        assert r["citation"] == f"{r['doc']} §{r['section']}"


def test_search_finds_the_right_document():
    assert get_index().search("home office stipend")[0]["citation"] == "Remote Work §3"
    assert get_index().search("paid company holidays per year")[0]["citation"] == "Holidays §1"
    assert get_index().search("when does health insurance coverage start")[0]["doc"] == "Benefits"


def test_search_tool_wraps_results(ctx_factory):
    result, status = execute_tool("search_handbook", {"query": "expense receipt deadline"}, ctx_factory("E004"))
    assert status == "ok"
    assert len(result["results"]) == 3
    assert any(r["doc"] == "Expenses" for r in result["results"])
