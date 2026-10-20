# OrkaFlow — Segurança: melhorias para o futuro

**Registrado em:** 20/10/2026
**Status:** NADA disto foi alterado. Foi deixado de fora de propósito da versão de melhorias de 20/10/2026 (decisão do dono: não é prioridade agora).
**Origem:** auditoria por leitura de código; os itens do backend e do frontend vieram de subagentes e devem ser validados no código antes de corrigir.

A ordem abaixo é a sugerida para quando o assunto voltar. Vários itens exigem mudar backend, frontend e workers juntos, ou migrar dados (credenciais), por isso valem uma janela própria de subida.

## 1. Segredos e criptografia (backend)

- **`JWT_SECRET_KEY` fraco.** O `.env` de desenvolvimento tem 8 caracteres. Usar 32+ bytes aleatórios e fazer a aplicação falhar ao iniciar se o segredo for curto. O `.env.example` nem lista a variável.
- **Chave das credenciais derivada do JWT** (`app/core/security.py`, ~91-107). Quem descobrir o segredo do JWT consegue ler as credenciais, e trocar o JWT torna todas ilegíveis. Criar `CREDENTIAL_ENCRYPTION_KEY` separada e planejar a migração:
  1. descriptografar tudo com a chave atual;
  2. recriptografar com a chave nova;
  3. só então trocar o segredo do JWT (isso desloga todos os usuários).
- **`APP_DEBUG=true` por padrão** (`app/main.py`, `.env`). Com debug ligado o Starlette devolve página de traceback e ignora o handler JSON de exceção. Padrão `False`, `APP_ENV=production` e `/docs` fechado em produção.
- **CORS fixo no código** (`app/main.py`): `localhost:5173`, `127.0.0.1:5173` e `10.50.156.202:5173`. Mover para variável de ambiente.

## 2. Autorização por perfil (backend e frontend)

- Só os routers `users` e `profiles` usam `require_admin`/`require_permission`. Qualquer usuário logado consegue:
  - revelar segredos (`GET /credentials/{id}/items/{item}/reveal`);
  - criar, editar e apagar credenciais, bots, schedules, runners, runner config e tasks;
  - desativar runners;
  - criar bots e versões com `source_url`/`entrypoint` arbitrários, o que na prática é execução remota de código nas máquinas Windows.
- Aplicar `require_permission(modulo, ação)` em todos os routers e auditar o `reveal` (quem revelou, quando).
- Frontend: nenhuma rota nem item de menu filtra por permissão. Criar `RequirePermission` e esconder itens do menu. Isso é só UX, o bloqueio real continua sendo do backend.

## 3. Registro e autenticação do worker (backend e worker)

- **Registro aberto** (`POST /worker/registration/`). Qualquer um que conheça o `uuid` de um runner rotaciona o token dele (`worker_service.py`, ~324-338), e o `uuid` aparece em `GET /runners` para qualquer usuário logado. Exigir segredo de enrollment (ou aprovação de um admin) para criar ou reativar runner.
- **Token do worker no corpo da requisição** (`uuid` e `token` em todo JSON). Mover para header `Authorization`.
- **Comparação do token com `==`** (`security.py`, ~89). Usar `hmac.compare_digest`.
- Sem rate limit nem bloqueio por tentativas de token inválido. Opcional: validar IP/host de origem.
- **Refresh token expirado devolve 500** (`auth_service.py`, ~85-97): `decode_token` levanta `ValueError` e há `raise Exception("Bot não encontrado")`. Trocar por `UnauthorizedException`/`NotFoundException`.

## 4. Credenciais e parâmetros secretos

- **Qualquer runner resolve qualquer `credential_id`** (`worker_service.py`, ~881-907). Restringir à credencial referenciada pela task que o runner está executando e registrar auditoria.
- **Parâmetros `is_secret` em texto puro** em `task_parameters`, devolvidos sem máscara em `GET /tasks/{id}` e `/tasks/{id}/parameters`. Mascarar na leitura do painel e, idealmente, criptografar em repouso.
- No worker, o arquivo temporário da task em `C:\OrkaFlow\tmp\task_<id>.json` contém as credenciais resolvidas em texto puro enquanto o bot roda. A versão de 20/10/2026 passou a apagar temporários com mais de 2 dias, mas o ideal é apagar logo após o bot ler, ou passar por variável de ambiente/pipe.

## 5. Uploads e downloads

- **Screenshots sem limite** (`upload_runner_screenshot`, `error_screenshot`): aceitam base64 sem limite de tamanho e sem validar que é PNG, e gravam binário no banco. Limitar tamanho e validar o tipo.
- **Download público do instalador** (`public_downloads.py`): sem autenticação, sem checksum nem assinatura, e o nome da lista permitida (`orkaflow-worker.exe`) não bate com o arquivo real (`OrkaFlowWorker-Setup.exe`), então hoje responde 404. Corrigir o nome, publicar o SHA-256 e considerar exigir login.
- **Binários no git** (`OrkaFlowWorker-Setup.exe` ~42 MB e `nssm.exe` em `app/static/downloads`). Tirar do histórico e usar Releases ou um repositório de artefatos.

## 6. Frontend

- **Tokens em `localStorage`** (`auth-store.ts`): access token, refresh token (7 dias) e usuário ficam expostos a qualquer XSS. Preferir cookie `httpOnly` se o backend passar a suportar.
- **`.env` versionado.** Rodar `git rm --cached .env` no frontend (o `.gitignore` já cobre daqui para frente).
- Remover `console.error` com a resposta da API no login em produção.

## 7. Repositórios e dados sensíveis

- `security.py` na raiz do backend imprime o hash bcrypt de "admin". Remover do git.
- `api.json`, `inciar` e arquivos `.md` com IPs internos e caminhos pessoais versionados. Revisar o que precisa mesmo ficar no repositório.
- `bcrypt==3.2.2` fixo com `passlib` (combinação antiga). Rever na próxima atualização de dependências.
- Rodar `npm audit` (frontend) e `pip-audit` (backend e worker) no servidor.

## 8. Worker (Windows)

- **`requirements.txt` do worker** traz dependências do servidor (`fastapi`, `uvicorn`, `sqlalchemy`, `pyodbc`, `alembic`, `pyinstaller`, `PySide6`...). Separar o que o runtime realmente usa (`requests`, `psutil`, `pydantic`, `mss`, `Pillow`) do que é só do instalador gráfico. Menos pacotes = menos superfície e instalação mais rápida.
- **Token DPAPI em `LOCAL_MACHINE`**: qualquer usuário local da máquina consegue ler o token. Avaliar escopo de usuário (`CurrentUser`) já que o worker roda na sessão do usuário.
- `git clone` de bots usa `repository_url` vinda do backend. Se alguém cadastrar uma URL maliciosa, o worker a executa (ligado ao item 2, permissões). Considerar lista de hosts permitidos.
- Decidir de forma documentada se o worker roda como administrador ou não (o SAP GUI e a automação com foco dependem disso). Hoje o worker registra no log e no evento `worker_started` se está elevado (`admin=True/False`).
