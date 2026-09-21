"""Safe application-data reset that deliberately preserves downloaded models."""
from __future__ import annotations

from pathlib import Path
import argparse
import shutil
import sqlite3


TABLES = ("alerts", "vehicles", "observations", "jobs", "ocr_samples", "ocr_training_runs")
DATA_DIRECTORIES = ("events", "images", "jobs", "ocr-learning", "training")


def reset_application_data(data_root: str | Path) -> dict:
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "gate.db"
    deleted = {}
    if db_path.exists():
        with sqlite3.connect(db_path) as db:
            existing = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES:
                if table in existing:
                    deleted[table] = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    db.execute(f"DELETE FROM {table}")
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    for name in DATA_DIRECTORIES:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("管理領域外のパスは削除できません。")
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    return {"deleted": deleted, "preserved": ["/models", "model cache"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Gate業務データを初期化し、モデルキャッシュを保持します。")
    parser.add_argument("data_root", nargs="?", default="/data")
    parser.add_argument("--confirm", required=True, choices=["DELETE DATA"])
    args = parser.parse_args()
    print(reset_application_data(args.data_root))


if __name__ == "__main__":
    main()
