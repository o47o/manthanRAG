"""Create an API key from the command line (used to bootstrap the first admin key).

Usage (from backend/):
    python scripts/create_api_key.py --name "Install admin" --admin
    python scripts/create_api_key.py --name "Customer app" --collections 1 2
    python scripts/create_api_key.py --create-collection "Finance reports"

The full key is printed once. Only its SHA-256 hash is stored.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.routes.admin import create_key  # noqa: E402
from app.db.connection import get_db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="Label for the key")
    ap.add_argument("--admin", action="store_true", help="Admin key (manages keys/collections, sees all)")
    ap.add_argument("--collections", type=int, nargs="*", default=[], help="Collection ids for a scoped key")
    ap.add_argument("--create-collection", metavar="NAME", help="Create a collection and print its id")
    args = ap.parse_args()

    if args.create_collection:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO collections (name) VALUES (%s) "
                    "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                    (args.create_collection.strip(),),
                )
                print(f"collection id: {cur.fetchone()[0]}")
        if not args.name:
            return

    if not args.name:
        ap.error("--name is required")
    try:
        out = create_key(args.name, args.admin, args.collections)
    except (ValueError, LookupError) as exc:
        raise SystemExit(str(exc))
    print(f"key id:   {out['id']}")
    print(f"admin:    {out['is_admin']}")
    print(f"scope:    {out['collection_ids'] or 'all collections'}")
    print(f"API key:  {out['api_key']}")
    print("Store this key now. It cannot be shown again.")


if __name__ == "__main__":
    main()
