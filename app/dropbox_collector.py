from pathlib import Path
import shutil
from typing import List

ALLOWED_SUFFIXES = {".pdf", ".txt", ".xml", ".csv", ".jpg", ".jpeg", ".png"}


def collect_from_dropbox(source_dir: Path, inbox_dir: Path) -> List[str]:
    source_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    moved: List[str] = []

    for file_path in sorted(source_dir.iterdir()):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() not in ALLOWED_SUFFIXES:
            continue

        target = inbox_dir / file_path.name
        if target.exists():
            stem = target.stem
            suffix = target.suffix
            counter = 1
            while True:
                candidate = inbox_dir / f"{stem}_{counter}{suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                counter += 1

        shutil.move(str(file_path), str(target))
        moved.append(str(target))

    return moved
