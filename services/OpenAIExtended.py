import requests
from typing import List, Optional, Union, Any, Dict
from openai import OpenAI
from pydantic import BaseModel, Field


class Usage(BaseModel):
    prompt_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class Document(BaseModel):
    text: str
    multi_modal: Optional[dict] = None


class RerankerResult(BaseModel):
    index: int
    document: Optional[Union[str, Document, Dict[str, Any]]] = None
    relevance_score: float


class RerankerResponse(BaseModel):
    id: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[Usage] = None
    results: List[RerankerResult] = Field(default_factory=list)


class Reranker:
    def __init__(self, client: OpenAI):
        self._client = client

    def create(
        self,
        model: str,
        query: str,
        documents: List[str],
    ) -> RerankerResponse:
        """
        Gọi API rerank với tham số query và documents riêng biệt.
        """
        url = f"{str(self._client.base_url).rstrip('/')}/rerank"
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self._client.api_key}"
            },
            json={
                "model": model,
                "query": query,
                "documents": documents,
            },
        )

        response.raise_for_status()
        return RerankerResponse.model_validate(response.json())


class OpenAIExtended(OpenAI):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reranker = Reranker(self)