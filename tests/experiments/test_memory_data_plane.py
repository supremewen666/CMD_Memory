import json
import pytest
from experiments.memory_data_plane import AuditedInMemoryDataPlane, Mem0DataPlane
from experiments.run_longmemeval_m0_r1 import _ordered_sessions, iter_json_array, run

def test_scope_order_and_audit(tmp_path):
    plane=AuditedInMemoryDataPlane(tmp_path/"audit.jsonl")
    plane.add(content="old pizza",scope="a"); plane.add(content="new pizza",scope="a")
    assert {x.content for x in plane.search(query="pizza",scope="a",limit=2)} == {"new pizza","old pizza"}
    assert plane.search(query="pizza",scope="b",limit=2)==()
    lines=(tmp_path/"audit.jsonl").read_text().splitlines(); assert len(lines)==4

def test_buffered_audit_flushes_once(tmp_path):
    path=tmp_path/"audit.jsonl"
    plane=AuditedInMemoryDataPlane(path,sync_each_event=False)
    plane.add(content="one",scope="a"); plane.add(content="two",scope="a")
    assert not path.exists()
    plane.flush()
    assert len(path.read_text().splitlines())==2

def test_oracle_is_sidecar(tmp_path):
    relevant = [{"role":"user","content":"hello gold"}]
    distractor = [{"role":"user","content":"unrelated"}]
    data=tmp_path/"data.json"; oracle=tmp_path/"oracle.json"
    data.write_text(json.dumps([{
        "question_id":"q1","question":"hello","answer":"secret-label",
        "answer_session_ids":["s-old"],
        "haystack_dates":["2026/01/02 (Fri) 10:00","2026/01/01 (Thu) 10:00"],
        "haystack_session_ids":["s-new","s-old"],
        "haystack_sessions":[distractor,relevant],
    }]))
    oracle.write_text(json.dumps([{
        "question_id":"q1","haystack_sessions":[relevant]
    }]))
    payload=run(data,oracle,tmp_path/"out")
    assert payload["question_count"]==1
    assert payload["r1_recall_at_5"]==1.0
    written="".join(path.read_text() for path in (tmp_path/"out").rglob("*.json*"))
    assert "secret-label" not in written


def test_streams_top_level_array_across_small_chunks(tmp_path):
    path=tmp_path/"rows.json"
    path.write_text(json.dumps([{"question_id":"q1","text":"α"},{"question_id":"q2","text":"beta"}]))
    assert [row["question_id"] for row in iter_json_array(path,chunk_size=3)]==["q1","q2"]


def test_orders_paired_sessions_chronologically_with_stable_ties():
    row={
        "haystack_dates":["2026/01/02 (Fri) 09:00","2026/01/01 (Thu) 09:00","2026/01/01 (Thu) 09:00"],
        "haystack_session_ids":["late","tie-a","tie-b"],
        "haystack_sessions":[["late"],["a"],["b"]],
    }
    assert [session_id for _,session_id,_ in _ordered_sessions(row)]==["tie-a","tie-b","late"]


def test_unscorable_is_null_not_false(tmp_path):
    data=tmp_path/"data.json"; oracle=tmp_path/"oracle.json"
    data.write_text(json.dumps([{
        "question_id":"q1","question":"missing",
        "haystack_dates":["2026/01/01 (Thu) 09:00"],
        "haystack_session_ids":["s1"],"haystack_sessions":[["present"]],
    }]))
    oracle.write_text(json.dumps([{"question_id":"q1","haystack_sessions":[["absent"]]}]))
    payload=run(data,oracle,tmp_path/"out")
    instance=json.loads(next((tmp_path/"out"/"instances").glob("*.json")).read_text())
    assert instance["scorable"] is False
    assert instance["r1_recall_at_5"] is None
    assert payload["unscorable_fraction"]==1.0

def test_streamer_fails_closed_on_malformed_tail(tmp_path):
    path=tmp_path/"bad.json"; path.write_text('[{"question_id":"q"}] junk')
    with pytest.raises(ValueError):
        list(iter_json_array(path, chunk_size=2))

def test_arm_scope_and_oracle_firewall_and_resume_parity(tmp_path):
    relevant=[{"role":"user","content":"needle"}]
    rows=[]
    for number in range(5):
        rows.append({"question_id":f"q{number}","question":"needle","answer":"GOLD-DO-NOT-LEAK","answer_session_ids":["gold-id"],"haystack_dates":["2026/01/02 (Fri) 09:00","2026/01/01 (Thu) 09:00"],"haystack_session_ids":["late","early"],"haystack_sessions":[[{"content":"noise"}],relevant]})
    data=tmp_path/"data.json"; oracle=tmp_path/"oracle.json"; data.write_text(json.dumps(rows)); oracle.write_text(json.dumps([{"question_id":f"q{i}","haystack_sessions":[relevant]} for i in range(5)]))
    clean=run(data,oracle,tmp_path/"clean",arms=("vanilla","static","cmd","ghost"),limit=5)
    resumed=run(data,oracle,tmp_path/"clean",arms=("vanilla","static","cmd","ghost"),limit=5,resume=True)
    assert resumed["outcome_root"]==clean["outcome_root"]
    audits="".join(x.read_text() for x in (tmp_path/"clean"/"audits").glob("*.jsonl"))
    assert "GOLD-DO-NOT-LEAK" not in audits and "gold-id" not in audits
    assert len(list((tmp_path/"clean"/"audits").glob("*.vanilla.jsonl")))==5
    assert clean["arms"]["cmd"]["mode"]=="shadow_observe_only"

def test_mem0_injected_client_has_explicit_scope_and_real_search_boundary(tmp_path):
    class Fake:
        def __init__(self): self.calls=[]
        def add(self, content, *, user_id): self.calls.append(("add",content,user_id)); return {"results":[]}
        def search(self, query, *, user_id, limit): self.calls.append(("search",query,user_id,limit)); return {"results":[{"id":"x","memory":"value"}]}
    fake=Fake(); plane=Mem0DataPlane(namespace="tenant",user_id="tenant",config={"vector_store":{}},audit_path=tmp_path/"a.jsonl",client=fake)
    plane.add(content="value",scope="tenant")
    assert plane.search(query="value",scope="tenant",limit=1)[0].content=="value"
    with pytest.raises(ValueError): plane.search(query="value",scope="other",limit=1)
    assert fake.calls[0][-1]=="tenant"
