"""
Procedure Filter AI Agent:
- Thẩm định chuyên sâu và lọc danh sách Thủ tục hành chính ứng viên
- Dựa trên câu hỏi của người dùng và câu trả lời giải đáp pháp lý của Chatbot
- Loại bỏ các thủ tục không phù hợp, chỉ giữ lại các thủ tục thực sự cần thiết kèm lý do
"""

import os
import re
import json
from typing import List, Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts")

def _load_prompt(filename: str, fallback: str = "") -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception as e:
        print(f"[ProcedureFilterAgent] Lỗi đọc prompt '{filename}': {e}")
    return fallback

SYSTEM_PROMPT = _load_prompt("procedure_filter_prompt.md", fallback="""Bạn là Chuyên gia thẩm định Thủ tục hành chính Việt Nam.""")



class ProcedureFilterAgent:
    def __init__(self):
        self.api_key = os.getenv("CHAT_API_KEY") or os.getenv("LLM_API_KEY", "EMPTY")
        self.base_url = os.getenv("CHAT_BASE_URL") or os.getenv("LLM_BASE_URL", "http://103.143.202.2:8686/v1")
        self.model_name = os.getenv("CHAT_MODEL_NAME") or os.getenv("LLM_MODEL_NAME", "Ornith-1.0-35B")
        self.client = OpenAI(
            api_key=self.api_key or "EMPTY",
            base_url=self.base_url,
            timeout=15.0
        )

    def filter_procedures(
        self,
        user_query: str,
        chatbot_answer: str,
        candidate_procedures: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Dùng LLM thẩm định và lọc danh sách candidate_procedures.
        """
        if not candidate_procedures or not user_query:
            return []

        # Chuẩn bị danh sách ứng viên tóm tắt để tiết kiệm token
        candidates_summary = []
        candidates_map = {}
        for p in candidate_procedures:
            p_id = str(p.get("procedure_id", ""))
            candidates_map[p_id] = p
            candidates_summary.append({
                "procedure_id": p_id,
                "procedure_code": p.get("procedure_code", ""),
                "procedure_name": p.get("procedure_name", ""),
                "field": p.get("field", ""),
                "implementing_authority": p.get("implementing_authority", "")
            })

        user_content = f"""CÂU HỎI CỦA NGƯỜI DÙNG:
{user_query}

NỘI DUNG GIẢI ĐÁP PHÁP LÝ CỦA TRỢ LÝ:
{chatbot_answer[:800]}

DANH SÁCH THỦ TỤC HÀNH CHÍNH ỨNG VIÊN ({len(candidates_summary)} thủ tục):
{json.dumps(candidates_summary, ensure_ascii=False, indent=2)}

Hãy thẩm định và trả về danh sách JSON các thủ tục thực sự phù hợp:"""

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content}
                ],
                temperature=0.0,
                max_tokens=600
            )

            raw_output = response.choices[0].message.content or ""
            
            # Bóc tách JSON
            json_text = raw_output.strip()
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_text, re.DOTALL)
            if m:
                json_text = m.group(1)
            else:
                m_direct = re.search(r"\{.*\}", json_text, re.DOTALL)
                if m_direct:
                    json_text = m_direct.group(0)

            parsed = json.loads(json_text)
            selected_items = parsed.get("selected_procedures", [])

            filtered_results = []
            for item in selected_items:
                p_id = str(item.get("procedure_id", ""))
                reason = item.get("relevance_reason", "")
                if p_id in candidates_map:
                    original_proc = dict(candidates_map[p_id])
                    original_proc["relevance_reason"] = reason
                    filtered_results.append(original_proc)

            return filtered_results

        except Exception as e:
            print(f"[ProcedureFilterAgent] Lỗi khi LLM thẩm định thủ tục: {e}")
            # Fallback: Trả về top 1-2 thủ tục có điểm liên quan cao nhất nếu có
            return candidate_procedures[:2]
