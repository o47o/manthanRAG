"""API-key auth + collection scope, against the real database schema.

Runs only when RUN_DB_TESTS=1 (it writes rows to this service's own schema
and removes them afterwards). Needs only light dependencies: the admin router
and the auth module, not the ML stack.
"""
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1",
                                reason="set RUN_DB_TESTS=1 to run database tests")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import auth  # noqa: E402
from app.api.routes import admin  # noqa: E402
from app.db.connection import get_db  # noqa: E402

TAG = f"pytest-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def setup():
    app = FastAPI()
    app.include_router(admin.router, prefix="/admin")

    @app.get("/whoami")
    def whoami(p: auth.Principal = Depends(auth.require_key)):
        return {"admin": p.is_admin, "collections": list(p.collection_ids)}

    @app.get("/scope")
    def scope(collection_id: int | None = None, p: auth.Principal = Depends(auth.require_key)):
        cols = auth.effective_collections(p, [collection_id] if collection_id else None)
        return {"collections": cols, "docs": auth.allowed_document_ids(cols)}

    root = admin.create_key(f"{TAG}-root", True, [])
    client = TestClient(app)
    yield client, root
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM document_registry WHERE original_filename LIKE %s", (f"{TAG}%",))
            cur.execute("DELETE FROM api_keys WHERE name LIKE %s", (f"{TAG}%",))
            cur.execute("DELETE FROM collections WHERE name LIKE %s", (f"{TAG}%",))
    auth.clear_cache()


def _h(key):
    return {"X-API-Key": key}


def test_missing_and_bad_key(setup):
    client, _ = setup
    assert client.get("/whoami").status_code == 401
    assert client.get("/whoami", headers=_h("mrag_wrong")).status_code == 401


def test_bearer_header_works(setup):
    client, root = setup
    r = client.get("/whoami", headers={"Authorization": f"Bearer {root['api_key']}"})
    assert r.status_code == 200 and r.json()["admin"] is True


def test_scoped_key_flow(setup):
    client, root = setup
    a = client.post("/admin/collections", json={"name": f"{TAG}-A"}, headers=_h(root["api_key"])).json()
    b = client.post("/admin/collections", json={"name": f"{TAG}-B"}, headers=_h(root["api_key"])).json()
    ka = client.post("/admin/keys", json={"name": f"{TAG}-ka", "collection_ids": [a["id"]]},
                     headers=_h(root["api_key"])).json()
    assert ka["api_key"].startswith(auth.KEY_PREFIX)

    # Two completed docs, one in each collection.
    doc_a, doc_b = str(uuid.uuid4()), str(uuid.uuid4())
    with get_db() as conn:
        with conn.cursor() as cur:
            for d, c in ((doc_a, a["id"]), (doc_b, b["id"])):
                cur.execute(
                    """INSERT INTO document_registry (id, original_filename, storage_path, file_size_bytes,
                                                      status, is_active, collection_id)
                       VALUES (%s, %s, 'x', 1, 'completed', true, %s)""",
                    (d, f"{TAG}-{c}.pdf", c),
                )

    # Scoped key: sees only collection A's document.
    r = client.get("/scope", headers=_h(ka["api_key"])).json()
    assert r["collections"] == [a["id"]] and r["docs"] == [doc_a]
    # Scoped key asking for collection B: 403.
    assert client.get(f"/scope?collection_id={b['id']}", headers=_h(ka["api_key"])).status_code == 403
    # Scoped key cannot use admin routes.
    assert client.get("/admin/keys", headers=_h(ka["api_key"])).status_code == 403
    # Admin sees both (unrestricted = None).
    r = client.get("/scope", headers=_h(root["api_key"])).json()
    assert r["collections"] is None

    # Document access check hides out-of-scope ids as 404.
    pa = auth.Principal(ka["id"], "ka", False, (a["id"],))
    auth.check_document_access(pa, doc_a)
    with pytest.raises(Exception) as exc:
        auth.check_document_access(pa, doc_b)
    assert getattr(exc.value, "status_code", None) == 404

    # Revoke: key stops working at once.
    assert client.post(f"/admin/keys/{ka['id']}/revoke", headers=_h(root["api_key"])).status_code == 200
    assert client.get("/whoami", headers=_h(ka["api_key"])).status_code == 401


def test_non_admin_key_needs_collection(setup):
    client, root = setup
    r = client.post("/admin/keys", json={"name": f"{TAG}-empty"}, headers=_h(root["api_key"]))
    assert r.status_code == 400


def test_cannot_revoke_own_key(setup):
    client, root = setup
    assert client.post(f"/admin/keys/{root['id']}/revoke", headers=_h(root["api_key"])).status_code == 400


def test_empty_scope_fails_closed():
    assert auth.allowed_document_ids([]) == []
