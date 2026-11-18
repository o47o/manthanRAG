"""Both ingestion code paths must run the completion hooks (docs/EMBEDDING_MODEL_SWAP.md).

The live pipeline is the split task chain in ingestion_tasks.py (parse_task →
chunk_embed_store_task); ingestion_orchestrator.ingest_document is the monolithic path.
The first Part A/B build wired the version flip and the embedding_model stamp only into the
orchestrator — the real chain never ran them (v1 stayed active after a v2 upload). This test
pins both hooks in BOTH files so that regression cannot recur silently.
"""
import inspect
import re

from app.services import ingestion_orchestrator, ingestion_tasks


def _code(module) -> str:
    return re.sub(r"#.*", "", inspect.getsource(module))


def test_version_flip_runs_after_completed_in_both_paths():
    for mod in (ingestion_tasks, ingestion_orchestrator):
        code = _code(mod)
        done = code.index('update_status(document_id, "completed", completed_at=True)')
        flip = code.find("doc_repo.activate_version(document_id)", done)
        assert flip != -1, f"{mod.__name__}: activate_version must follow the 'completed' status write"
        assert flip - done < 800, f"{mod.__name__}: flip too far from completion — probably a different branch"


def test_document_records_run_after_completed_in_both_paths():
    for mod in (ingestion_tasks, ingestion_orchestrator):
        code = _code(mod)
        done = code.index('update_status(document_id, "completed", completed_at=True)')
        hook = code.find("run_after_completion(document_id)", done)
        assert hook != -1, f"{mod.__name__}: record extraction must follow the 'completed' status write"
        assert hook - done < 1200, f"{mod.__name__}: record hook too far from completion"


def test_embedding_model_is_stamped_in_both_paths():
    for mod in (ingestion_tasks, ingestion_orchestrator):
        code = _code(mod)
        assert 'update_status(document_id, "embedded", embedded_at=True,' in code, mod.__name__
        assert "embedding_model=_embed_model_name()" in code, mod.__name__
