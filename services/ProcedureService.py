"""
Procedure Resolution Service:
- Liên kết các văn bản pháp luật tìm thấy (doc_nums) với bảng legal_bases và procedures
- Chấm điểm độ liên quan (Relevance Scoring & Reranking)
- Trả ra danh sách thủ tục hành chính đầy đủ thông tin hỗ trợ người dân chuẩn bị nộp hồ sơ
"""

import os
import re
import psycopg2
from psycopg2 import pool
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

load_dotenv()

POSTGRES_USER = os.getenv("POSTGRES_USER", "vector_admin_99")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "P@ssw0rd_Str0ng_V3ct0r_16#")
POSTGRES_DB = os.getenv("POSTGRES_DB", "vector_db_test")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))


class ProcedureService:
    def __init__(self):
        try:
            self.db_pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                dbname=POSTGRES_DB,
                user=POSTGRES_USER,
                password=POSTGRES_PASSWORD,
                host=POSTGRES_HOST,
                port=POSTGRES_PORT,
                connect_timeout=5
            )
        except Exception as e:
            print(f"[ProcedureService] Không thể kết nối PostgreSQL: {e}")
            self.db_pool = None

    def _clean_doc_num(self, raw: str) -> str:
        if not raw:
            return ""
        pattern = r"\b\d+[\w\.]*/\d{4}/[A-Za-zĐđƯưƠơÀ-ỹ\-]+(?:\d+)?\b"
        m = re.search(pattern, raw, flags=re.UNICODE)
        return m.group(0).strip() if m else raw.strip()

    def get_related_procedures(
        self,
        doc_nums: List[str],
        user_query: str = "",
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Tìm kiếm các thủ tục hành chính liên kết với danh sách số hiệu văn bản (doc_nums).
        """
        if not self.db_pool or not doc_nums:
            return []

        clean_nums = list(set([self._clean_doc_num(d) for d in doc_nums if d and d.strip()]))
        if not clean_nums:
            return []

        conn = None
        try:
            conn = self.db_pool.getconn()
            with conn.cursor() as cur:
                sql = """
                SELECT 
                    p.id::text AS procedure_id,
                    p.procedure_code,
                    p.procedure_name,
                    p.field,
                    p.implementing_authority,
                    p.competent_authority,
                    p.receiving_address,
                    p.execution_sequence,
                    p.requirements,
                    p.source_url,
                    array_agg(DISTINCT lb.reference_number) AS matched_legal_bases,
                    COUNT(DISTINCT lb.id) AS basis_match_count
                FROM legal_bases lb
                JOIN procedures p ON lb.procedure_id = p.id
                WHERE (
                    lb.reference_number = ANY(%(doc_nums)s)
                    OR EXISTS (
                        SELECT 1 FROM unnest(%(doc_nums)s) dn 
                        WHERE dn != '' AND lb.reference_number ILIKE '%%' || dn || '%%'
                    )
                )
                GROUP BY p.id, p.procedure_code, p.procedure_name, p.field, 
                         p.implementing_authority, p.competent_authority, 
                         p.receiving_address, p.execution_sequence, p.requirements, p.source_url
                ORDER BY basis_match_count DESC
                LIMIT 20;
                """
                cur.execute(sql, {"doc_nums": clean_nums})
                rows = cur.fetchall()

                procedures = []
                query_words = set(re.findall(r"\w+", user_query.lower())) if user_query else set()

                for r in rows:
                    p_id, p_code, p_name, p_field, p_impl, p_comp, p_addr, p_seq, p_req, p_url, p_bases, match_cnt = r
                    
                    # Tính điểm liên quan với câu hỏi người dùng
                    score = float(match_cnt) * 1.5
                    if query_words and p_name:
                        name_words = set(re.findall(r"\w+", p_name.lower()))
                        overlap = len(query_words.intersection(name_words))
                        score += overlap * 2.0
                    if query_words and p_field:
                        field_words = set(re.findall(r"\w+", p_field.lower()))
                        overlap_field = len(query_words.intersection(field_words))
                        score += overlap_field * 3.0

                    procedures.append({
                        "procedure_id": p_id,
                        "procedure_code": p_code or "",
                        "procedure_name": p_name or "",
                        "field": p_field or "Khác",
                        "implementing_authority": p_impl or "",
                        "competent_authority": p_comp or "",
                        "receiving_address": p_addr or "",
                        "execution_sequence": p_seq or "",
                        "requirements": p_req or "",
                        "source_url": p_url or "",
                        "matched_legal_bases": p_bases or [],
                        "relevance_score": score
                    })

                # Sort theo điểm tương quan cao nhất
                procedures.sort(key=lambda x: x["relevance_score"], reverse=True)
                return procedures[:top_k]

        except Exception as e:
            print(f"[ProcedureService] Lỗi khi truy vấn procedures: {e}")
            return []
        finally:
            if conn and self.db_pool:
                self.db_pool.putconn(conn)
