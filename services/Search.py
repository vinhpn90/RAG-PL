import os
import re
import json
import numpy as np
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv
from services.Chat import ChatService

load_dotenv()

# Cấu hình Vector Backend
VECTOR_BACKEND = os.getenv("VECTOR_BACKEND", "pgvector").strip().lower()

# Cấu hình PostgreSQL
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5434"))

# Cấu hình dữ liệu tĩnh FAISS (fallback)
DATA_DIR = "data"
FAISS_INDEX_FILE = os.path.join(DATA_DIR, "faiss.index")
FAISS_ID_MAP_FILE = os.path.join(DATA_DIR, "faiss_id_map.json")
CHUNK_MAP_FILE = os.path.join(DATA_DIR, "chunk_map.json")
ARTICLE_MAP_FILE = os.path.join(DATA_DIR, "article_index_map.json")
CHUNKS_FILE = os.path.join(DATA_DIR, "chunks.json")


class SearchService:
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.chat_service = ChatService()
        self.backend = VECTOR_BACKEND

        self.pg_pool = None
        self.index = None
        self.faiss_id_map = {}
        self.chunk_map = {}
        self.article_index_map = {}
        self.chunks_text_map = {}

        if self.backend == "pgvector" and POSTGRES_DB and POSTGRES_USER:
            try:
                from psycopg2.pool import ThreadedConnectionPool
                print(f"[SearchService] Khởi tạo kết nối PostgreSQL pgvector ({POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB})...")
                self.pg_pool = ThreadedConnectionPool(
                    minconn=1,
                    maxconn=10,
                    dbname=POSTGRES_DB,
                    user=POSTGRES_USER,
                    password=POSTGRES_PASSWORD,
                    host=POSTGRES_HOST,
                    port=POSTGRES_PORT,
                    connect_timeout=5
                )
                print("[SearchService] Kết nối PostgreSQL pgvector thành công!")
            except Exception as e:
                print(f"[SearchService] Không thể kết nối PostgreSQL ({e}), chuyển sang FAISS fallback...")
                self.backend = "faiss"

        if self.backend == "faiss":
            self._init_faiss()

    def _init_faiss(self):
        """Khởi tạo dữ liệu từ file tĩnh FAISS."""
        import faiss
        if not os.path.exists(FAISS_INDEX_FILE):
            raise FileNotFoundError(f"Không tìm thấy file index: {FAISS_INDEX_FILE}")
        
        print(f"[SearchService] Đang nạp FAISS index từ {FAISS_INDEX_FILE}...")
        self.index = faiss.read_index(FAISS_INDEX_FILE)

        with open(FAISS_ID_MAP_FILE, "r") as f:
            self.faiss_id_map = {int(k): v for k, v in json.load(f).items()}

        with open(CHUNK_MAP_FILE, "r", encoding="utf-8") as f:
            self.chunk_map = json.load(f)

        with open(ARTICLE_MAP_FILE, "r", encoding="utf-8") as f:
            self.article_index_map = json.load(f)

        if os.path.exists(CHUNKS_FILE):
            with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
                all_chunks = json.load(f)
            for c in all_chunks:
                self.chunks_text_map[c["chunk_id"]] = c.get("embed_text", "")
        print("[SearchService] Đã nạp xong FAISS index.")

    def _get_chunk_content(self, chunk_id: str) -> str:
        """Lấy nội dung full của chunk trong FAISS."""
        if chunk_id in self.chunks_text_map:
            return self.chunks_text_map[chunk_id]

        meta = self.chunk_map.get(chunk_id, {})
        parts = []
        if meta.get("title"): parts.append(meta["title"])
        if meta.get("article"): parts.append(meta["article"])
        if meta.get("content"): parts.append(meta["content"])
        return " | ".join(parts)

    def semantic_search(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """Tìm kiếm ngữ nghĩa toàn cục."""
        if self.backend == "pgvector" and self.pg_pool:
            return self._semantic_search_pgvector(query=query, top_k=top_k)
        return self._semantic_search_faiss(query=query, top_k=top_k)

    def _semantic_search_pgvector(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """Tìm kiếm ngữ nghĩa trực tiếp qua PostgreSQL + pgvector (HNSW Index)."""
        vec = self.chat_service.get_embedding(query)
        if not vec:
            return []

        conn = self.pg_pool.getconn()
        try:
            cur = conn.cursor()
            limit_candidates = min(top_k * 2, 100)
            
            # Sử dụng toán tử <=> (Cosine Distance) trên HNSW Index
            cur.execute("""
                SELECT 
                    chunk_id, doc_id, doc_num, title, article, clause, embed_text, content,
                    1 - (embedding <=> %s::vector) AS similarity
                FROM legal_knowledge_chunks
                ORDER BY embedding <=> %s::vector
                LIMIT %s
            """, (vec, vec, limit_candidates))

            rows = cur.fetchall()
            cur.close()

            candidates = []
            docs_text_for_rerank = []

            for r in rows:
                chunk_id, doc_id, doc_num, title, article, clause, embed_text, content, sim = r
                full_content = content if content else (embed_text or "")
                
                meta = {
                    "doc_id": doc_id,
                    "doc_num": doc_num,
                    "title": title,
                    "article": article,
                    "clause": clause
                }

                candidates.append({
                    "chunk_id": chunk_id,
                    "faiss_score": float(sim) if sim is not None else 0.0,
                    "metadata": meta,
                    "content": full_content
                })
                docs_text_for_rerank.append(full_content)

            if not candidates:
                return []

            # Rerank kết quả
            rerank_scores = self.chat_service.get_rerank_scores(query, docs_text_for_rerank)
            for i, candidate in enumerate(candidates):
                candidate["rerank_score"] = rerank_scores[i]
                candidate["final_score"] = rerank_scores[i]

            candidates.sort(key=lambda x: x["final_score"], reverse=True)

            # Lọc bỏ các document có điểm rerank quá thấp/âm để tránh nhiễu ngữ cảnh
            positive_candidates = [c for c in candidates if c["final_score"] > 0]
            selected_candidates = positive_candidates if positive_candidates else candidates[:min(top_k, 3)]

            final_results = []
            for item in selected_candidates[:top_k]:
                final_results.append({
                    "chunk_id": item["chunk_id"],
                    "score": item["final_score"],
                    "metadata": item["metadata"],
                    "content": item["content"]
                })

            return final_results
        except Exception as e:
            print(f"[SearchService] Lỗi pgvector semantic_search: {e}")
            return []
        finally:
            self.pg_pool.putconn(conn)

    def _semantic_search_faiss(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """Tìm kiếm ngữ nghĩa với FAISS."""
        vec = self.chat_service.get_embedding(query)
        if not vec or self.index is None:
            return []

        vec_np = np.array([vec], dtype=np.float32)
        scores, ids = self.index.search(vec_np, min(top_k * 2, self.index.ntotal))

        candidates = []
        docs_text_for_rerank = []

        for score, faiss_idx in zip(scores[0], ids[0]):
            if faiss_idx < 0: continue
            chunk_id = self.faiss_id_map.get(int(faiss_idx))
            if chunk_id is None: continue

            meta = self.chunk_map.get(chunk_id, {})
            content = self._get_chunk_content(chunk_id)

            if float(score) < self.threshold:
                continue

            candidates.append({
                "chunk_id": chunk_id,
                "faiss_score": float(score),
                "metadata": meta,
                "content": content
            })
            docs_text_for_rerank.append(content)

        if not candidates:
            return []

        rerank_scores = self.chat_service.get_rerank_scores(query, docs_text_for_rerank)
        for i, candidate in enumerate(candidates):
            candidate["rerank_score"] = rerank_scores[i]
            candidate["final_score"] = rerank_scores[i]

        candidates.sort(key=lambda x: x["final_score"], reverse=True)

        positive_candidates = [c for c in candidates if c["final_score"] > 0]
        selected_candidates = positive_candidates if positive_candidates else candidates[:min(top_k, 3)]

        final_results = []
        for item in selected_candidates[:top_k]:
            final_results.append({
                "chunk_id": item["chunk_id"],
                "score": item["final_score"],
                "metadata": item["metadata"],
                "content": item["content"]
            })

        return final_results

    def doc_ref_search(
        self,
        query: str,
        doc_ref: str,
        article_filter: Optional[str] = None,
        clause_filter: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """Tìm kiếm trong văn bản cụ thể (dùng cho Tool tra cứu)."""
        if self.backend == "pgvector" and self.pg_pool:
            return self._doc_ref_search_pgvector(
                query=query,
                doc_ref=doc_ref,
                article_filter=article_filter,
                clause_filter=clause_filter,
                top_k=top_k
            )
        return self._doc_ref_search_faiss(
            query=query,
            doc_ref=doc_ref,
            article_filter=article_filter,
            clause_filter=clause_filter,
            top_k=top_k
        )

    def _doc_ref_search_pgvector(
        self,
        query: str,
        doc_ref: str,
        article_filter: Optional[str] = None,
        clause_filter: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """Tìm kiếm chính xác số hiệu / điều / khoản trong PostgreSQL."""
        extracted_doc_num = self._extract_doc_num(doc_ref)
        target_ref = extracted_doc_num or doc_ref.strip()

        conn = self.pg_pool.getconn()
        try:
            cur = conn.cursor()
            conditions = ["(doc_num ILIKE %s OR title ILIKE %s)"]
            params: List[Any] = [f"%{target_ref}%", f"%{target_ref}%"]

            if article_filter:
                conditions.append("article ILIKE %s")
                params.append(f"%{article_filter.strip()}%")

            if clause_filter:
                conditions.append("clause ILIKE %s")
                params.append(f"%{clause_filter.strip()}%")

            where_clause = " AND ".join(conditions)

            # Nếu có query, embed query và sort theo khoảng cách vector
            vec = self.chat_service.get_embedding(query) if query else None
            if vec:
                params.append(vec)
                params.append(top_k)
                sql = f"""
                    SELECT chunk_id, doc_id, doc_num, title, article, clause, embed_text, content
                    FROM legal_knowledge_chunks
                    WHERE {where_clause}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                """
            else:
                params.append(top_k)
                sql = f"""
                    SELECT chunk_id, doc_id, doc_num, title, article, clause, embed_text, content
                    FROM legal_knowledge_chunks
                    WHERE {where_clause}
                    LIMIT %s
                """

            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()

            results = []
            for r in rows:
                chunk_id, doc_id, doc_num, title, article, clause, embed_text, content = r
                full_content = content if content else (embed_text or "")
                results.append({
                    "chunk_id": chunk_id,
                    "score": 1.0,
                    "metadata": {
                        "doc_id": doc_id,
                        "doc_num": doc_num,
                        "title": title,
                        "article": article,
                        "clause": clause
                    },
                    "content": full_content,
                    "source": "doc_ref_search"
                })

            return results
        except Exception as e:
            print(f"[SearchService] Lỗi pgvector doc_ref_search: {e}")
            return []
        finally:
            self.pg_pool.putconn(conn)

    def _doc_ref_search_faiss(
        self,
        query: str,
        doc_ref: str,
        article_filter: Optional[str] = None,
        clause_filter: Optional[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """Tìm kiếm văn bản cụ thể trong file FAISS."""
        extracted_doc_num = self._extract_doc_num(doc_ref)
        ref_norm = self._normalize_doc_ref(extracted_doc_num or doc_ref)

        matched_ids: List[str] = []
        if extracted_doc_num:
            extracted_norm = self._normalize_doc_ref(extracted_doc_num)
            for chunk_id, meta in self.chunk_map.items():
                doc_num_norm = self._normalize_doc_ref(meta.get("doc_num", ""))
                if doc_num_norm == extracted_norm:
                    matched_ids.append(chunk_id)

        if not matched_ids:
            for chunk_id, meta in self.chunk_map.items():
                doc_num_norm = self._normalize_doc_ref(meta.get("doc_num", ""))
                title_norm = self._normalize_doc_ref(meta.get("title", ""))
                if (ref_norm in doc_num_norm or ref_norm in title_norm or 
                    doc_num_norm in ref_norm or title_norm in ref_norm):
                    matched_ids.append(chunk_id)

        if not matched_ids:
            return []

        if article_filter:
            dieu_norm = self._normalize_doc_ref(article_filter)
            doc_id = self.chunk_map[matched_ids[0]].get("doc_id")
            article_key = f"{doc_id}|{article_filter.strip()}"
            faiss_ids_for_article = self.article_index_map.get(article_key)
            if faiss_ids_for_article:
                ids_from_article = {
                    self.faiss_id_map[fid] for fid in faiss_ids_for_article 
                    if fid in self.faiss_id_map
                }
                matched_ids = [cid for cid in matched_ids if cid in ids_from_article]
            else:
                matched_ids = [
                    cid for cid in matched_ids 
                    if dieu_norm in self._normalize_doc_ref(self.chunk_map[cid].get("article", ""))
                ]

        if clause_filter:
            khoan_norm = self._normalize_doc_ref(clause_filter)
            matched_ids = [
                cid for cid in matched_ids 
                if khoan_norm in self._normalize_doc_ref(self.chunk_map[cid].get("clause", ""))
            ]

        if not matched_ids:
            return []

        if len(matched_ids) > 1 and self.index is not None:
            vec = self.chat_service.get_embedding(query)
            if vec:
                vec_np = np.array([vec], dtype=np.float32)
                chunk_to_faiss = {v: k for k, v in self.faiss_id_map.items()}
                matched_faiss_ids = [chunk_to_faiss[cid] for cid in matched_ids if cid in chunk_to_faiss]

                if matched_faiss_ids:
                    k_search = min(len(matched_faiss_ids) + 5, self.index.ntotal)
                    scores, ids = self.index.search(vec_np, k_search)
                    faiss_id_set = set(matched_faiss_ids)
                    scored_matches = []
                    for score, fid in zip(scores[0], ids[0]):
                        if fid in faiss_id_set and float(score) >= self.threshold:
                            cid = self.faiss_id_map[int(fid)]
                            scored_matches.append((float(score), cid))
                    scored_matches.sort(reverse=True)
                    matched_ids = [cid for _, cid in scored_matches[:top_k]]

        results = []
        for cid in matched_ids[:top_k]:
            meta = self.chunk_map.get(cid, {})
            content = self._get_chunk_content(cid)
            results.append({
                "chunk_id": cid,
                "score": 1.0,
                "metadata": meta,
                "content": content,
                "source": "doc_ref_search"
            })

        return results

    def _normalize_doc_ref(self, text: str) -> str:
        if not text: return ""
        return re.sub(r'\s+', ' ', text.strip().lower())

    def _extract_doc_num(self, text: str) -> Optional[str]:
        if not text: return None
        match = re.search(
            r'\d+[A-Za-z]*\s*/\s*\d{4}\s*/\s*[A-ZĐƯƠ]+(?:-[A-ZĐƯƠ]+)*',
            text, flags=re.UNICODE
        )
        if not match: return None
        raw = match.group(0)
        return re.sub(r'\s*/\s*', '/', raw).strip()