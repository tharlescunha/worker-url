# OrkaFlow Worker

Programa que roda nas máquinas Windows e executa as tasks do orquestrador (bots em background e em foreground).

## Repositórios

O mesmo código está publicado em dois repositórios, e **os dois devem ficar iguais**:

| Repositório | Papel |
|---|---|
| `orkaflow-worker` | Origem do **instalador** (`OrkaFlowWorker-Setup.exe`): tela de configuração, registro da máquina no backend. |
| `worker-url` | O que as máquinas **executam**. O backend entrega esta URL ao instalador, que clona o repositório em `C:\OrkaFlow\bots\worker-runtime`. |

Ao alterar o worker, envie para os dois (`git push orkaflow-worker main` e `git push worker-url main`).
Backup do histórico antigo do `orkaflow-worker` (abril/2026): branch `backup-main-abril-2026`.

## Instalar uma máquina do zero

1. Apague `C:\OrkaFlow` (se existir).
2. Rode o instalador (`.exe`, ou `python -m app.main` com `pip install -r requirements-installer.txt`).
3. Informe a URL da API, login e senha de um usuário do painel, nome e rótulo da máquina.
4. O instalador registra a máquina (se o nome já existe no painel, o runner é **atualizado**, sem precisar apagar), clona o runtime, cria a venv, instala `requirements.txt` e cria `C:\OrkaFlow\iniciar_worker.bat` e o atalho na área de trabalho.
5. Abra o atalho **como administrador**, se os seus bots (ex.: SAP GUI) precisarem.

## Rodar

`C:\OrkaFlow\iniciar_worker.bat` (mantenha a janela aberta):
- a cada início faz `git fetch` + `reset --hard` + `clean` do runtime: sempre sobe na versão do repositório;
- se o worker cair ou travar, reinicia sozinho (Ctrl+C e responda S para parar);
- não é serviço do Windows, porque automações com foco de tela precisam da sessão do usuário.

Logs: `C:\OrkaFlow\logs\worker.log` (5 dias, rotativo) e `launcher.log`.

## Regras de execução

- Até N tasks **background** ao mesmo tempo (N = "max_concurrency" do runner, no máximo 5) e 1 task **foreground** por vez; a foreground roda junto com as de background.
- Robô sem nenhuma saída por 10 minutos é encerrado (`INACTIVITY_TIMEOUT`); a task pode definir outro valor em `inactivity_timeout_seconds`.
- O timeout total vem da task ou do bot (padrão 300s).

## Testes

```
venv\Scripts\python.exe -m unittest discover -s tests -v
```

Dependências: `requirements.txt` (runtime, o que roda nas máquinas) e `requirements-installer.txt` (instalador gráfico e geração do .exe).
