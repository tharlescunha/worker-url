# OrkaFlow — Plano de Ação de Melhorias

**Data:** 20/10/2026
**Projetos:** `orkaflow-worker` (Windows), `orkaflow-backend` (FastAPI), `orkaflow-frontend` (React/Vite)
**Objetivo:** corrigir falhas de execução, eliminar pausa/morte automática de bots, dar visibilidade de erros e subir uma versão nova (as máquinas foram migradas).
**Estratégia:** ajustar tudo e testar na máquina de desenvolvimento. Só depois subir para produção.

> Origem: auditoria por leitura de código (20/10/2026). Os itens do backend e do frontend vieram de subagentes e precisam ser **validados no código** antes de cada correção. Marque cada item ao concluir.

## Propostas em discussão (ainda NÃO decididas)

Eram exemplos dados na conversa, não decisões fechadas. Cada item abaixo do plano só será executado depois de aprovado.

- `git pull` forçado no worker, descartando alterações locais.
- Reinício automático do worker pelo próprio `.bat` (sem serviço do Windows).
- Não pausar runner ou bot automaticamente por causa de erro (registrar e alertar).
- Exclusividade da sessão interativa para automações de foreground.

## Ordem de execução

| Fase | Tema | Projetos |
|---|---|---|
| 0 | Verificar o estado atual | Servidor, banco, máquinas |
| 1 | Destravar a execução | Worker, Backend |
| 2 | Acabar com pausa e morte automática | Backend, Worker |
| 3 | Visibilidade e alertas | Backend, Frontend, Worker |
| 4 | Foreground / SAP | Backend, Worker |
| 5 | Segurança e organização | Todos |
| 6 | Teste local e subida | Todos |

---

## Fase 0 — Verificar (sem alterar código)

- [ ] **0.1** Confirmar qual versão do backend roda no servidor. Há 3 arquivos modificados e não commitados (`worker_service.py`, `worker_tasks.py`, `schemas/worker.py`) que corrigem um deadlock de claim.
- [ ] **0.2** Rodar SQL de diagnóstico: tasks WAITING/READY/STOP_REQUESTED paradas há mais de 10 min, schedules em ERROR, locks ativos duplicados.
- [ ] **0.3** Coletar `worker.log` e `critical_api_failures.jsonl` de uma máquina que está falhando.
- [ ] **0.4** Rodar `git status` na pasta do worker de uma máquina com problema, para confirmar se o `git pull` está falhando.

---

## Fase 1 — Destravar a execução

### Worker

- [ ] **W1 · Atualização forçada.** Em `iniciar_worker.bat`, trocar `git pull` por `git fetch` + `git reset --hard origin/main` + `git clean -fd`. O worker sempre inicia na versão mais nova, independente de arquivos locais.
- [ ] **W2 · Reinício automático pelo `.bat`.** Colocar o `.bat` em loop: se o `python` terminar por qualquer motivo, espera alguns segundos, faz a atualização (W1) e reinicia. Remover o `pause` final. Registrar cada reinício num arquivo de log com data e código de saída.
- [ ] **W3 · Detectar travamento.** O worker escreve um arquivo de "pulso" a cada ciclo. Um watchdog no `.bat` (ou processo auxiliar) mata e reinicia o worker se o pulso parar por mais de N minutos.
- [ ] **W4 · Tirar `__pycache__` do git.** Remover os 48 arquivos versionados e criar `.gitignore` (`__pycache__/`, `*.pyc`, `venv/`).
- [ ] **W5 · Heartbeat em thread própria.** Hoje sync de bots e instalação (`git`, `pip`, até 300s por comando) bloqueiam o heartbeat. Mover o heartbeat para uma thread separada.
- [ ] **W6 · Token e erros de autenticação.**
  - Separar 401 (token) de 403 (permissão), em `http_client.py`.
  - A task em execução passa a usar o token renovado.
  - Retentar `finish_task` com o token novo se vier 401.
- [ ] **W7 · Sync e instalação.** Mover o sync/instalação de bots para fora do loop principal. Limitar a retentativa de bot com instalação em erro (backoff, em vez de a cada 5 min para sempre).

### Backend

- [ ] **B1 · Registro de worker (erro 500).** `worker_registration.py:17,20` chama `service.get_by_uuid` e `service.update_runner`, que não existem em `WorkerService`. Implementar na service ou chamar o `RunnerRepository`. *(Confirmado por grep.)*
- [ ] **B2 · Deadlock de claim.** `get_next_task` deve devolver o primeiro candidato **claimável**, ou o claim deve ignorar tasks WAITING pré-vinculadas. Commitar a correção que já está na working tree, depois de revisar.
- [ ] **B3 · Claim e lock atômicos.**
  - `UPDATE ... WHERE status='WAITING'` conferindo `rowcount`, ou `UPDLOCK`.
  - Migration com índice único filtrado em `locks(lock_key) WHERE active = 1`.
  - Tratar locks duplicados já existentes antes de criar o índice.
- [ ] **B4 · Tasks que nunca expiram.**
  - Timeout por `runner_claimed_at` quando `started_at` é nulo (task READY).
  - `request_stop` em task WAITING cancela direto.
  - `cancel_task` e `force_stop` liberam lock e runner.
  - Implementar `inactivity_timeout_seconds` (hoje só é salvo).
- [ ] **B5 · Rotas não montadas.** Em `router.py:428,430`, mover `include_router` de `permissions` e `worker_runtime_events` para antes de montar no `api_router`.
- [ ] **B6 · `/runners/overview`** declarar antes de `/{runner_id}` (hoje devolve 422).
- [ ] **B7 · Versão do bot.** Usar a versão ativa (`is_active`) na criação da task, igual ao `sync`.

---

## Fase 2 — Acabar com pausa e morte automática

### Backend

- [ ] **B8 · Schedule não vai mais para ERROR.** Em `scheduler_engine_service.py:71-76, 283-298`, erro transitório faz retry com backoff e gera alerta. O schedule continua ACTIVE.
- [ ] **B9 · Schedule que descarta execução registra o motivo** (`:212-231, 267-298`): `TaskLog` ou evento "skipped" visível no painel. Schedule ONCE só vira inativo se criou a task.
- [ ] **B10 · Watchdog: remover `fail_tasks_without_online_workers`** (`watchdog_service.py:275-323`) ou deixá-lo só como alerta.
- [ ] **B11 · Watchdog: não definir `stop_requested`** nos caminhos de offline, órfã e timeout por heartbeat. Reservar para ação do usuário e timeout real da task.
- [ ] **B12 · Estado `LOST`.** Task com heartbeat perdido vai para um estado novo (ex.: `LOST`), e o worker reconcilia ao reconectar. Não marcar como ERROR.
- [ ] **B13 · Heartbeat e atividade.** Aumentar o limite de heartbeat (hoje 60s/120s). `create_task_log` e `create_task_error` passam a atualizar `last_update_at`.
- [ ] **B14 · Lock não é liberado por heartbeat vencido.** Só em estado final confirmado pelo worker. Renovar o lock com heartbeat/telemetria. O lock deve cobrir o `timeout_seconds` a partir do início real.
- [ ] **B15 · `release_startup_locks`** marcar como `INTERRUPTED`/ERROR com `TaskError`, em vez de `CANCELED`.
- [ ] **B16 · Máquina de estados.** Validar transição em `update_task_status` e `finish_task`: estado final não volta para RUNNING. `final_message` obrigatória em ERROR.
- [ ] **B17 · Heartbeat × banco.** Reconciliar `running_tasks` do worker com as tasks abertas no banco (`worker_service.py:385-408`).

### Worker

- [ ] **W8 · Worker reconcilia ao iniciar e ao reconectar.** Informa ao backend quais tasks ele realmente está executando, em vez de depender de cancelamento em massa.

---

## Fase 3 — Visibilidade e alertas

### Backend

- [ ] **B18 · Logging.** Trocar `print` por `logging` (arquivo ou journald) nos loops e serviços. `PYTHONUNBUFFERED=1` no serviço. Reduzir o log de cada request de heartbeat/polling.
- [ ] **B19 · `TaskError`/`TaskLog` (origem SYSTEM/WATCHDOG)** em toda transição automática (watchdog, dispatcher, schedule).
- [ ] **B20 · Supervisão das threads** (`app/workers/main.py`): `try/except` por ciclo, reinício da thread que cair, heartbeat do sistema em tabela.
- [ ] **B21 · `/health`** devolver 503 em falha e incluir o heartbeat de watchdog, dispatcher e schedulers.
- [ ] **B22 · Notificação** por e-mail ou webhook (Teams): task com erro/timeout, runner sem heartbeat, task parada na fila, schedule com falha. Usar `Automation.notification_type`.
- [ ] **B23 · Task WAITING parada** gerar `waiting_reason` ("sem runner online", "runner em manutenção") e desfazer a pré-vinculação após N segundos sem claim.
- [ ] **B24 · Dispatcher** considerar `execution_mode`, `priority DESC, id ASC`, `Automation.active` e `Bot.active`. Sem limite de 500 com `id DESC`.

### Frontend

- [ ] **F1 · Banner global de falhas** com: tasks `error`/`timeout`/`forced_stop`/`lost` nas últimas 24h, runners offline/desativados/sem heartbeat, tasks paradas na fila. Atualização a cada 15–30s.
- [ ] **F2 · Badge de contagem de erros** no item "Erros" do menu. Linha de task com erro destacada na lista.
- [ ] **F3 · Task parada:** badge "parada há N min" e o motivo provável (runner offline, desativado ou em manutenção).
- [ ] **F4 · Runners.** Filtro padrão "todos". Badge "DESATIVADO" separado do status. Badge "EM MANUTENÇÃO". "Heartbeat há X min" calculado, com alerta de heartbeat velho.
- [ ] **F5 · Toaster único** e helper `getApiErrorMessage` com o `detail` do backend. `onError` global (`QueryCache`/`MutationCache`) e em todas as mutations de runner, task e schedule.
- [ ] **F6 · Indicador "sem conexão com a API desde HH:MM"** quando o refetch falha com dados antigos na tela.
- [ ] **F7 · Detalhe da task** com polling enquanto `running`/`waiting`.
- [ ] **F8 · Usar `request-stop`** (parada graciosa) além de cancelar e forçar parada.

### Worker

- [ ] **W9 · Reportar ao backend** falha de sync, de auto-update de bot, de resolução de credencial e de instalação (hoje só log local).
- [ ] **W10 · Falha ao resolver credencial** deve falhar a task com erro claro, e não rodar o bot sem credencial.
- [ ] **W11 · Rotação do `worker.log`** (`RotatingFileHandler`).
- [ ] **W12 · Log de saída do `.bat`** (reinícios, `git`, `pip`) em arquivo.

---

## Fase 4 — Foreground / SAP

- [ ] **B25 · Sessão interativa por host.** Adicionar `session_key` (host + usuário) ao runner. Task foreground adquire lock `EXTERNAL_SYSTEM` com chave `session:{host}`. Só é liberado por estado final confirmado pelo worker.
- [ ] **B26 · Dispatcher** só escolhe runner foreground com o modo certo e a sessão livre.
- [ ] **W13 · Verificação da sessão antes de task foreground.** Sessão ativa, desbloqueada e interativa. Se não estiver, falhar com mensagem clara no painel.
- [ ] **W14 · Documentar a máquina de foreground:** usuário logado, sem bloqueio de tela, RDP sem desconectar a sessão, e a regra de administrador × usuário normal (a solução antiga do SAP precisa ser confirmada e registrada).
- [ ] **W15 · Bot no diretório local.** Avaliar `git clean -ffdx` e `reset --hard` no diretório do bot: arquivos locais não versionados são apagados a cada reinstalação.

---

## Fase 5 — Segurança e organização

### Backend

- [ ] **B27 · Segredos.** `JWT_SECRET_KEY` com 32+ bytes, falha ao iniciar se for curto. `CREDENTIAL_ENCRYPTION_KEY` separada. Documentar no `.env.example`. Plano para migrar as credenciais já criptografadas.
- [ ] **B28 · `APP_DEBUG=false` por padrão** e `/docs` fechado em produção.
- [ ] **B29 · Autorização por perfil** (`require_permission`) em todos os routers. Auditar o `reveal` de credenciais.
- [ ] **B30 · Registro de worker com segredo de enrollment.** Comparar token com `hmac.compare_digest`. Rate limit.
- [ ] **B31 · Credenciais:** runner só resolve credencial da task que está executando. Mascarar parâmetros `is_secret` nas respostas do painel. Auditoria.
- [ ] **B32 · Refresh token expirado** devolver 401 (hoje 500). Trocar `raise Exception` por exceções HTTP.
- [ ] **B33 · Limite de tamanho** e validação de PNG nos uploads de screenshot.
- [ ] **B34 · Banco.** `DB_TIMEOUT` ≈ 30s, `pool_recycle`, `pool_size`/`max_overflow`. Índices `Task(status, runner_id)`, `Task(status, automation_id)`. Tratar cron inválido na lista de saúde.
- [ ] **B35 · Consultas.** `get_next_task` sem carregar todas as tasks WAITING com 7 `selectinload`. Remover N+1 em `list_overview` e `automation_health_service`.
- [ ] **B36 · CORS** por variável de ambiente.
- [ ] **B37 · Um único entrypoint** dos serviços de fundo (evita tasks duplicadas). Índice único `(schedule_id, requested_start_at)`.
- [ ] **B38 · Limpeza.** Remover do git `security.py` (raiz), `.exe`, `api.json`, `inciar`. Remover código morto (`SchedulerService`, `WorkerLogService`, `WorkerErrorService`, `MOCK_WORKER_PARAMETERS`). Corrigir o download público (nome do arquivo e autenticação). `max_concurrency` vindo da config do runner. `created_by` preenchido nas tasks.
- [ ] **B39 · Testes mínimos** para claim, watchdog, dispatcher e schedule.

### Frontend

- [ ] **F9 · Build.** Mover `baseUrl` e `paths` para `tsconfig.app.json`. Corrigir os erros restantes (`login-page.tsx:1`, `app-sidebar.tsx:5`, `tasks-page.tsx:766-767`). `npm run build` precisa passar.
- [ ] **F10 · Publicação.** Servir `dist` por nginx, em vez de `vite dev`. Atualizar o `orkaflow-frontend.md`.
- [ ] **F11 · `.env`.** Tirar do git, criar `.env.example`. URL da API por ambiente (ou `/api` com proxy).
- [ ] **F12 · Cliente HTTP.** `timeout` (~15s). Fila única de refresh de token. Em falha, `logout()` e redirecionar para `/login`. `PrivateRoute` assinando o store. Corrigir `error.config` indefinido.
- [ ] **F13 · `AuthProvider`.** Falha de rede em `/auth/me` não deve apagar a sessão.
- [ ] **F14 · Permissões na UI** (`RequirePermission` nas rotas e no menu). Só UX; o bloqueio real é no backend.
- [ ] **F15 · Qualidade.** Lint (64 problemas), remover `any`, tipos de runner duplicados, arquivos soltos (`iniciar`, `App.css`, assets do template), rota `/logs` com placeholder, `shadcn` em `devDependencies`, script `typecheck`.

---

## Fase 6 — Teste local e subida

- [ ] **T1** Subir backend e frontend na máquina de desenvolvimento, com banco de teste (ou cópia).
- [ ] **T2** Rodar um worker local contra esse backend.
- [ ] **T3** Roteiro de testes:
  - registrar worker novo e reinstalado;
  - task em background e task em foreground, uma de cada;
  - claim concorrente com 2 runners;
  - derrubar o worker no meio de uma task e conferir reinício e reconciliação;
  - derrubar a rede por mais de 2 minutos com task rodando e conferir que **não** vira ERROR nem recebe `stop_requested`;
  - schedule com erro transitório, que continua ACTIVE;
  - token expirando no meio de uma task longa;
  - `git pull` forçado com arquivos locais alterados;
  - alertas e banner aparecendo para cada tipo de falha.
- [ ] **T4** Testar uma automação SAP real em foreground.
- [ ] **T5** Migrations: backup do banco, rodar em cópia, conferir a cabeça única.
- [ ] **T6** Plano de subida e de reversão (backup, ordem: backend, frontend, worker; como voltar).

---

## Registro de execução

| Data | Item | Resultado |
|---|---|---|
| 20/10/2026 | Auditoria e plano criados | Nada alterado no código |
| 20/10/2026 | **Worker** (v0.3.0) | Implementado: `.bat` com git forçado e reinício automático, supervisor com pulso, client HTTP com 401 e 403 separados e renovação de token compartilhada, heartbeat e sync em threads próprias, backoff de instalação, auto_update sem validar versão, credencial com falha derruba a task, concorrência background (até 5) + 1 foreground, logs com rotação de 5 dias, `.gitignore`. Verificado: 15 testes unitários e simulação do `.bat` com repositório falso. **Não verificado:** execução completa contra um backend real. |
| 20/10/2026 | **Backend** | Implementado: registro como upsert, claim atômico, locks com índice único, watchdog sem ERROR/stop_requested automáticos, cancelar/forçar parada liberam lock, inactivity timeout, dispatcher por modo e prioridade, schedule nunca vai para ERROR, rotas montadas, `/runners/overview`, logging, supervisão de threads, `/health` com 503, pool de banco. Migration `b7e4c2a91f10`. Verificado: `import app.main`, 18 testes (sqlite), cabeça única do alembic. **Não verificado:** SQL Server real (rowcount, índice filtrado, claim paralelo), `alembic upgrade`, testes HTTP. |
| 20/10/2026 | **Frontend** | Implementado: build corrigido, banner de falhas, toaster e `onError` global, runners (filtro "todos", badges, heartbeat), tasks (parada há N min, request-stop), schedule com último erro, cliente axios com fila de refresh e timeout, `.env.example`. Verificado: `tsc`, `npm run build`. **Pendente:** lint (28 erros `set-state-in-effect` preexistentes), teste no navegador, `git rm --cached .env`. |
| 20/10/2026 | Segurança | Não alterada de propósito. Ver `SEGURANCA_MELHORIAS_FUTURAS.md`. |
| 20/10/2026 | **Descoberta da base** | Os três remotos tinham um commit forçado de 27/06 (backend `2862001` roda no servidor; banco em `a3f2e8b1c4d5`). As melhorias foram refeitas sobre essa base, na branch `melhorias-sobre-junho` de cada repositório (sem push). Branches `melhorias-2026-10-20` guardam a versão sobre a base antiga. |
| 20/10/2026 | **Decisão: runner nunca é bloqueado** | Parada forçada deixou de bloquear o runner; runners `BLOCKED` herdados voltam a ONLINE quando o worker responde. Só o administrador desativa (`enabled=False`) ou coloca em manutenção. 33 testes no backend. |
| 20/10/2026 | **Migration aplicada no banco** | `a3f2e8b1c4d5 -> b7e4c2a91f10`: removeu o índice único `uq_tasks_one_open_per_runner` e adicionou 4 colunas nulas (`extra_json`, `last_error`, `last_skipped_reason`, `last_skipped_at`). Nenhuma linha alterada. Antes: 0 tasks abertas, 7 waiting, 6 runners offline. |
| — | **Teste local ponta a ponta** | PENDENTE (Fase 6). |
