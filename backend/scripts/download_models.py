"""Download the models the service needs into the mounted volumes (run once, needs internet).

    docker compose run --rm models

- Embedding model (EMBEDDING_MODEL) and reranker (RERANKER_NAME) into the HF cache (HF_HOME, /models).
- Docling layout / table weights into backend/docling_models.
After this the API and worker run with HF_HUB_OFFLINE=1 and never call out.
"""
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from huggingface_hub import snapshot_download  # noqa: E402

from app.config import settings  # noqa: E402

DOCLING_DIR = Path(__file__).resolve().parents[1] / "docling_models"


def main() -> None:
    for repo in (settings.BGE_MODEL_NAME, settings.RERANKER_NAME):
        print(f"downloading {repo} …", flush=True)
        snapshot_download(repo_id=repo)
    print(f"downloading docling models into {DOCLING_DIR} …", flush=True)
    from docling.pipeline.standard_pdf_pipeline import StandardPdfPipeline
    StandardPdfPipeline.download_models_hf(local_dir=DOCLING_DIR, force=False)
    print("done", flush=True)


if __name__ == "__main__":
    main()
