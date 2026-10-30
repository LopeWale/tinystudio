"""Authored retrieval choices shared by admission and execution."""
MODES = frozenset({"lexical", "structured", "episode", "semantic", "hybrid"})


def validate_retrieval_selection(definition: dict) -> dict:
    params = definition.get("parameters", {})
    retrieval = definition.get("retrieval") or {}
    operation = params.get("operation", "build")
    if not isinstance(operation, str) or operation not in {"build", "query"}:
        raise ValueError("retrieval operation must be build or query")
    if "mode" in params and "mode" in retrieval and params["mode"] != retrieval["mode"]:
        raise ValueError("conflicting retrieval modes in parameters.mode and retrieval.mode")
    mode = params.get("mode", retrieval.get("mode"))
    if mode is not None and (not isinstance(mode, str) or mode not in MODES):
        raise ValueError("unsupported local retrieval mode")
    if mode is None and operation == "build":
        mode = "lexical"
    top_k = params.get("top_k", 5)
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError("top_k must be an integer between 1 and 100")
    if "queries" in params and "query" in params:
        raise ValueError("choose either query or queries")
    queries = params.get("queries", [params.get("query", "")])
    if not isinstance(queries, list) or not queries or not all(isinstance(query, str) for query in queries):
        raise ValueError("queries must be a nonempty list of strings")
    filters = params.get("filters", {})
    if not isinstance(filters, dict) or not all(isinstance(key, str) for key in filters):
        raise ValueError("filters must be an object with string keys")
    if any(key in params for key in {"embedding_model", "embedding_model_path"}):
        raise ValueError("embedding model sources must use immutable input artifact roles")
    inputs = definition.get("inputs", [])
    indexes = [item for item in inputs if item.get("role") == "retrieval_index"]
    embeddings = [item for item in inputs if item.get("role") == "embedding_model"]
    if len(embeddings) > 1 or any(item.get("visibility", "model") != "model" for item in embeddings):
        raise ValueError("embedding_model requires exactly one model-visible artifact binding")
    if operation == "query":
        if len(indexes) != 1 or indexes[0].get("visibility", "model") != "model":
            raise ValueError("query requires exactly one model-visible retrieval_index input")
        if "records" in params or any(item.get("role") not in {"retrieval_index", "embedding_model"} for item in inputs):
            raise ValueError("query cannot include corpus inputs or records")
    elif indexes:
        raise ValueError("build cannot include a retrieval_index input")
    if "records" in params and not isinstance(params["records"], list):
        raise ValueError("records must be a list")
    if mode in {"semantic", "hybrid"} and len(embeddings) != 1:
        raise ValueError("semantic/hybrid retrieval requires an immutable embedding_model input")
    if embeddings and mode in MODES - {"semantic", "hybrid"}:
        raise ValueError("embedding_model input requires semantic/hybrid retrieval")
    return {"operation": operation, "mode": mode, "queries": queries, "top_k": top_k, "filters": filters}
