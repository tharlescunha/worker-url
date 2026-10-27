# maestro

Biblioteca que o robô importa para conversar com o orquestrador. Sem dependências.

```python
from maestro import Maestro, CredencialInvalida

maestro = Maestro()            # producao=True por padrão; Maestro(producao=False) na sua máquina

def main():
    filial = maestro.param("filial", default="010")
    senha = maestro.credential("sap_senha")
    maestro.step(1, 2, "Login no SAP")
    maestro.info("Lendo pedidos", filial=filial)
    maestro.success("Pronto", result={"pedidos": 42})

if __name__ == "__main__":
    with maestro:
        main()
```

Na sua máquina (uma vez): `pip install git+https://github.com/tharlescunha/maestro.git`. Depois, `python -m maestro init` cria um `task.json` de exemplo ao lado do
`main.py`; com `Maestro(producao=False)` os logs vão para o console e o resultado para
`resultado.json`. Nas máquinas dos robôs o worker instala este pacote sozinho em cada venv.

## Onde fica o código

A fonte é a pasta `maestro/` do repositório do worker (`orkaflow-worker`), que vai junto para as máquinas. O repositório
`github.com/tharlescunha/maestro` é uma cópia publicada dela para o `pip install` do desenvolvedor, atualizada com:

```bash
git subtree push --prefix=maestro https://github.com/tharlescunha/maestro.git main
```
