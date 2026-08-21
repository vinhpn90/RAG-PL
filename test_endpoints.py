import requests
import json
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_URL = "http://localhost:8000"

def test_health():
    print("\n--- 1. Testing GET /health ---")
    res = requests.get(f"{BASE_URL}/health")
    print(f"Status Code: {res.status_code}")
    print(f"Response: {res.json()}")
    assert res.status_code == 200
    assert res.json().get("status") == "ok"
    print(">>> GET /health PASSED! ✅")

def test_root():
    print("\n--- 2. Testing GET / (HTML UI) ---")
    res = requests.get(f"{BASE_URL}/")
    print(f"Status Code: {res.status_code}")
    print(f"Content-Type: {res.headers.get('content-type')}")
    print(f"HTML Preview: {res.text[:150]}...")
    assert res.status_code == 200
    assert "Legal AI Assistant" in res.text or "<!DOCTYPE html>" in res.text or "html" in res.text
    assert "Thủ tục" in res.text
    print(">>> GET / PASSED! ✅")

def test_chat_empty_validation():
    print("\n--- 3. Testing POST /chat with empty messages (Validation) ---")
    payload = {"messages": [], "stream": False}
    res = requests.post(f"{BASE_URL}/chat", json=payload)
    print(f"Status Code: {res.status_code}")
    print(f"Response: {res.json()}")
    assert res.status_code == 400
    print(">>> Validation Test PASSED! ✅")

def test_chat_non_streaming():
    print("\n--- 4. Testing POST /chat (Non-streaming: stream=false) ---")
    payload = {
        "messages": [
            {"role": "user", "content": "Thủ tục đăng ký khai sinh thực hiện ở đâu?"}
        ],
        "stream": False
    }
    t0 = time.time()
    res = requests.post(f"{BASE_URL}/chat", json=payload, timeout=60)
    elapsed = time.time() - t0
    print(f"Status Code: {res.status_code} (took {elapsed:.2f}s)")
    data = res.json()
    print(f"Sub-queries count: {len(data.get('sub_queries', []))}")
    print(f"Citations count: {len(data.get('citations', {}))}")
    print(f"Sources count: {len(data.get('sources', []))}")
    print(f"Related Procedures count: {len(data.get('related_procedures', []))}")
    if data.get('related_procedures'):
        p0 = data['related_procedures'][0]
        print(f"  -> Sample Procedure: [{p0.get('procedure_code')}] {p0.get('procedure_name')}")
        print(f"     Reason: {p0.get('relevance_reason')}")
        print(f"     DVC URL: {p0.get('source_url')}")
    print(f"Steps count: {len(data.get('steps', []))}")
    print(f"Final Answer: {data.get('final_answer', '')[:250]}...")
    print(f"Clarification: {data.get('clarification')}")
    assert res.status_code == 200
    assert len(data.get("final_answer", "")) > 0
    print(">>> POST /chat (Non-streaming) PASSED! ✅")

def test_chat_streaming():
    print("\n--- 5. Testing POST /chat (SSE Streaming: stream=true) ---")
    payload = {
        "messages": [
            {"role": "user", "content": "Thời hạn đăng ký khai sinh cho trẻ là bao lâu và cần làm thủ tục gì?"}
        ],
        "stream": True
    }
    t0 = time.time()
    res = requests.post(f"{BASE_URL}/chat", json=payload, stream=True, timeout=60)
    print(f"Status Code: {res.status_code}")
    print(f"Content-Type: {res.headers.get('content-type')}")
    assert res.status_code == 200
    assert "text/event-stream" in res.headers.get("content-type", "")

    event_count = 0
    received_steps = set()
    answer_chunks = []
    has_done = False
    received_procedures = []

    for line in res.iter_lines(decode_unicode=True):
        if line:
            if line.startswith("data: "):
                raw_data = line[6:]
                if raw_data == "[DONE]":
                    has_done = True
                    print("\n-> Received [DONE] signal!")
                    break
                try:
                    event = json.loads(raw_data)
                    event_count += 1
                    step = event.get("step")
                    status = event.get("status")
                    received_steps.add(step)
                    if step == "answer" and status == "streaming":
                        chunk_text = event.get("data", {}).get("chunk", "") or event.get("data", {}).get("text", "")
                        answer_chunks.append(chunk_text)
                        print(".", end="", flush=True)
                    elif step == "related_procedures" and status == "done":
                        received_procedures = event.get("data", {}).get("procedures", [])
                        print(f"\n-> Event: related_procedures ({len(received_procedures)} items found)", end="", flush=True)
                    else:
                        print(f"\n-> Event: {step} ({status})", end="", flush=True)
                except Exception as e:
                    print(f"\nError parsing event: {e}, line: {line}")

    elapsed = time.time() - t0
    full_stream_answer = "".join(answer_chunks)
    print(f"\nTotal SSE events received: {event_count} (took {elapsed:.2f}s)")
    print(f"Steps traversed: {received_steps}")
    print(f"Received Procedures in Stream: {len(received_procedures)}")
    if received_procedures:
        p0 = received_procedures[0]
        print(f"  -> Sample Stream Procedure: [{p0.get('procedure_code')}] {p0.get('procedure_name')}")
        print(f"     Reason: {p0.get('relevance_reason')}")
    print(f"Streamed Answer Preview: {full_stream_answer[:200]}...")
    assert has_done, "Did not receive [DONE] signal"
    assert "sub_queries" in received_steps
    assert "retrieval" in received_steps
    assert "context_ready" in received_steps
    assert "answer" in received_steps
    assert len(full_stream_answer) > 0
    print(">>> POST /chat (SSE Streaming) PASSED! ✅")

if __name__ == "__main__":
    test_health()
    test_root()
    test_chat_empty_validation()
    test_chat_non_streaming()
    test_chat_streaming()
    print("\n=======================================================")
    print("🎉 ALL API & PROCEDURE RESOLUTION TESTS PASSED! ✅")
    print("=======================================================")
