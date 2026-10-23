"""Test infrastructure: a dedicated PostgreSQL database (tinystudio_test on the configured server), Qdrant in the
client's local mode, Redis database 9 for queue tests, and a temporary data directory. Tests that need a store
skip with a reason when it is not reachable."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import tinystudio  # noqa: E402,F401  (loads .env so the development store URLs apply)
# Unit/integration suites must never inherit production cloud credentials or mode.
os.environ["TINYSTUDIO_STORAGE_BACKEND"] = "local"
os.environ["TINYSTUDIO_CF_ACCESS_REQUIRED"] = "0"
os.environ["TINYSTUDIO_SECURE_COOKIES"] = "0"
for _key in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_SECRET", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "TINYSTUDIO_PUBLIC_URL"):
    os.environ[_key] = ""
from tinystudio.config import DEFAULT_DATABASE_URL, DEFAULT_REDIS_URL, reload_config  # noqa: E402

TEST_DB = "tinystudio_test"
TOKENIZER = "HuggingFaceTB/SmolLM2-135M-Instruct"


def _tokenizer_cached() -> bool:
    try:
        from transformers import AutoTokenizer

        AutoTokenizer.from_pretrained(TOKENIZER)
        return True
    except Exception:  # noqa: BLE001
        return False


needs_tokenizer = pytest.mark.skipif(not _tokenizer_cached(), reason="SmolLM2 tokenizer not in the local Hub cache")


def _test_database_url() -> str:
    base = os.environ.get("TINYSTUDIO_DATABASE_URL", DEFAULT_DATABASE_URL)
    return base.rsplit("/", 1)[0] + "/" + TEST_DB


def _create_test_database() -> str | None:
    import psycopg

    base = os.environ.get("TINYSTUDIO_DATABASE_URL", DEFAULT_DATABASE_URL)
    dsn = base.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as conn:
            exists = conn.execute("select 1 from pg_database where datname = %s", (TEST_DB,)).fetchone()
            if not exists:
                conn.execute(f'create database "{TEST_DB}"')
        return None
    except Exception as cause:  # noqa: BLE001
        return f"{type(cause).__name__}: {cause}"


@pytest.fixture(scope="session")
def infra(tmp_path_factory: pytest.TempPathFactory) -> Any:
    from tinystudio import db as db_mod
    from tinystudio import vectors, worker
    from tinystudio.db.migrate import upgrade
    from tinystudio.db.models import Base

    problem = _create_test_database()
    if problem:
        pytest.skip(f"PostgreSQL is not reachable for tests ({problem}); start it with `docker compose up -d postgres redis qdrant`")
    data_dir = tmp_path_factory.mktemp("data")
    os.environ["TINYSTUDIO_DATA_DIR"] = str(data_dir)
    os.environ["TINYSTUDIO_QDRANT_URL"] = ":memory:"
    os.environ["TINYSTUDIO_DATABASE_URL"] = _test_database_url()
    redis_base = os.environ.get("TINYSTUDIO_REDIS_URL", DEFAULT_REDIS_URL)
    os.environ["TINYSTUDIO_REDIS_URL"] = redis_base.rsplit("/", 1)[0] + "/9"
    os.environ.pop("TINYSTUDIO_INFERENCE_URL", None)
    os.environ.pop("TINYSTUDIO_ADMIN_EMAIL", None)
    os.environ.pop("TINYSTUDIO_ADMIN_PASSWORD", None)
    reload_config()
    db_mod.reset()
    vectors.reset()
    worker.reset()
    Base.metadata.drop_all(db_mod.engine())
    with db_mod.engine().connect() as conn:
        from sqlalchemy import text

        conn.execute(text("drop table if exists alembic_version"))
        conn.commit()
    upgrade(database_url=_test_database_url())
    yield {"data_dir": data_dir}
    db_mod.reset()


@pytest.fixture
def db(infra: Any) -> Any:
    """A clean database per test (every table truncated first) and one session the test commits through."""
    from sqlalchemy import text

    from tinystudio import db as db_mod
    from tinystudio import vectors
    from tinystudio.api.deps import forget_secret
    from tinystudio.db.models import Base
    from tinystudio.inference.engine import set_engine
    from tinystudio.services import settings as settings_svc

    with db_mod.engine().connect() as conn:
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        conn.execute(text(f"truncate {names} cascade"))
        conn.commit()
    settings_svc._APPLIED.clear()
    settings_svc._ENV_SEED.clear()
    forget_secret()
    set_engine(None)
    vectors.reset()
    session = db_mod.session_factory()()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def project(db: Any) -> Any:
    from tinystudio.services.projects import create_project

    project = create_project(db, "Home Assistant", "answer smart-home commands as JSON")
    db.commit()
    return project


@pytest.fixture
def redis_available(infra: Any) -> bool:
    from tinystudio import worker

    if not worker.ping():
        pytest.skip("Redis is not reachable for queue tests")
    worker.redis_conn().flushdb()
    return True


def row(user: str, answer: str, **extra: Any) -> dict[str, Any]:
    from tinystudio.core.data import validate_datapoint

    return validate_datapoint({"messages": [{"role": "user", "content": user}, {"role": "assistant", "content": answer}], **extra})


def tiny_llama_dir(tmp_path: Path, seed: int | None = None) -> Path:
    """A random 2-layer SwiGLU Llama saved as a model directory, with the real SmolLM2 tokenizer."""
    import torch
    from transformers import AutoTokenizer, LlamaConfig, LlamaForCausalLM

    tok = AutoTokenizer.from_pretrained(TOKENIZER)
    cfg = LlamaConfig(vocab_size=len(tok), hidden_size=32, intermediate_size=48, num_hidden_layers=2, num_attention_heads=4, num_key_value_heads=2, max_position_embeddings=256, hidden_act="silu", tie_word_embeddings=True)
    torch.manual_seed(seed if seed is not None else len(str(tmp_path)))
    out = tmp_path / "tiny-model"
    out.parent.mkdir(parents=True, exist_ok=True)
    LlamaForCausalLM(cfg).save_pretrained(out)
    tok.save_pretrained(out)
    return out


def tiny_bert_dir(tmp_path: Path) -> Path:
    import torch
    from transformers import AutoTokenizer, BertConfig, BertModel

    tok = AutoTokenizer.from_pretrained(TOKENIZER)
    cfg = BertConfig(vocab_size=len(tok), hidden_size=32, intermediate_size=64, num_hidden_layers=2, num_attention_heads=4, max_position_embeddings=512)
    torch.manual_seed(1)
    out = tmp_path / "tiny-embedder"
    BertModel(cfg).save_pretrained(out)
    tok.save_pretrained(out)
    return out
