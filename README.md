# OrkaFlow Worker

Programa que roda nas máquinas Windows e executa as tasks do orquestrador (bots em background e em foreground).

## Repositórios

O mesmo código está publicado em dois repositórios, e **os dois devem ficar iguais**:

| Repositório | Papel |
|---|---|
| `worker-url` | O que as máquinas **executam**. O backend entrega esta URL na configuração, e o runtime fica clonado em `C:\OrkaFlow\bots\worker-runtime`. |
| `orkaflow-worker` | Cópia espelho (mesmo código). |

Ao alterar o worker, envie para os dois (`git push worker-url main` e `git push orkaflow-worker main`).
Backup do histórico antigo do `orkaflow-worker` (abril/2026): branch `backup-main-abril-2026`.

## Configurar uma máquina nova

Requisitos: **Python 3.12 ou superior** (com tcl/tk, marcado por padrão no instalador do Python) e **Git** no PATH.

1. Baixe o worker em `C:\OrkaFlow\bots\worker-runtime`:
   ```
   git clone https://github.com/tharlescunha/worker-url.git C:\OrkaFlow\bots\worker-runtime
   ```
2. Dê dois cliques em `configurar_worker.bat` (dentro dessa pasta). **Não precisa ser administrador.**
   Ele cria o ambiente Python, instala as dependências e abre a tela de configuração.
3. Na tela, preencha a URL da API, login e senha do painel, e o nome da máquina, e clique em **Configurar**.
   O andamento e qualquer erro aparecem na própria tela (botão "Copiar log") e em `C:\OrkaFlow\logs\setup.log`.
4. Ao terminar, a tela registra a máquina (se o nome já existir no painel, o runner é **atualizado**, sem precisar apagar),
   cria `C:\OrkaFlow\iniciar_worker.bat` e o atalho **OrkaFlow Worker** na área de trabalho.
   O atalho vem marcado para **executar como administrador** (opção da tela, necessária para bots como SAP GUI), então o Windows pede confirmação ao abrir.

Para configurar de novo uma máquina que já existe: rode `configurar_worker.bat` outra vez. Para recomeçar do zero, apague `C:\OrkaFlow` antes.

## Rodar

Abra o atalho **OrkaFlow Worker** (ou `C:\OrkaFlow\iniciar_worker.bat`) e mantenha a janela aberta:
- a cada início faz `git fetch` + `reset --hard` + `clean` do runtime: sempre sobe na versão do repositório;
- se o worker cair ou travar, reinicia sozinho (Ctrl+C e responda S para parar);
- não é serviço do Windows, porque automações com foco de tela precisam da sessão do usuário.

Logs: `C:\OrkaFlow\logs\worker.log` (5 dias, rotativo), `launcher.log` e `setup.log`.

## Regras de execução

- Até N tasks **background** ao mesmo tempo (N = "max_concurrency" do runner, no máximo 5) e 1 task **foreground** por vez; a foreground roda junto com as de background.
- Robô sem nenhuma saída por 10 minutos é encerrado (`INACTIVITY_TIMEOUT`); a task pode definir outro valor em `inactivity_timeout_seconds`.
- O timeout total vem da task ou do bot (padrão 300s).

## Testes

```
venv\Scripts\python.exe -m unittest discover -s tests -v
```

Dependências do runtime: `requirements.txt` (só o que roda nas máquinas).
