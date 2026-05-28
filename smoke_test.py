"""5-case smoke test: verify agent_generate + evaluator pipeline works."""
from cmd_audit.core.llm_client import LLMClient, LLMClientConfig

def test_model(client, name, prompt, system=None):
    try:
        result = client.generate(prompt, system=system)
        ok = result is not None and len(result.strip()) > 0
        print(f"  [{name}] {'PASS' if ok else 'FAIL'} → {result.strip()[:120]}...")
        return ok
    except Exception as e:
        print(f"  [{name}] FAIL ({type(e).__name__}: {e})")
        return False

# ---- Test cases ----
TEST_QUERY = "What is the capital of Portugal?"
TEST_CONTEXT = "Lisbon is the capital and largest city of Portugal."
TEST_GOLD = "Lisbon"
TEST_WRONG = "Porto"
TEST_EVIDENCE = "Lisbon is the capital and largest city of Portugal."

print("=" * 60)
print("Agent test (qwen2.5:7b)")
print("=" * 60)
qwen = LLMClient(LLMClientConfig(model="qwen2.5:7b", temperature=0.0))
qwen_ok = test_model(qwen, "agent_generate",
    f"Query: {TEST_QUERY}\nContext: {TEST_CONTEXT}\nAnswer concisely:")

print("\n" + "=" * 60)
print("Evaluator-A test (mistral:7b)")
print("=" * 60)
mistral = LLMClient(LLMClientConfig(model="mistral:7b", temperature=0.0))

SYSTEM_EVIDENCE = (
    "You are an evidence verifier. Reply with exactly one word: "
    "PRESENT or ABSENT. Do not explain."
)
mis_ev_ok = test_model(mistral, "evidence_scorer",
    f"Evidence: {TEST_EVIDENCE}\nText: {TEST_CONTEXT}",
    system=SYSTEM_EVIDENCE)

SYSTEM_ANSWER = (
    "You are an answer verifier. Reply with exactly one word: "
    "EQUIVALENT or NOT_EQUIVALENT. Do not explain."
)
mis_ans_ok = test_model(mistral, "answer_verifier (eq)",
    f"Answer A: {TEST_GOLD}\nAnswer B: Lisbon",
    system=SYSTEM_ANSWER)
mis_ans2_ok = test_model(mistral, "answer_verifier (neq)",
    f"Answer A: {TEST_GOLD}\nAnswer B: {TEST_WRONG}",
    system=SYSTEM_ANSWER)

print("\n" + "=" * 60)
print("Evaluator-B test (gemma3:12b)")
print("=" * 60)
gemma = LLMClient(LLMClientConfig(model="gemma3:12b", temperature=0.0))
gemma_ok = test_model(gemma, "evidence_scorer",
    f"Evidence: {TEST_EVIDENCE}\nText: {TEST_CONTEXT}",
    system=SYSTEM_EVIDENCE)

print("\n" + "=" * 60)
print("LLM-A test (llama3.1:8b)")
print("=" * 60)
llama = LLMClient(LLMClientConfig(model="llama3.1:8b", temperature=0.0))
SYSTEM_LLMA = """Classify the memory failure. Reply JSON only:
{"suggested_label": "retrieval_error", "rationale": "1 sentence"}"""
llama_ok = test_model(llama, "LLM-A suggestion",
    f"Query: {TEST_QUERY}\nGold answer: {TEST_GOLD}\n"
    f"Memory items: [Lisbon capital info in vector store]\n"
    f"Retrieved: []\nInjected context: (empty)",
    system=SYSTEM_LLMA)

# ---- Summary ----
all_ok = all([qwen_ok, mis_ev_ok, mis_ans_ok, mis_ans2_ok, gemma_ok, llama_ok])
print("\n" + "=" * 60)
print(f"SMOKE TEST: {'ALL PASS' if all_ok else 'SOME FAILED'}")
print(f"  agent_generate (qwen):    {'OK' if qwen_ok else 'FAIL'}")
print(f"  evidence_scorer (mistral):{'OK' if mis_ev_ok else 'FAIL'}")
print(f"  answer_verifier (mistral):{'OK' if mis_ans_ok and mis_ans2_ok else 'FAIL'}")
print(f"  evaluator-B (gemma):      {'OK' if gemma_ok else 'FAIL'}")
print(f"  LLM-A (llama):            {'OK' if llama_ok else 'FAIL'}")
print("=" * 60)
