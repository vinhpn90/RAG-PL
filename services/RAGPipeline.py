import os
import json
import re
from typing import List, Dict, Any, Generator, Optional
from services.Chat import ChatService
from services.Search import SearchService
from services.ProcedureService import ProcedureService
from services.ProcedureFilterAgent import ProcedureFilterAgent

# Định nghĩa Tool cho LLM
SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_referenced_document",
            "description": (
                "Tìm kiếm nội dung cụ thể trong một văn bản pháp luật được trích dẫn. "
                "Sử dụng KHI VÀ CHỈ KHI ngữ cảnh hiện tại nhắc đến một văn bản khác (vd: Luật X, Thông tư Y) "
                "và bạn BẮT BUỘC cần chi tiết từ văn bản đó để trả lời chính xác câu hỏi."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_ref": {
                        "type": "string",
                        "description": "Số hiệu văn bản pháp luật ĐẦY ĐỦ (ví dụ: '36/2015/QĐ-TTg'). KHÔNG điền [1], [2].",
                    },
                    "dieu_filter": {
                        "type": "string",
                        "description": "(Tùy chọn) Chỉ ghi số điều, ví dụ 'Điều 74'.",
                    },
                    "khoan_filter": {
                        "type": "string",
                        "description": "(Tùy chọn) Chỉ ghi số khoản, ví dụ 'Khoản 3'.",
                    },
                    "content_query": {
                        "type": "string",
                        "description": "(Bắt buộc) Từ khóa hoặc chủ đề cần tìm trong văn bản đó.",
                    },
                },
                "required": ["doc_ref", "content_query"],
            },
        },
    }
]

SUB_QUERY_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "sub_queries",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "reasoning": {"type": "string",'description': "suy luận ngắn gọn"},
                "queries": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["queries"],
            "additionalProperties": True
        }
    }
}


class RAGPipeline:
    def __init__(self):
        self.chat_service = ChatService()
        self.search_service = SearchService()
        self.procedure_service = ProcedureService()
        self.procedure_filter_agent = ProcedureFilterAgent()
        # Giới hạn số chunk tối đa trong context để tránh tràn token
        self.MAX_CONTEXT_CHUNKS = 50
        self.MAX_TOOL_ITERATIONS = 3



    def _format_context(self, docs: List[Dict]) -> str:
        """Format context thành dạng [1]: content, [2]: content... kèm thông tin văn bản nguồn rõ ràng."""
        if not docs:
            return "Không có thông tin ngữ cảnh nào."
        formatted = []
        for i, d in enumerate(docs):
            meta = d.get("metadata", {})
            title = meta.get("title") or meta.get("doc_num") or ""
            article = meta.get("article") or ""
            clause = meta.get("clause") or ""
            content = d.get("content", "").strip()

            # Nếu content chưa có header tên văn bản, bổ sung để LLM nhận biết chính xác phạm vi văn bản
            if title and not content.startswith(title[:30]):
                header_parts = [p for p in [title, article, clause] if p]
                full_chunk = f"[{' - '.join(header_parts)}]\n{content}"
            else:
                full_chunk = content

            formatted.append(f"[{i + 1}]: {full_chunk}")
        return "\n\n".join(formatted)

    def _deduplicate_docs(self, docs: List[Dict]) -> List[Dict]:
        """Loại bỏ các document trùng lặp dựa trên chunk_id."""
        seen = set()
        unique_docs = []
        for doc in docs:
            doc_id = doc.get('chunk_id') or hash(doc.get('content', ''))
            if doc_id not in seen:
                seen.add(doc_id)
                unique_docs.append(doc)
        return unique_docs



    def _get_last_user_question(self, messages: List[Dict[str, str]]) -> str:
        """Lấy câu hỏi mới nhất của user, dùng để log / fallback."""
        for m in reversed(messages):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""


    def _extract_clarification(self, text: str):
        """Bóc tách phần JSON clarification ở cuối câu trả lời nếu có."""
        tag_start = "<<<CLARIFICATION>>>"
        tag_end = "<<<END_CLARIFICATION>>>"
        if tag_start in text:
            parts = text.split(tag_start, 1)
            main_text = parts[0].strip()
            json_str = parts[1]
            if tag_end in json_str:
                json_str = json_str.split(tag_end, 1)[0].strip()
            else:
                json_str = json_str.strip()
            try:
                json_clean = re.sub(r"^```(?:json)?\s*", "", json_str, flags=re.IGNORECASE)
                json_clean = re.sub(r"\s*```$", "", json_clean).strip()
                clarification_obj = json.loads(json_clean)
                if clarification_obj.get("has_clarification") or clarification_obj.get("questions"):
                    return main_text, clarification_obj
            except Exception as e:
                print(f"[RAGPipeline] Lỗi parse clarification JSON: {e}")
            return main_text, None
        return text.strip(), None

    def _build_context_message(self, context_docs: List[Dict]) -> Dict[str, str]:
        """Tạo 1 system/user message chứa ngữ cảnh + quy tắc trích dẫn + quy tắc làm rõ ý định,
        được chèn vào NGAY TRƯỚC lượt hội thoại của user để LLM luôn thấy
        context mới nhất mà không phá vỡ cấu trúc nhiều lượt hội thoại."""
        context_text = self._format_context(context_docs)
        return {
            "role": "system",
            "content": f"""Bạn là trợ lý pháp lý AI chuyên nghiệp về pháp luật Việt Nam. Hãy trả lời chính xác, rõ ràng và có căn cứ dựa trên ngữ cảnh được cung cấp.

NGỮ CẢNH PHÁP LÝ:
{context_text}

QUY TẮC ĐỐI SOÁT PHẠM VI ÁP DỤNG & TÍNH NHẤT QUÁN (QUAN TRỌNG - CHỐNG ẢO GIÁC/GẮN SAI NHÃN):
1. TÍNH NHẤT QUÁN GIỮA NGUỒN TRÍCH DẪN VÀ NỘI DUNG/TIÊU ĐỀ PHÂN LOẠI:
   - Mọi phân loại, tiêu đề đề mục, điều kiện áp dụng hoặc kết luận trong câu trả lời PHẢI phản ánh chính xác phạm vi điều chỉnh và đối tượng áp dụng được nêu trong văn bản nguồn trích dẫn [N].
   - TUYỆT ĐỐI KHÔNG trích xuất quy định áp dụng cho một nhóm đối tượng, điều kiện hoặc trường hợp đặc thù này nhưng lại gắn nhãn hoặc đặt dưới tiêu đề/phân loại của một nhóm đối tượng hoặc trường hợp khác.
   - Khi văn bản có các trường hợp loại trừ, điều kiện áp dụng riêng biệt hoặc phạm vi áp dụng cụ thể, phải nêu rõ ràng, đúng ngữ cảnh của từng điều khoản.
2. TÍNH CHÍNH XÁC VỀ THẨM QUYỀN, TRÌNH TỰ VÀ THỜI HẠN:
   - Phải trình bày chính xác cơ quan có thẩm quyền giải quyết, trình tự thủ tục, hồ sơ và thời hạn xử lý theo đúng từng văn bản quy định, không tùy tiện suy diễn hoặc hoán đổi giữa các quy trình khác nhau.

QUY TẮC XỬ LÝ CÂU HỎI & LÀM RÕ Ý ĐỊNH:
1. Xử lý câu hỏi có nhiều trường hợp / phân nhánh:
   - Nếu nội dung câu hỏi của người dùng có nhiều trường hợp hoặc phân nhánh pháp lý khác nhau mà người dùng chưa nêu rõ:
     + Trả lời tóm lược/khái quát các trường hợp chính theo quy định pháp luật dựa trên ngữ cảnh (kèm trích dẫn nguồn [N]).
     + Ở CUỐI CÙNG của câu trả lời, BẮT BUỘC chèn khối thông tin làm rõ theo đúng định dạng sau, với các câu hỏi và tùy chọn được sinh động phù hợp trực tiếp với các phân nhánh của câu hỏi hiện tại:
<<<CLARIFICATION>>>
{{
  "has_clarification": true,
  "title": "<Tiêu đề ngắn gọn xác định trường hợp>",
  "questions": [
    {{
      "question": "<Câu hỏi phân loại trường hợp cụ thể dựa trên vấn đề đang hỏi>",
      "options": [
        "<Tùy chọn trường hợp 1>",
        "<Tùy chọn trường hợp 2>"
      ]
    }}
  ],
  "guide_message": "Vui lòng chọn trường hợp phù hợp bên dưới để tôi hướng dẫn chi tiết theo đúng quy định cho bạn."
}}
<<<END_CLARIFICATION>>>

2. Xử lý khi người dùng đã rõ trường hợp:
   - Nếu người dùng đã nêu rõ trường hợp cụ thể (ngay trong câu hỏi hoặc qua các lượt trao đổi trước đó), hãy trả lời trực tiếp, đầy đủ và chi tiết cho trường hợp đó. TUYỆT ĐỐI KHÔNG chèn khối <<<CLARIFICATION>>>.

QUY TẮC ĐỊNH DẠNG & TRÌNH BÀY (BẮT BUỘC CHUẨN HÓA PHÂN CẤP ĐẦU MỤC):
1. LỜI MỞ ĐẦU THÂN THIỆN, HÀI HƯỚC & NHIỆT TÌNH:
   - Trước khi đi vào nội dung phân tích chi tiết, hãy LUÔN mở đầu bằng 01 câu ngắn gọn, tự nhiên, thể hiện sự sẵn lòng lắng nghe, nhiệt tình giúp đỡ (có thể sử dụng một vài từ ngữ vui vẻ, hóm hỉnh, tích cực để tạo không khí thoải mái nhưng vẫn giữ sự tôn trọng và chuyên nghiệp).
   - Ví dụ:
     + *"Dạ vâng, chuyện trọng đại thế này cứ để em cùng đồng hành gỡ rối pháp lý với bạn nhé!"*
     + *"Chào bạn! Vấn đề này tuy nhiều điều khoản nhưng đừng lo, tôi sẽ tóm tắt thật dễ hiểu ngay dưới đây nhé!"*
     + *"Rất vui được hỗ trợ bạn! Chúng ta cùng điểm qua các bước chuẩn chỉnh theo đúng quy định pháp luật nhé!"*
2. PHÂN CẤP ĐẦU MỤC CHUẨN XÁC TỪ LỚN ĐẾN NHỎ:
   - Cấp 1 (Chủ đề lớn / Các trường hợp chính / Tình huống phân loại): Dùng chữ cái in hoa in đậm **A., B., C...**
   - Cấp 2 (Nội dung chính / Các bước thực hiện / Đề mục con): Dùng chữ số thứ tự in đậm **1., 2., 3...**
   - Cấp 3 (Các ý nhỏ / Hồ sơ chi tiết / Điều kiện áp dụng): Dùng gạch đầu dòng **-** (dấu trừ)
   - Cấp 4 (Chi tiết giải thích thêm / Ý phụ của từng mục): Dùng dấu cộng **+** (thụt dòng rõ ràng)
3. SỬ DỤNG BẢNG (MARKDOWN TABLE) HỢP LÝ & ĐẸP MẮT:
   - Khuyến khích sử dụng Bảng (Markdown table) khi cần so sánh giữa các trường hợp, tổng hợp danh mục hồ sơ, phân chia thẩm quyền hoặc thời hạn giải quyết để thông tin trực quan, dễ theo dõi.
   - Bảng phải có hàng tiêu đề rõ ràng, các cột căn chỉnh ngắn gọn, súc tích và có gắn mã trích dẫn [N].
4. TÍNH CHUẨN XÁC & RÕ RÀNG:
   - Sử dụng in đậm (**...**) cho các từ khóa quan trọng (tên cơ quan thẩm quyền, thời hạn giải quyết, điều kiện tiên quyết, giấy tờ bắt buộc).
   - Phần nội dung quy định pháp lý giữ văn phong chuẩn mực, gãy gọn, có căn cứ.
5. TUYỆT ĐỐI KHÔNG DÙNG EMOJI/ICON ĐÁNH SỐ TRONG NỘI DUNG PHÁP LÝ:
   - TUYỆT ĐỐI KHÔNG dùng các icon emoji số hiệu như 1️⃣, 2️⃣, 3️⃣, 4️⃣... hoặc các icon biểu tượng trang trí (🔹, 🔸, 📌, ⚖️...) trong nội dung các bước, bảng biểu hoặc điều khoản.
   - BẮT BUỘC dùng ký tự số và chữ chuẩn: chữ số thông thường (1., 2., 3...), chữ cái (A., B., C...), dấu gạch ngang (-) và dấu cộng (+). Emoji chỉ được dùng tối đa 1 icon ở câu chào mở đầu, còn toàn bộ nội dung phân tích pháp lý phải nghiêm túc, chuẩn mực.

QUY TẮC TRÍCH DẪN BẮT BUỘC:
- Mọi thông tin lấy từ ngữ cảnh đều phải trích dẫn nguồn.
- Sử dụng CHÍNH XÁC định dạng [N] (ví dụ: [1], [2], [3]).
- KHÔNG thêm khoảng trắng (không dùng [ 1 ]), KHÔNG dùng định dạng khác.
- Đặt mã trích dẫn ở cuối câu hoặc cuối ý tương ứng.
- Nếu ngữ cảnh NHẮC ĐẾN một văn bản khác (vd: "theo Luật X") và bạn CẦN chi tiết từ văn bản đó để trả lời
  chính xác, hãy gọi tool `search_referenced_document` thay vì trả lời ngay.
- Nếu không tìm thấy thông tin trong ngữ cảnh, hãy nói rõ là không có thông tin.
- Hãy tham khảo các lượt hội thoại trước đó để hiểu đúng mạch trao đổi của người dùng, nhưng chỉ trích dẫn [N]
  cho thông tin lấy từ ngữ cảnh pháp lý ở trên.""",
        }

    def process(self, messages: List[Dict[str, str]], stream: bool = True) -> Generator[Dict[str, Any], None, None]:
        """Pipeline xử lý chính.

        messages: lịch sử hội thoại dạng [{"role": "user"/"assistant", "content": "..."}]
        theo đúng thứ tự thời gian, không cần chứa system prompt (pipeline tự thêm).
        """

        system_prompt = (
            "Bạn là trợ lý pháp lý AI chuyên nghiệp. Hãy trả lời chính xác, chuyên nghiệp dựa trên ngữ cảnh được cung cấp. "
            "Nếu không tìm thấy thông tin trong ngữ cảnh, hãy nói rõ là không có thông tin."
        )

        # Lọc bỏ mọi system message người dùng gửi lên (pipeline tự quản lý system prompt)
        conversation = [m for m in messages if m.get("role") in ("user", "assistant") and m.get("content")]
        if not conversation:
            yield {"step": "answer", "status": "error", "data": {"error": "Không có nội dung hội thoại hợp lệ."}}
            return

        question = self._get_last_user_question(conversation)
        

        # ==========================================
        # BƯỚC 1: SUB-QUERY (Phân tích câu hỏi, dựa trên TOÀN BỘ hội thoại)
        # ==========================================
        yield {"step": "sub_queries", "status": "processing", "data": None}

        sub_query_prompt = (
            f"Hãy phân tích đoạn hội thoại sau, tập trung vào ý định mới nhất của người dùng, "
            f"và tách câu hỏi thành các sub-queries để tìm kiếm thông tin hiệu quả hơn.\n\n"
            f"QUY TẮC BẮT BUỘC:\n"
            f"1. Mỗi sub-query PHẢI là một câu hỏi ĐẦY ĐỦ NGỮ CẢNH, hiểu được độc lập mà không cần đọc "
            f"các sub-query khác. Nếu câu hỏi gốc có chủ thể/điều kiện chung (đối tượng áp dụng, điều kiện, "
            f"mốc thời gian, loại hình văn bản/thủ tục...), BẮT BUỘC phải LẶP LẠI (chèn lại) điều kiện đó "
            f"vào TỪNG sub-query được tách ra — không được lược bỏ dù đã nêu ở sub-query trước.\n"
            f"2. Nếu câu hỏi của người dùng mang tính tổng quát nhưng có nhiều phân nhánh pháp lý theo quy định, "
            f"hãy tạo các sub-query bao quát quy định chung và các trường hợp chính để thu thập đủ tài liệu pháp lý.\n"
            f"3. Chỉ tách thành nhiều sub-query khi các ý có thể tìm kiếm ĐỘC LẬP mà không mất nghĩa.\n"
            f"4. Nếu câu hỏi gốc chỉ có MỘT ý cụ thể, hoặc các ý nhỏ gắn chặt với nhau, hãy trả về DUY NHẤT 1 sub-query "
            f"đầy đủ nghĩa thay vì cố tách nhỏ.\n\n"
            f"Trả về kết quả dưới dạng JSON thuần túy với key 'queries'.\n\n"
        )

        try:
            sub_query_response = ""
            # stream=False -> ChatService yield đúng 1 lần: response.choices[0].message
            for message in self.chat_service.generate_response(
                messages=[
                    {"role": "system", "content": system_prompt + sub_query_prompt},
                    *messages
                ],
                
                response_format=SUB_QUERY_SCHEMA,
                stream=False,
            ):
                
                sub_query_response = json.loads(message.content)

            sub_queries = sub_query_response.get("queries",[])
        except Exception as e:
            print(f"Lỗi khi parse sub-queries: {e}")
            sub_queries = [question]

        yield {"step": "sub_queries", "status": "done", "data": {"queries": sub_queries}}

        # ==========================================
        # BƯỚC 2: SEARCH (Semantic Search ban đầu)
        # ==========================================
        yield {"step": "retrieval", "status": "processing", "data": None}

        retrieved_docs = []
        for sq in sub_queries:
            try:
                docs = self.search_service.semantic_search(query=sq, top_k=10)
                retrieved_docs.extend(docs)
            except Exception as e:
                print(f"Lỗi search cho query '{sq}': {e}")

        unique_docs = self._deduplicate_docs(retrieved_docs)
        context_docs = unique_docs[:self.MAX_CONTEXT_CHUNKS]

        yield {
            "step": "retrieval",
            "status": "done",
            "data": {"count": len(context_docs)},
        }
        citation_map: Dict[str, Any] = {str(i + 1): d for i, d in enumerate(context_docs)}

        # ==========================================
        # BƯỚC 2.5: CONTEXT READY
        # ------------------------------------------
        # Trả ra citations/sources NGAY khi vừa retrieval xong, TRƯỚC khi
        # LLM bắt đầu trả lời — để sidebar tài liệu tham khảo hiện lên sớm
        # cho người dùng xem trong lúc chờ LLM sinh câu trả lời.
        #
        # QUAN TRỌNG: KHÔNG dùng step="answer", status="done" ở đây, vì đó
        # là tín hiệu "câu trả lời đã hoàn tất" thật sự ở cuối luồng — nếu
        # dùng trùng, frontend sẽ tưởng câu trả lời xong ngay từ đầu (trong
        # khi "text" chưa tồn tại) và có thể tắt luôn UI streaming.
        # Dùng step riêng "context_ready" để frontend cập nhật sidebar mà
        # không đụng vào logic xử lý "answer".
        # ==========================================
        yield {
            "step": "context_ready",
            "status": "done",
            "data": {
                "citations": citation_map,
                "sources": context_docs,
            },
        }

        # ==========================================
        # BƯỚC 2.6: CANDIDATE PROCEDURES
        # ------------------------------------------
        # Lấy danh sách thủ tục hành chính ứng viên từ căn cứ pháp lý
        # (Sẽ được AI Filter Agent thẩm định sau khi có câu trả lời)
        # ==========================================
        doc_nums = [
            d.get("metadata", {}).get("doc_num")
            for d in context_docs
            if d.get("metadata", {}).get("doc_num")
        ]
        candidate_procedures = self.procedure_service.get_related_procedures(
            doc_nums=doc_nums,
            user_query=question,
            top_k=8
        )

        # ==========================================================
        # BƯỚC 3+4 (GỘP): LLM STREAM — vừa quyết định tool call vừa
        # trả lời trực tiếp trong CÙNG một lần gọi, giống code mẫu.
        # Lặp tối đa MAX_TOOL_ITERATIONS lần nếu LLM liên tục gọi tool.
        # ==========================================================
        # Cấu trúc: [system prompt, system context+quy tắc trích dẫn, ...toàn bộ hội thoại gốc]
        # Giữ nguyên multi-turn để LLM hiểu đúng mạch hội thoại, thay vì gộp hết vào 1 user message.
        llm_messages = [
            # {"role": "system", "content": system_prompt},
            self._build_context_message(context_docs),
            *conversation,
        ]

        full_answer = ""
        streamed_len = 0

        for iteration in range(self.MAX_TOOL_ITERATIONS):
            did_tool_call = False
            did_content = False

            # Buffer để gom các mảnh tool_call arguments bị chia nhỏ qua nhiều chunk
            # key = index của tool call trong response (OpenAI có thể trả nhiều tool_calls song song)
            tool_call_buffers: Dict[int, Dict[str, Any]] = {}

            try:
                response_stream = self.chat_service.generate_response(
                    messages=llm_messages,
                    tools=SEARCH_TOOLS,
                    stream=True,
                )
            except Exception as e:
                yield {"step": "answer", "status": "error", "data": {"error": str(e)}}
                return

            if iteration == 0:
                yield {"step": "tool_call", "status": "processing", "data": None}
                yield {"step": "answer", "status": "start", "data": None}

            try:
                for chunk in response_stream:
                    # ChatService yield {"error": ...} thay vì raise khi có lỗi ở giữa stream
                    if isinstance(chunk, dict) and "error" in chunk:
                        raise Exception(chunk["error"])
                    if not getattr(chunk, "choices", None):
                        continue
                    delta = chunk.choices[0].delta

                    # --- Trả lời trực tiếp (không cần tool) ---
                    if getattr(delta, "content", None):
                        did_content = True
                        piece = delta.content
                        full_answer += piece

                        # Chỉ stream đoạn text nằm TRƯỚC thẻ <<<CLARIFICATION>>>
                        tag_start = "<<<CLARIFICATION>>>"
                        if tag_start in full_answer:
                            visible_text = full_answer.split(tag_start, 1)[0]
                        else:
                            # Tránh stream dở dang nếu thẻ đang hình thành ở cuối chuỗi
                            if "<" in full_answer[-20:]:
                                idx = full_answer.rfind("<")
                                if tag_start.startswith(full_answer[idx:]):
                                    visible_text = full_answer[:idx]
                                else:
                                    visible_text = full_answer
                            else:
                                visible_text = full_answer

                        if len(visible_text) > streamed_len:
                            chunk_to_send = visible_text[streamed_len:]
                            streamed_len = len(visible_text)
                            yield {
                                "step": "answer",
                                "status": "streaming",
                                "data": {
                                    "chunk": chunk_to_send,
                                    "citations": citation_map,
                                },
                            }

                    # --- Tool call (có thể tới theo từng mảnh nhỏ) ---
                    if getattr(delta, "tool_calls", None):
                        did_tool_call = True
                        for tc_delta in delta.tool_calls:
                            idx = tc_delta.index
                            if idx not in tool_call_buffers:
                                tool_call_buffers[idx] = {
                                    "id": tc_delta.id or "",
                                    "name": "",
                                    "arguments": "",
                                }
                            buf = tool_call_buffers[idx]
                            if tc_delta.id:
                                buf["id"] = tc_delta.id
                            if tc_delta.function and tc_delta.function.name:
                                buf["name"] += tc_delta.function.name
                            if tc_delta.function and tc_delta.function.arguments:
                                buf["arguments"] += tc_delta.function.arguments

            except Exception as e:
                yield {"step": "answer", "status": "error", "data": {"error": str(e)}}
                return

            # Nếu vòng này LLM không gọi tool -> đã trả lời xong, thoát loop
            if not did_tool_call:
                break

            # ---- Xử lý các tool call đã gom được ----
            assistant_tool_calls = []
            for idx in sorted(tool_call_buffers.keys()):
                buf = tool_call_buffers[idx]
                assistant_tool_calls.append({
                    "id": buf["id"],
                    "type": "function",
                    "function": {
                        "name": buf["name"],
                        "arguments": buf["arguments"],
                    },
                })

            # Thêm assistant message chứa tool_calls vào history (bắt buộc theo chuẩn OpenAI)
            llm_messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": assistant_tool_calls,
            })

            for tc in assistant_tool_calls:
                if tc["function"]["name"] != "search_referenced_document":
                    # tool lạ, bỏ qua an toàn
                    llm_messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": "Tool không được hỗ trợ.",
                    })
                    continue

                try:
                    args = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                yield {"step": "tool_call", "status": "detected", "data": {"args": args}}

                try:
                    extra_docs = self.search_service.doc_ref_search(
                        query=args.get("content_query", question),
                        doc_ref=args.get("doc_ref"),
                        article_filter=args.get("dieu_filter"),
                        clause_filter=args.get("khoan_filter"),
                        top_k=5,
                    )
                except Exception as e:
                    print(f"Lỗi thực thi tool: {e}")
                    extra_docs = []
                    yield {"step": "tool_call", "status": "error", "data": {"error": str(e)}}

                if extra_docs:
                    context_docs = self._deduplicate_docs(context_docs + extra_docs)[:self.MAX_CONTEXT_CHUNKS]
                    citation_map = {str(i + 1): d for i, d in enumerate(context_docs)}
                    yield {"step": "tool_call", "status": "executed", "data": {"found_count": len(extra_docs)}}

                    # Context vừa được bổ sung -> phát lại "context_ready" để
                    # frontend cập nhật sidebar với danh sách tài liệu mới nhất.
                    yield {
                        "step": "context_ready",
                        "status": "done",
                        "data": {
                            "citations": citation_map,
                            "sources": context_docs,
                        },
                    }

                    tool_result_content = (
                        f"Đã tìm thấy {len(extra_docs)} đoạn trích từ văn bản {args.get('doc_ref')}. "
                        f"Ngữ cảnh đầy đủ đã được cập nhật ở lượt tiếp theo."
                    )
                else:
                    yield {"step": "tool_call", "status": "executed", "data": {"found_count": 0, "message": "Không tìm thấy thông tin"}}
                    tool_result_content = f"Không tìm thấy thông tin bổ sung trong văn bản {args.get('doc_ref')}."

                llm_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result_content,
                })

            # Cập nhật lại phần "ngữ cảnh" cho lượt gọi tiếp theo bằng cách
            # thêm 1 user message mới chứa context đã bổ sung, để model
            # thực sự "nhìn thấy" nội dung mới lấy được (không chỉ là message
            # thông báo suông ở trên).
            llm_messages.append({
                "role": "user",
                "content": (
                    f"Đây là ngữ cảnh đầy đủ đã được cập nhật sau khi tra cứu thêm:\n\n"
                    f"{self._format_context(context_docs)}\n\n"
                    f"Hãy trả lời câu hỏi gốc: {question}\n"
                    f"Nhớ tuân thủ quy tắc trích dẫn [N] như đã nêu. Nếu vẫn còn thiếu thông tin quan trọng "
                    f"và cần tra cứu thêm văn bản khác, hãy tiếp tục gọi tool."
                ),
            })

            yield {"step": "tool_call", "status": "done", "data": None}
            # loop tiếp -> gọi lại LLM với context mới

        # ==========================================
        # KẾT THÚC: Bóc tách clarification và phát tín hiệu answer/done
        # ==========================================
        clean_answer, clarification = self._extract_clarification(full_answer)

        if clarification and (clarification.get("has_clarification") or clarification.get("questions")):
            yield {
                "step": "clarification",
                "status": "done",
                "data": clarification,
            }

        # ==========================================
        # BƯỚC 4.5: AI AGENT THẨM ĐỊNH & LỌC THỦ TỤC
        # ------------------------------------------
        # Dùng LLM chuyên trách thẩm định câu hỏi + câu trả lời để loại bỏ thủ tục rác
        # ==========================================
        filtered_procedures = []
        if candidate_procedures and clean_answer:
            try:
                filtered_procedures = self.procedure_filter_agent.filter_procedures(
                    user_query=question,
                    chatbot_answer=clean_answer,
                    candidate_procedures=candidate_procedures
                )
            except Exception as e:
                print(f"[RAGPipeline] Lỗi filter procedures: {e}")
                filtered_procedures = candidate_procedures[:2]

        if filtered_procedures:
            yield {
                "step": "related_procedures",
                "status": "done",
                "data": {
                    "count": len(filtered_procedures),
                    "procedures": filtered_procedures,
                },
            }

        yield {
            "step": "answer",
            "status": "done",
            "data": {
                "text": clean_answer,
                "citations": citation_map,
                "clarification": clarification,
                "related_procedures": filtered_procedures,
            },
        }