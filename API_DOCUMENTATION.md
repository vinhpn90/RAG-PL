# Tài Liệu Hướng Dẫn Tích Hợp API - Trợ Lý Pháp Lý AI (Legal RAG Assistant)

Tài liệu này cung cấp hướng dẫn chi tiết về cách gọi API, cấu trúc dữ liệu gửi lên (Request), cấu trúc dữ liệu trả về (Response) và ý nghĩa chi tiết của từng trường cho hệ thống Trợ lý Pháp lý AI.

---

## 1. Thông Tin Chung (Overview)

- **Base URL:** `http://localhost:8000` (hoặc URL máy chủ triển khai)
- **Content-Type:** `application/json`
- **Giao thức hỗ trợ:**
  - **REST JSON (Non-streaming):** Trả về toàn bộ kết quả tổng hợp trong một JSON duy nhất (`stream: false`).
  - **Server-Sent Events / SSE (Streaming):** Truyền dữ liệu thời gian thực theo luồng (`stream: true`).

---

## 2. Endpoint Chính: Trò Chuyện Pháp Lý (`POST /chat`)

### 2.1. Dữ Liệu Gửi Lên (Request Body)

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Thủ tục đăng ký khai sinh như thế nào?"
    }
  ],
  "stream": true
}
```

#### Bảng Mô Tả Các Trường Request:

| Tên trường | Kiểu dữ liệu | Bắt buộc | Mô tả |
| :--- | :--- | :---: | :--- |
| `messages` | `Array<Object>` | **Có** | Lịch sử cuộc trò chuyện theo thứ tự thời gian. Hỗ trợ ngữ cảnh đa lượt (Multi-turn conversation). |
| `messages[].role` | `String` | **Có** | Vai trò của người nói. Chỉ chấp nhận một trong hai giá trị: `"user"` (người dùng) hoặc `"assistant"` (trợ lý AI). |
| `messages[].content` | `String` | **Có** | Nội dung tin nhắn / câu hỏi pháp lý. |
| `stream` | `Boolean` | Không | `true` (mặc định): Trả về theo luồng sự kiện SSE (Streaming).<br>`false`: Chờ xử lý xong và trả về 1 đối tượng JSON duy nhất. |

#### Ví dụ Request Đa Lượt (Multi-turn Conversation):
```json
{
  "messages": [
    { "role": "user", "content": "Thủ tục đăng ký khai sinh như thế nào?" },
    { "role": "assistant", "content": "Thủ tục đăng ký khai sinh bao gồm khai sinh trong nước và có yếu tố nước ngoài..." },
    { "role": "user", "content": "Trẻ sinh tại Việt Nam, có cha hoặc mẹ là người nước ngoài" }
  ],
  "stream": true
}
```

---

## 3. Dữ Liệu Trả Về - Chế Độ Non-Streaming (`stream: false`)

Khi gửi `stream: false`, API xử lý hoàn tất và trả về phản hồi định dạng `application/json`.

### 3.1. Ví Dụ Response JSON Đầy Đủ:
```json
{
  "final_answer": "Theo quy định của Luật Hộ tịch [1] và Nghị định số 123/2015/NĐ-CP [2], thủ tục đăng ký khai sinh được quy định cụ thể như sau:\n\n1. **Thẩm quyền đăng ký:** UBND cấp xã nơi cư trú của người cha hoặc người mẹ [1]...\n2. **Hồ sơ cần chuẩn bị:** Tờ khai đăng ký khai sinh, Giấy chứng sinh [2]...",
  "clarification": {
    "has_clarification": true,
    "title": "Bạn đang thuộc trường hợp nào?",
    "questions": [
      {
        "question": "Nơi sinh và quốc tịch của cha mẹ?",
        "options": [
          "Trẻ sinh tại Việt Nam, cha mẹ là công dân Việt Nam",
          "Trẻ sinh tại Việt Nam, có cha hoặc mẹ là người nước ngoài",
          "Trẻ sinh ở nước ngoài, về cư trú tại Việt Nam"
        ]
      },
      {
        "question": "Thời hạn và trường hợp đặc biệt?",
        "options": [
          "Đăng ký đúng hạn (trong vòng 60 ngày)",
          "Đăng ký quá hạn",
          "Đăng ký khai sinh kết hợp nhận cha mẹ con",
          "Trẻ bị bỏ rơi hoặc chưa xác định được cha mẹ"
        ]
      }
    ],
    "guide_message": "Vui lòng chọn trường hợp phù hợp bên dưới để tôi hướng dẫn chi tiết hồ sơ, thủ tục và cơ quan có thẩm quyền cho bạn."
  },
  "citations": {
    "1": {
      "chunk_id": "fbe53e50-2d29-11f1-abe9-991a68879e74",
      "score": 9.98,
      "metadata": {
        "doc_id": "46746",
        "doc_num": "60/2014/QH13",
        "title": "Luật Hộ tịch số 60/2014/QH13",
        "article": "Điều 16",
        "clause": "Khoản 1"
      },
      "content": "Luật Hộ tịch số 60/2014/QH13 - Điều 16. Thủ tục đăng ký khai sinh - Khoản 1: Người đi đăng ký khai sinh nộp tờ khai theo mẫu..."
    },
    "2": {
      "chunk_id": "d9bff440-2d52-11f1-9451-0bd8742a8422",
      "score": 9.54,
      "metadata": {
        "doc_id": "92897",
        "doc_num": "123/2015/NĐ-CP",
        "title": "Nghị định số 123/2015/NĐ-CP Quy định chi tiết thi hành Luật Hộ tịch",
        "article": "Điều 17",
        "clause": "Khoản 1"
      },
      "content": "Nghị định số 123/2015/NĐ-CP - Điều 17. Đăng ký khai sinh: Giấy tờ chứng minh nơi sinh..."
    }
  },
  "sources": [
    {
      "chunk_id": "fbe53e50-2d29-11f1-abe9-991a68879e74",
      "score": 9.98,
      "metadata": {
        "doc_id": "46746",
        "doc_num": "60/2014/QH13",
        "title": "Luật Hộ tịch số 60/2014/QH13",
        "article": "Điều 16",
        "clause": "Khoản 1"
      },
      "content": "..."
    }
  ],
  "sub_queries": [
    "Theo quy định của pháp luật Việt Nam, thủ tục đăng ký khai sinh trong nước...",
    "Theo quy định của pháp luật Việt Nam, thủ tục đăng ký khai sinh có yếu tố nước ngoài..."
  ],
  "steps": [
    { "step": "sub_queries", "status": "done", "data": { "queries": [...] } },
    { "step": "retrieval", "status": "done", "data": { "count": 14 } },
    { "step": "context_ready", "status": "done", "data": { "citations": {...}, "sources": [...] } },
    { "step": "clarification", "status": "done", "data": {...} },
    { "step": "answer", "status": "done", "data": { "text": "...", "citations": {...}, "clarification": {...} } }
  ]
}
```

### 3.2. Bảng Mô Tả Các Trường Response (Non-Streaming):

| Tên trường | Kiểu dữ liệu | Mô tả chi tiết |
| :--- | :--- | :--- |
| `final_answer` | `String` | Văn bản câu trả lời hoàn chỉnh của Trợ lý AI, định dạng Markdown, có gắn các mã trích dẫn `[1]`, `[2]`,... tương ứng với các nguồn trong `citations`. |
| `clarification` | `Object` hoặc `null` | **Cấu trúc làm rõ ý định / phân nhánh tình huống.** Trả về `null` nếu câu hỏi đã rõ ràng và không cần hỏi thêm. |
| `clarification.has_clarification` | `Boolean` | `true` nếu câu hỏi có nhiều phân nhánh và cần người dùng làm rõ. |
| `clarification.title` | `String` | Tiêu đề khối làm rõ (Ví dụ: *"Bạn đang thuộc trường hợp nào?"*). |
| `clarification.questions` | `Array<Object>` | Danh sách các nhóm câu hỏi kèm các phương án trả lời tương ứng. |
| `clarification.questions[].question` | `String` | Tiêu đề / câu hỏi phân loại của nhóm (Ví dụ: *"Nơi sinh và quốc tịch của cha mẹ?"*). |
| `clarification.questions[].options` | `Array<String>` | **Danh sách các câu trả lời / lựa chọn cụ thể** để người dùng có thể click chọn nhanh (Ví dụ: `["Trẻ sinh tại VN...", "Trẻ sinh ở nước ngoài..."]`). |
| `clarification.guide_message` | `String` | Lời nhắn hướng dẫn người dùng chọn hoặc cung cấp thông tin. |
| `citations` | `Object` (Map) | Danh mục tài liệu tham khảo được đánh số khóa `"1"`, `"2"`,... khớp chính xác với ký hiệu `[1]`, `[2]` trong `final_answer`. |
| `citations[key].chunk_id` | `String` | ID duy nhất của đoạn trích văn bản trong cơ sở dữ liệu. |
| `citations[key].score` | `Float` | Điểm độ tương đồng và xếp hạng phù hợp (Rerank score). |
| `citations[key].metadata` | `Object` | Thông tin pháp lý của đoạn trích (`doc_num`: Số hiệu, `title`: Tên văn bản, `article`: Điều, `clause`: Khoản). |
| `citations[key].content` | `String` | Nội dung văn bản luật đầy đủ của đoạn trích. |
| `sources` | `Array<Object>` | Danh sách tất cả các đoạn trích liên quan được truy xuất từ cơ sở dữ liệu. |
| `sub_queries` | `Array<String>` | Danh sách các truy vấn con do AI phân tách để tìm kiếm ngữ nghĩa chính xác hơn. |
| `steps` | `Array<Object>` | Lịch sử các bước xử lý chính của Pipeline (dùng cho log / hiển thị tiến trình). |

---

## 4. Dữ Liệu Trả Về - Chế Độ Streaming SSE (`stream: true`)

Khi gửi `stream: true`, API trả về luồng `text/event-stream`. Mỗi thông điệp bắt đầu bằng `data: <JSON>` và kết thúc bằng `data: [DONE]`.

### 4.1. Trình Tự Các Sự Kiện (Event Flow):

```
Client Gửi Request (stream: true)
       │
       ├─► 1. sub_queries (processing -> done)
       │     └─ Phân tích ý định & tách truy vấn tìm kiếm
       │
       ├─► 2. retrieval (processing -> done)
       │     └─ Tìm kiếm Vector pgvector + HNSW
       │
       ├─► 3. context_ready (done)
       │     └─ Trả về danh sách citations & sources cho sidebar
       │
       ├─► 4. tool_call (detected -> executed -> done) [Nếu có gọi tra cứu điều khoản]
       │
       ├─► 5. answer (start -> streaming: từng chunk)
       │     └─ Stream từng đoạn chữ nhỏ của câu trả lời
       │
       ├─► 6. clarification (done) [Nếu câu hỏi cần làm rõ]
       │     └─ Trả về đối tượng câu hỏi & options lựa chọn
       │
       ├─► 7. answer (done)
       │     └─ Trả về toàn bộ câu trả lời, citations và clarification hoàn tất
       │
       └─► 8. data: [DONE]
```

### 4.2. Chi Tiết Các Sự Kiện SSE:

#### 1. Bước phân tích truy vấn con (`sub_queries`):
```http
data: {"step": "sub_queries", "status": "processing", "data": null}

data: {"step": "sub_queries", "status": "done", "data": {"queries": ["Quy định đăng ký khai sinh trong nước...", "Quy định đăng ký khai sinh có yếu tố nước ngoài..."]}}
```

#### 2. Bước tìm kiếm tài liệu (`retrieval`):
```http
data: {"step": "retrieval", "status": "processing", "data": null}

data: {"step": "retrieval", "status": "done", "data": {"count": 14}}
```

#### 3. Bước tài liệu sẵn sàng (`context_ready`):
*Sự kiện này được phát ngay sau khi tìm kiếm xong để phía Client có thể hiển thị danh sách tài liệu tham khảo vào Sidebar trong lúc chờ AI viết câu trả lời.*
```http
data: {"step": "context_ready", "status": "done", "data": {
    "citations": { "1": { "metadata": {...}, "content": "..." } },
    "sources": [ {...} ]
}}
```

#### 4. Bước tra cứu văn bản bổ sung (`tool_call` - nếu có):
```http
data: {"step": "tool_call", "status": "detected", "data": {"args": {"doc_ref": "60/2014/QH13", "dieu_filter": "Điều 15"}}}

data: {"step": "tool_call", "status": "executed", "data": {"found_count": 3}}

data: {"step": "tool_call", "status": "done", "data": null}
```

#### 5. Bước sinh câu trả lời trực tiếp (`answer` - streaming):
*Mỗi chunk chỉ chứa đoạn text mới, cực kỳ nhẹ và tối ưu dung lượng mạng.*
```http
data: {"step": "answer", "status": "start", "data": null}

data: {"step": "answer", "status": "streaming", "data": {"chunk": "Theo"}}

data: {"step": "answer", "status": "streaming", "data": {"chunk": " quy định"}}

data: {"step": "answer", "status": "streaming", "data": {"chunk": " của Luật Hộ tịch [1]..."}}
```

#### 6. Bước làm rõ ý định / tùy chọn trường hợp (`clarification`):
```http
data: {"step": "clarification", "status": "done", "data": {
    "has_clarification": true,
    "title": "Bạn đang thuộc trường hợp nào?",
    "questions": [
      {
        "question": "Nơi sinh và quốc tịch của cha mẹ?",
        "options": [
          "Trẻ sinh tại Việt Nam, cha mẹ là công dân Việt Nam",
          "Trẻ sinh tại Việt Nam, có cha hoặc mẹ là người nước ngoài"
        ]
      }
    ],
    "guide_message": "Vui lòng chọn trường hợp phù hợp bên dưới..."
}}
```

#### 7. Bước hoàn tất câu trả lời (`answer` - done):
```http
data: {"step": "answer", "status": "done", "data": {
    "text": "Nội dung toàn bộ câu trả lời hoàn chỉnh...",
    "citations": { ... },
    "clarification": { ... }
}}

data: [DONE]
```

---

## 5. Ví Dụ Gọi API (Code Snippets)

### 5.1. Sử Dụng cURL

#### Chế độ Non-Streaming:
```bash
curl -X POST "http://localhost:8000/chat" \
     -H "Content-Type: application/json" \
     -d '{
       "messages": [{"role": "user", "content": "Thủ tục đăng ký khai sinh như thế nào?"}],
       "stream": false
     }'
```

#### Chế độ Streaming:
```bash
curl -N -X POST "http://localhost:8000/chat" \
     -H "Content-Type: application/json" \
     -d '{
       "messages": [{"role": "user", "content": "Thủ tục đăng ký khai sinh như thế nào?"}],
       "stream": true
     }'
```

---

### 5.2. Sử Dụng Python

```python
import requests
import json

API_URL = "http://localhost:8000/chat"

# 1. Gọi Non-Streaming
def chat_non_stream(prompt: str):
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    response = requests.post(API_URL, json=payload)
    data = response.json()
    
    print("=== CÂU TRẢ LỜI ===")
    print(data["final_answer"])
    
    if data.get("clarification"):
        print("\n=== CÂU HỎI LÀM RÕ / LỰA CHỌN ===")
        for item in data["clarification"]["questions"]:
            print(f"- {item['question']}:")
            for opt in item["options"]:
                print(f"  * {opt}")

# 2. Gọi Streaming (SSE)
def chat_stream(prompt: str):
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "stream": True
    }
    response = requests.post(API_URL, json=payload, stream=True)
    
    for line in response.iter_lines():
        if not line:
            continue
        line_str = line.decode('utf-8')
        if line_str.startswith('data: '):
            raw = line_str[6:]
            if raw == '[DONE]':
                print("\n[Đã nhận xong toàn bộ câu trả lời]")
                break
            
            event = json.loads(raw)
            step = event.get("step")
            status = event.get("status")
            
            if step == "answer" and status == "streaming":
                print(event["data"]["chunk"], end="", flush=True)
            elif step == "clarification" and status == "done":
                print("\n\n[Đã nhận tùy chọn làm rõ]:", event["data"]["title"])

if __name__ == "__main__":
    chat_stream("Tôi muốn làm thủ tục đăng ký khai sinh")
```

---

### 5.3. Sử Dụng JavaScript / TypeScript (Frontend Fetch API)

```javascript
async function askLegalAssistant(questionHistory) {
    const response = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            messages: questionHistory,
            stream: true
        })
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith('data: ') || trimmed === 'data: [DONE]') continue;

            const event = JSON.parse(trimmed.slice(6));
            
            // Xử lý stream text câu trả lời
            if (event.step === 'answer' && event.status === 'streaming') {
                process.stdout.write(event.data.chunk);
            }
            
            // Xử lý khi có khối câu hỏi làm rõ
            if (event.step === 'clarification' && event.status === 'done') {
                renderClarificationOptions(event.data);
            }
        }
    }
}
```

---

## 6. Endpoint Kiểm Tra Trạng Thái (`GET /health`)

- **URL:** `/health`
- **Method:** `GET`
- **Response:**
  ```json
  {
    "status": "ok"
  }
  ```
