"""FAQ retrieval over the faq_index vector index (Manny's search_faq tool).

The index is built by the maintops_rag job (rag/02, rag/03). Auth comes from the
environment: a Databricks profile locally, automatic credentials inside Model Serving.
"""

from databricks.sdk import WorkspaceClient
from databricks.sdk.core import Config

INDEX_NAME = "bootcamp_students.maintops.faq_index"
MAX_RESULTS = 10

_client: WorkspaceClient | None = None


def _get_client() -> WorkspaceClient:
    """Lazily create one workspace client with a short HTTP timeout."""
    global _client
    if _client is None:
        _client = WorkspaceClient(config=Config(http_timeout_seconds=15))
    return _client


def search_faq(question: str, k: int = 3) -> list[dict]:
    """Return the k FAQ chunks most relevant to the question (hybrid search).

    Each result is {"chunk_id", "text", "score"}, best first. Raises ValueError
    for an empty question; API errors propagate to the caller (the agent reports them).
    """
    question = (question or "").strip()
    if not question:
        raise ValueError("question must not be empty")
    k = max(1, min(int(k), MAX_RESULTS))

    response = _get_client().api_client.do(
        "POST",
        f"/api/2.0/vector-search/indexes/{INDEX_NAME}/query",
        body={
            "columns": ["chunk_id", "chunk_text"],
            "query_text": question,
            "num_results": k,
            "query_type": "hybrid",
        },
    )
    rows = (response.get("result") or {}).get("data_array") or []
    # The score is appended as the last column of every row
    return [{"chunk_id": r[0], "text": r[1], "score": r[-1]} for r in rows]
