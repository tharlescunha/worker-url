# app\core\json_store.py


"""
Funções para salvar e carregar JSON.
"""

import json
from pathlib import Path
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def save_model(file_path: Path, model: BaseModel) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(model.model_dump(mode="json"), indent=4, ensure_ascii=False)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    # Atomic replace — garante que arquivo nunca fica corrompido a meio escrita
    import os
    os.replace(tmp_path, file_path)


def load_model(file_path: Path, model_type: Type[T]) -> T | None:
    if not file_path.exists():
        return None

    raw = json.loads(file_path.read_text(encoding="utf-8"))
    return model_type.model_validate(raw)

def load_json(path: Path) -> dict:
    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)