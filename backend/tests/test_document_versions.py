"""Document versioning (Part A, docs/EMBEDDING_MODEL_SWAP.md).

Pure-logic tests for the upload decision plus structural guards that the reprocess path
never takes part in versioning. No database or storage is touched.
"""
import hashlib
import inspect

import pytest

from app.api.routes import ingestion
from app.db.repositories import document_registry as doc_repo


# ── resolve_upload_action: the decision table ───────────────────────────────

def _h(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_new_identity_is_new():
    assert ingestion.resolve_upload_action(None, _h(b"abc")) == "new"


def test_same_identity_same_bytes_is_already_current():
    existing = {"id": "doc-1", "file_hash": _h(b"abc"), "version_no": 1}
    assert ingestion.resolve_upload_action(existing, _h(b"abc")) == "already_current"


def test_same_identity_changed_bytes_is_new_version():
    existing = {"id": "doc-1", "file_hash": _h(b"abc"), "version_no": 3}
    assert ingestion.resolve_upload_action(existing, _h(b"abcd")) == "new_version"


def test_legacy_row_without_hash_becomes_new_version_not_skip():
    # Rows ingested before versioning carry no fingerprint: we cannot prove the bytes are
    # identical, so a re-upload must create a new version rather than be skipped.
    existing = {"id": "doc-1", "file_hash": None, "version_no": 1}
    assert ingestion.resolve_upload_action(existing, _h(b"abc")) == "new_version"


def test_content_hash_is_sha256_hex():
    assert ingestion._content_hash(b"hello") == hashlib.sha256(b"hello").hexdigest()


# ── identity check requires a domain ────────────────────────────────────────

def test_no_domain_means_no_identity_lookup(monkeypatch):
    called = {"db": False}

    def _boom():
        called["db"] = True
        raise AssertionError("get_db must not be called without a domain_id")

    monkeypatch.setattr(ingestion, "get_db", _boom)
    assert ingestion._find_active_version("a.pdf", None) is None
    assert called["db"] is False


# ── reprocess never versions ────────────────────────────────────────────────

def _code_without_docstring(fn) -> str:
    src = inspect.getsource(fn)
    doc = inspect.getdoc(fn) or ""
    # Strip the docstring text so prose that *mentions* the guarded names doesn't trip the check.
    for line in doc.splitlines():
        src = src.replace(line.strip(), "")
    return src


def test_reprocess_does_not_touch_identity_or_supersedes():
    code = _code_without_docstring(ingestion.reprocess_document)
    assert "_register_and_dispatch" not in code, "reprocess must rebuild the same id, not re-register"
    assert "resolve_upload_action" not in code
    assert "_find_active_version" not in code
    assert "supersedes" not in code, "reprocess must never write the supersedes link"


def test_register_inserts_version_columns():
    src = inspect.getsource(ingestion._register_and_dispatch)
    for col in ("file_hash", "version_no", "supersedes"):
        assert col in src


# ── completion flip semantics ───────────────────────────────────────────────

class _Cur:
    def __init__(self, supersedes):
        self._supersedes = supersedes
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return (self._supersedes,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_activate_version_flips_old_when_supersedes_set(monkeypatch):
    cur = _Cur(supersedes="old-id")
    monkeypatch.setattr(doc_repo, "get_db", lambda: _Conn(cur))
    assert doc_repo.activate_version("new-id") == "old-id"
    sqls = [s for s, _ in cur.executed]
    assert any("is_active = false" in s and "superseded_by" in s for s in sqls)
    assert any("is_active = true" in s for s in sqls)


def test_activate_version_noop_for_first_version_or_reprocess(monkeypatch):
    cur = _Cur(supersedes=None)
    monkeypatch.setattr(doc_repo, "get_db", lambda: _Conn(cur))
    assert doc_repo.activate_version("doc-1") is None
    assert not any("is_active = false" in s for s, _ in cur.executed)


# ── read side filters ───────────────────────────────────────────────────────

def test_retrieval_queries_filter_inactive_versions():
    from app.services import retriever_service, hybrid_search_service, table_query_engine
    for mod in (retriever_service, hybrid_search_service):
        src = inspect.getsource(mod)
        assert "dr.status = 'completed' AND dr.is_active" in src
        assert "dr.status = 'completed'\n" not in src.replace("dr.status = 'completed' AND dr.is_active", "")
    assert "dr.is_active" in inspect.getsource(table_query_engine)
