"""``python -m maestro init``: cria um task.json de exemplo para rodar o robô na sua máquina."""
from __future__ import annotations

import json
import sys
from pathlib import Path

TEMPLATE = {
    "task_id": 0,
    "automation_id": 0,
    "correlation_id": None,
    "execution_mode": "manual",
    "parameters_json": {
        "filtros": {"filial": "010"},
        "dados_acesso": {"sap_usuario": "USUARIO_DE_TESTE", "sap_senha": "troque-aqui"},
    },
    "runtime_parameters_json": {"lote": "A"},
    "env": {"SAP_URL": "https://sap.exemplo"},
}


def main(argv: list[str]) -> int:
    if len(argv) >= 1 and argv[0] == "init":
        target = Path(argv[1]) if len(argv) > 1 else Path.cwd() / "task.json"
        if target.exists():
            print(f"{target} já existe; não sobrescrevi.")
            return 1
        target.write_text(json.dumps(TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Criado {target}. Edite os parâmetros e rode o robô com Maestro(producao=False).")
        return 0
    print("Uso: python -m maestro init [caminho/task.json]")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
