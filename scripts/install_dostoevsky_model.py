"""Install a Dostoevsky-compatible FastText sentiment model.

The official Dostoevsky downloader points at storage.b-labs.pro. That host can be
unavailable, so the Docker build first tries the official archive and then trains a
small compatible FastText model from the public RuSentiment CSV mirror. The produced
file lives where `dostoevsky.models.FastTextSocialNetworkModel` expects it.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import dostoevsky.data

MODEL_NAME = "fasttext-social-network-model"
RUSENTIMENT_REPO = "https://github.com/strawberrypie/rusentiment.git"
LABELS = {"positive", "negative", "neutral", "skip", "speech"}


def _model_path() -> Path:
    return Path(dostoevsky.data.DATA_BASE_PATH) / "models" / f"{MODEL_NAME}.bin"


def _has_model(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 1024 * 1024


def _run(args: list[str], *, timeout: int) -> bool:
    try:
        subprocess.run(args, check=True, timeout=timeout)
        return True
    except Exception as exc:
        print(f"WARN: command failed: {' '.join(args)} :: {exc}", file=sys.stderr)
        return False


def _try_official_download(dest: Path) -> bool:
    if _run([sys.executable, "-m", "dostoevsky", "download", MODEL_NAME], timeout=180):
        return _has_model(dest)
    return False


def _sanitize(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _write_fasttext_train_file(dataset_dir: Path, train_file: Path) -> int:
    n = 0
    with train_file.open("w", encoding="utf-8", newline="\n") as out:
        for csv_file in sorted(dataset_dir.glob("rusentiment_*.csv")):
            with csv_file.open("r", encoding="utf-8", newline="") as src:
                for row in csv.DictReader(src):
                    label = (row.get("label") or "").strip().lower()
                    text = _sanitize(row.get("text") or "")
                    if label in LABELS and text:
                        out.write(f"__label__{label} {text}\n")
                        n += 1
    return n


def _train_fallback(dest: Path) -> bool:
    with tempfile.TemporaryDirectory(prefix="rusentiment-") as td:
        tmp = Path(td)
        repo = tmp / "rusentiment"
        if not _run(["git", "clone", "--depth", "1", RUSENTIMENT_REPO, str(repo)], timeout=180):
            return False

        train_file = tmp / "fasttext_train.txt"
        n = _write_fasttext_train_file(repo / "Dataset", train_file)
        if n < 1000:
            print(f"WARN: too few RuSentiment rows for fallback model: {n}", file=sys.stderr)
            return False

        import fasttext

        model = fasttext.train_supervised(
            input=str(train_file),
            epoch=10,
            lr=0.21909,
            dim=64,
            minCount=1,
            wordNgrams=3,
            minn=2,
            maxn=5,
            bucket=259929,
            loss="ova",
            verbose=1,
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(dest))
        print(f"Installed fallback Dostoevsky-compatible model from {n} RuSentiment rows")
    return _has_model(dest)


def main() -> int:
    dest = _model_path()
    if _has_model(dest):
        print(f"Dostoevsky model already installed: {dest}")
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)

    if _try_official_download(dest):
        print(f"Installed official Dostoevsky model: {dest}")
        return 0

    print("WARN: official Dostoevsky model download failed; training compatible fallback", file=sys.stderr)
    if _train_fallback(dest):
        print(f"Installed Dostoevsky-compatible model: {dest}")
        return 0

    print("WARN: Dostoevsky model is unavailable; provider will be disabled", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
