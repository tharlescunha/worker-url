"""
Testes do núcleo do worker. Rodar com:

    venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config_models import BotRegistryItem, RunnerConfigData, RunnerData
from app.core.exceptions import AuthenticationError, ForbiddenError
from app.core.http_client import HttpClient
from app.runtime.bot_usage import BotBusyError, BotUsage
from app.runtime.task_manager import TaskExecutionManager


class _Handler(BaseHTTPRequestHandler):
    # estado compartilhado configurado por cada teste
    responses: dict = {}
    calls: list = []

    def _reply(self):
        _Handler.calls.append((self.command, self.path, self.headers.get("Authorization")))
        status, body = _Handler.responses.get(self.path, (200, {}))
        if callable(status):
            status, body = status(self)
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._reply()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self._reply()

    def log_message(self, *args):
        pass


class HttpClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        _Handler.responses = {}
        _Handler.calls = []
        self.client = HttpClient(base_url=f"http://127.0.0.1:{self.port}")
        self.client.set_token("old")

    def test_403_is_forbidden_not_authentication(self):
        _Handler.responses["/x"] = (403, {"detail": "Runner desativado."})
        with self.assertRaises(ForbiddenError) as ctx:
            self.client.post("/x", {})
        self.assertIn("Runner desativado", str(ctx.exception))
        self.assertEqual(len(_Handler.calls), 1, "403 não deve ser repetido")

    def test_401_without_hook_raises_authentication_error(self):
        _Handler.responses["/x"] = (401, {"detail": "expirado"})
        with self.assertRaises(AuthenticationError):
            self.client.post("/x", {})

    def test_401_hook_refreshes_and_retries_once(self):
        def respond(handler):
            if handler.headers.get("Authorization") == "Bearer new":
                return 200, {"ok": True}
            return 401, {"detail": "expirado"}

        _Handler.responses["/x"] = (respond, None)

        def hook(failed_token):
            self.assertEqual(failed_token, "old")
            self.client.set_token("new")
            return True

        self.client.on_unauthorized = hook
        self.assertEqual(self.client.post("/x", {}), {"ok": True})
        self.assertEqual(len(_Handler.calls), 2)

    def test_401_hook_failure_raises(self):
        _Handler.responses["/x"] = (401, {"detail": "expirado"})
        self.client.on_unauthorized = lambda token: False
        with self.assertRaises(AuthenticationError):
            self.client.post("/x", {})


def _runner(max_concurrency: int) -> RunnerData:
    return RunnerData(
        id=1,
        uuid="u",
        name="n",
        label="l",
        host_name="h",
        ip="127.0.0.1",
        os_name="Windows",
        os_version="11",
        cpu_arch="x64",
        memory_total=1,
        runner_token="t",
        config=RunnerConfigData(max_concurrency=max_concurrency),
    )


class ManagerCapacityTests(unittest.TestCase):
    def _manager(self, max_concurrency: int):
        import app.runtime.task_manager as tm

        release = threading.Event()
        started: list[int] = []

        def fake_execute(client, runner, task_data, logger, events=None):
            started.append(int(task_data["task_id"]))
            release.wait(5)

        original = tm.execute_task
        tm.execute_task = fake_execute
        self.addCleanup(lambda: setattr(tm, "execute_task", original))
        self.addCleanup(release.set)

        manager = TaskExecutionManager(
            client=None,
            runner=_runner(max_concurrency),
            logger=logging.getLogger("test"),
        )
        return manager, started, release

    def test_background_limit_is_capped_and_foreground_is_one(self):
        manager, _, _ = self._manager(99)
        self.assertEqual(manager.background_limit(), 5)
        self.assertEqual(manager.foreground_limit(), 1)

    def test_many_background_plus_one_foreground(self):
        manager, started, _ = self._manager(3)

        for task_id in (1, 2, 3):
            self.assertTrue(manager.start_task({"task_id": task_id, "execution_mode": "background"}))

        # background lotado, mas foreground ainda cabe
        self.assertFalse(manager.has_capacity("background"))
        self.assertTrue(manager.has_capacity("foreground"))
        self.assertEqual(manager.available_modes(), ["foreground"])

        self.assertTrue(manager.start_task({"task_id": 10, "execution_mode": "foreground"}))

        # duas foreground ao mesmo tempo: nunca
        self.assertFalse(manager.has_capacity("foreground"))
        self.assertFalse(manager.start_task({"task_id": 11, "execution_mode": "foreground"}))
        self.assertEqual(manager.available_modes(), [])

        time.sleep(0.2)
        self.assertEqual(sorted(started), [1, 2, 3, 10])

    def test_capacity_returns_when_task_finishes(self):
        manager, _, release = self._manager(1)
        self.assertTrue(manager.start_task({"task_id": 1, "execution_mode": "background"}))
        self.assertFalse(manager.has_capacity("background"))
        release.set()
        deadline = time.time() + 3
        while time.time() < deadline and not manager.has_capacity("background"):
            time.sleep(0.05)
        self.assertTrue(manager.has_capacity("background"))


class BotUsageTests(unittest.TestCase):
    def test_exclusive_waits_for_running_and_blocks_new_runs(self):
        usage = BotUsage()
        events: list[str] = []

        with usage.running("a"):
            self.assertIsNone(usage.try_exclusive("a"), "bot em uso não pode ser atualizado")
            self.assertIsNotNone(usage.try_exclusive("b"))

        ctx = usage.try_exclusive("a")
        self.assertIsNotNone(ctx)

        def runner():
            with usage.running("a"):
                events.append("run")

        with ctx:
            thread = threading.Thread(target=runner)
            thread.start()
            time.sleep(0.2)
            self.assertEqual(events, [], "execução espera a atualização terminar")

        thread.join(2)
        self.assertEqual(events, ["run"])

    def test_two_runs_of_same_bot_are_allowed(self):
        usage = BotUsage()
        with usage.running("a"):
            with usage.running("a"):
                self.assertEqual(usage.running_count("a"), 2)


class NeedsInstallTests(unittest.TestCase):
    def test_auto_update_ignores_version_and_commit(self):
        import app.sync.bot_installer as bi

        with TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bots_dir = tmp_path / "bots"
            venvs_dir = tmp_path / "venvs"
            bi.BOTS_DIR = bots_dir
            bi.VENVS_DIR = venvs_dir

            bot = BotRegistryItem(bot_id="1", name="MeuBot")
            bot_dir, venv_dir = bi.resolve_bot_install_paths(bot)
            bot_dir.mkdir(parents=True)
            (venv_dir / "Scripts").mkdir(parents=True)
            (venv_dir / "Scripts" / "python.exe").write_text("")

            bot.local_path = str(bot_dir)
            bot.venv_path = str(venv_dir)
            bot.last_install_status = "ok"
            bot.installed_version = "1"
            bot.expected_version = "2"
            bot.installed_commit = "aaa"
            bot.expected_commit = "bbb"

            self.assertTrue(bi.needs_install(bot), "sem auto_update, versão diferente reinstala")

            bot.auto_update = True
            self.assertFalse(bi.needs_install(bot), "com auto_update, versão não é validada")

            bot.last_install_status = "error"
            self.assertTrue(bi.needs_install(bot), "instalação com erro continua pendente")

    def test_install_backoff_grows_and_resets(self):
        import app.sync.bot_installer as bi

        bot = BotRegistryItem(bot_id="1", name="x")
        now = datetime.now(timezone.utc)
        self.assertTrue(bi.install_retry_allowed(bot, now))

        bi.register_install_failure(bot, now)
        self.assertFalse(bi.install_retry_allowed(bot, now + timedelta(minutes=1)))
        self.assertTrue(bi.install_retry_allowed(bot, now + timedelta(minutes=6)))

        bi.register_install_failure(bot, now)
        self.assertEqual(bot.next_install_retry_at, now + timedelta(minutes=10))

        bi.register_install_success(bot)
        self.assertTrue(bi.install_retry_allowed(bot, now))


class LogCleanupTests(unittest.TestCase):
    def test_cleanup_removes_old_and_huge_files(self):
        import app.core.logging_config as lc

        with TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            tmpdir = Path(tmp) / "tmp"
            logs.mkdir()
            tmpdir.mkdir()

            old = logs / "worker.log.2020-01-01"
            fresh = logs / "worker.log"
            huge = logs / "other.log"
            old_task = tmpdir / "task_1.json"
            fresh_task = tmpdir / "task_2.json"

            for f in (old, fresh, old_task, fresh_task):
                f.write_text("x")
            huge.write_bytes(b"0" * (lc.LOG_MAX_FILE_BYTES + 1))

            ancient = time.time() - 30 * 86400
            os.utime(old, (ancient, ancient))
            os.utime(old_task, (ancient, ancient))

            orig = (lc.LOGS_DIR, lc.TMP_DIR)
            lc.LOGS_DIR, lc.TMP_DIR = logs, tmpdir
            try:
                lc.cleanup_old_files()
            finally:
                lc.LOGS_DIR, lc.TMP_DIR = orig

            self.assertFalse(old.exists())
            self.assertFalse(huge.exists())
            self.assertFalse(old_task.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(fresh_task.exists())


class InactivityTimeoutTests(unittest.TestCase):
    class _FakeApi:
        def check_task_status(self, task_id):
            return {}

        def send_log(self, **kwargs):
            return {}

    def _run(self, code: str, inactivity: int, timeout: int = 60):
        import subprocess as sp

        from app.runtime import task_executor as te

        process = sp.Popen(
            [sys.executable, "-u", "-c", code],
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
            encoding="utf-8",
        )
        try:
            return te._consume_process_output_live(
                process=process,
                timeout_seconds=timeout,
                inactivity_timeout_seconds=inactivity,
                logger=logging.getLogger("test"),
                task_id=1,
                api=self._FakeApi(),
            )
        finally:
            if process.poll() is None:
                process.kill()

    def test_silent_process_is_killed_after_inactivity_limit(self):
        from app.runtime.task_executor import TaskInactivityTimeout

        started = time.time()
        with self.assertRaises(TaskInactivityTimeout) as ctx:
            self._run("import time; time.sleep(60)", inactivity=2)

        self.assertEqual(ctx.exception.timeout, 2)
        self.assertLess(time.time() - started, 20)

    def test_process_that_keeps_printing_is_not_killed(self):
        code = "; ".join(
            [
                "import time",
                "[(print('vivo', i, flush=True), time.sleep(0.5)) for i in range(6)]",
            ]
        )
        stdout, _ = self._run(code, inactivity=2)
        self.assertIn("vivo 5", stdout)

    def test_default_and_per_task_inactivity_value(self):
        from app.core.constants import DEFAULT_INACTIVITY_TIMEOUT_SECONDS
        from app.runtime.task_executor import _resolve_inactivity_timeout

        self.assertEqual(DEFAULT_INACTIVITY_TIMEOUT_SECONDS, 600)
        self.assertEqual(_resolve_inactivity_timeout({}), 600)
        self.assertEqual(_resolve_inactivity_timeout({"inactivity_timeout_seconds": 0}), 600)
        self.assertEqual(_resolve_inactivity_timeout({"inactivity_timeout_seconds": None}), 600)
        self.assertEqual(_resolve_inactivity_timeout({"inactivity_timeout_seconds": 120}), 120)


class BotRegistryOnDemandTests(unittest.TestCase):
    """Máquina recém-configurada: o bot da task ainda não está no registry local."""

    class _FakeClient:
        def __init__(self, bots=None):
            self.bots = bots or []
            self.posts = []

        def post(self, path, payload=None, **kwargs):
            self.posts.append((path, payload))
            if path.endswith("/worker/sync"):
                return {"bots": self.bots, "polling_interval": 10, "max_concurrency": 2}
            return {}

        patch = post

    def _registry_in(self, tmp):
        import app.runtime.task_executor as te
        import app.sync.bot_sync as bs

        registry_file = Path(tmp) / "bots_registry.json"
        originals = (bs.BOTS_REGISTRY_FILE, te.BOTS_REGISTRY_FILE)
        bs.BOTS_REGISTRY_FILE = registry_file
        te.BOTS_REGISTRY_FILE = registry_file
        self.addCleanup(lambda: (setattr(bs, "BOTS_REGISTRY_FILE", originals[0]),
                                 setattr(te, "BOTS_REGISTRY_FILE", originals[1])))
        return registry_file

    def test_sync_without_install_only_registers_bots(self):
        import app.sync.bot_sync as bs

        with TemporaryDirectory() as tmp:
            self._registry_in(tmp)
            installs = []
            original_install = bs.install_or_update_bot
            bs.install_or_update_bot = lambda *a, **k: installs.append(1)
            self.addCleanup(lambda: setattr(bs, "install_or_update_bot", original_install))

            client = self._FakeClient(bots=[
                {"bot_id": 5, "name": "Bot Cinco", "repository_url": "https://x/y.git", "version": "1"},
            ])
            summary = bs.sync_bots(client, _runner(1), install=False)

            self.assertEqual(installs, [], "install=False não pode instalar nada")
            self.assertEqual(summary["linked"], 1)
            registry = bs.load_registry()
            self.assertEqual([b.bot_id for b in registry.bots], ["5"])
            self.assertFalse(registry.bots[0].local_path)

    def test_background_sync_does_not_install_bots_never_used_on_this_machine(self):
        import app.sync.bot_sync as bs
        from app.core.config_models import BotsRegistry
        from app.sync.bot_installer import InstallResult

        with TemporaryDirectory() as tmp:
            registry_file = self._registry_in(tmp)
            # bot 2 já existe nesta máquina (tem local_path), bot 1 nunca foi instalado aqui
            bs.save_registry(BotsRegistry(bots=[
                BotRegistryItem(bot_id="2", name="Bot Dois", local_path=str(Path(tmp) / "b2"),
                                venv_path=str(Path(tmp) / "v2")),
            ]))

            installed = []

            def fake_install(bot, progress_callback=None):
                installed.append(bot.bot_id)
                return InstallResult(local_path=str(Path(tmp) / "x"), venv_path=str(Path(tmp) / "y"),
                                     installed_commit="abc", requirements_hash=None, message="ok")

            original = bs.install_or_update_bot
            bs.install_or_update_bot = fake_install
            self.addCleanup(lambda: setattr(bs, "install_or_update_bot", original))

            client = self._FakeClient(bots=[
                {"bot_id": 1, "name": "Bot Um", "repository_url": "https://x/1.git", "version": "1"},
                {"bot_id": 2, "name": "Bot Dois", "repository_url": "https://x/2.git", "version": "2"},
            ])
            bs.sync_bots(client, _runner(1), install_missing=False)

            self.assertEqual(installed, ["2"], "só o bot que já existe na máquina é atualizado")

    def test_task_with_unknown_bot_syncs_and_retries_instead_of_failing(self):
        import app.runtime.task_executor as te
        import app.sync.bot_sync as bs

        with TemporaryDirectory() as tmp:
            self._registry_in(tmp)
            client = self._FakeClient(bots=[
                {"bot_id": 5, "name": "Bot Cinco", "repository_url": "https://x/y.git", "version": "1"},
            ])

            class Stop(RuntimeError):
                pass

            original_ready = te._ensure_bot_ready

            def fake_ready(bot, **kwargs):
                raise Stop("chegou na preparação do bot")

            te._ensure_bot_ready = fake_ready
            self.addCleanup(lambda: setattr(te, "_ensure_bot_ready", original_ready))

            te.execute_task(
                client=client,
                runner=_runner(1),
                task_data={"task_id": 99, "bot_id": 5, "execution_mode": "background"},
                logger=logging.getLogger("test"),
            )

            sync_calls = [p for p, _ in client.posts if p.endswith("/worker/sync")]
            self.assertEqual(len(sync_calls), 1, "deve sincronizar uma vez sob demanda")

            finish = [payload for path, payload in client.posts if path.endswith("/finish")]
            self.assertTrue(finish, "a task deve ser finalizada")
            message = finish[-1]["final_message"]
            self.assertIn("chegou na preparação do bot", message)
            self.assertNotIn("Bot não encontrado", message)


class TaskEnvironmentTests(unittest.TestCase):
    class _Api:
        def __init__(self, response=None, error=None):
            self.response, self.error = response, error

        def resolve_task_env(self, task_id):
            if self.error:
                raise self.error
            return self.response

        def check_task_status(self, task_id):
            return {}

        def send_log(self, **kwargs):
            return {}

    def test_secret_masker_hides_values_and_ignores_too_short_ones(self):
        from app.runtime.task_executor import SecretMasker

        masker = SecretMasker(["abc12345", "xy", ""])
        self.assertEqual(masker.mask("senha=abc12345 fim"), "senha=*** fim")
        self.assertEqual(masker.mask("xy continua"), "xy continua", "valor curto demais não é mascarado")

    def test_variables_are_loaded_reserved_names_are_dropped_and_secrets_masked(self):
        from app.runtime.task_executor import _load_task_environment

        api = self._Api({
            "variables": {"SAP_URL": "https://sap", "SAP_SENHA": "s3gredo!", "PATH": "x", "ORKAFLOW_TASK_ID": "9"},
            "secret_names": ["SAP_SENHA"],
        })

        variables, masker, secrets = _load_task_environment(api, 1, logging.getLogger("t"))

        self.assertEqual(variables, {"SAP_URL": "https://sap", "SAP_SENHA": "s3gredo!"})
        self.assertEqual(secrets, 1)
        self.assertEqual(masker.mask("login s3gredo! ok"), "login *** ok")

    def test_old_backend_without_endpoint_means_no_variables(self):
        from app.core.exceptions import ApiRequestError
        from app.runtime.task_executor import _load_task_environment

        variables, _masker, secrets = _load_task_environment(
            self._Api(error=ApiRequestError("HTTP 404: Not Found")), 1, logging.getLogger("t")
        )
        self.assertEqual((variables, secrets), ({}, 0))

    def test_other_failures_stop_the_task_instead_of_running_without_config(self):
        from app.core.exceptions import ApiRequestError
        from app.runtime.task_executor import _load_task_environment

        with self.assertRaises(RuntimeError) as ctx:
            _load_task_environment(self._Api(error=ApiRequestError("HTTP 500: erro")), 1, logging.getLogger("t"))
        self.assertIn("variáveis de ambiente", str(ctx.exception))

    def test_secret_printed_by_the_robot_is_masked_in_captured_output(self):
        import subprocess as sp

        from app.runtime import task_executor as te

        code = "import os; print('minha senha e', os.environ['SAP_SENHA'], flush=True)"
        env = dict(os.environ, SAP_SENHA="s3gredo!")
        process = sp.Popen(
            [sys.executable, "-u", "-c", code], stdout=sp.PIPE, stderr=sp.PIPE, text=True, encoding="utf-8", env=env
        )
        stdout, _ = te._consume_process_output_live(
            process=process,
            timeout_seconds=30,
            masker=te.SecretMasker(["s3gredo!"]),
            logger=logging.getLogger("t"),
            task_id=1,
            api=self._Api(),
        )

        self.assertIn("minha senha e ***", stdout)
        self.assertNotIn("s3gredo", stdout)


class CredentialAlertParsingTests(unittest.TestCase):
    """Linha ORKAFLOW_ALERTA_CREDENCIAL {...} emitida pelo robô."""

    def test_valid_json_line_is_parsed(self):
        from app.runtime.task_executor import _parse_credential_alert_line

        line = (
            'ORKAFLOW_ALERTA_CREDENCIAL {"sistema":"SAP","chave":"sap_senha",'
            '"motivo":"senha_expirada","mensagem":"Senha expirou no login."}'
        )
        parsed = _parse_credential_alert_line(line)

        self.assertEqual(
            parsed,
            {"sistema": "SAP", "chave": "sap_senha", "motivo": "senha_expirada", "mensagem": "Senha expirou no login."},
        )

    def test_unknown_motivo_becomes_invalida(self):
        from app.runtime.task_executor import _parse_credential_alert_line

        parsed = _parse_credential_alert_line('ORKAFLOW_ALERTA_CREDENCIAL {"chave":"x","motivo":"qualquer"}')
        self.assertEqual(parsed["motivo"], "invalida")

    def test_invalid_json_still_counts_as_alert_with_raw_line_as_message(self):
        from app.runtime.task_executor import _parse_credential_alert_line

        line = "ORKAFLOW_ALERTA_CREDENCIAL isto nao e json " + "x" * 400
        parsed = _parse_credential_alert_line(line)

        self.assertIsNotNone(parsed)
        self.assertEqual((parsed["chave"], parsed["sistema"], parsed["motivo"]), (None, None, "invalida"))
        self.assertTrue(parsed["mensagem"].startswith("ORKAFLOW_ALERTA_CREDENCIAL isto nao e json"))
        self.assertEqual(len(parsed["mensagem"]), 300)

    def test_line_without_marker_or_missing_is_not_an_alert(self):
        from app.runtime.task_executor import _parse_credential_alert_line

        self.assertIsNone(_parse_credential_alert_line(None))
        self.assertIsNone(_parse_credential_alert_line(""))
        self.assertIsNone(_parse_credential_alert_line("log normal do robo"))
        self.assertIsNone(_parse_credential_alert_line("texto antes ORKAFLOW_ALERTA_CREDENCIAL {}"))

    def test_alert_is_built_from_exit_code_3_without_marker_line(self):
        from app.runtime.task_executor import _build_credential_alert

        alert = _build_credential_alert(exit_code=3, marker_line=None, credential={"credential_id": 7, "keys": ["a"]})
        self.assertEqual(
            alert,
            {
                "credential_id": 7,
                "chave": None,
                "motivo": "invalida",
                "mensagem": "Bot encerrou com código de saída 3.",
                "sistema": None,
            },
        )

    def test_no_alert_without_signal_and_alert_with_marker_on_exit_0(self):
        from app.runtime.task_executor import _build_credential_alert

        self.assertIsNone(_build_credential_alert(exit_code=0, marker_line=None, credential=None))
        self.assertIsNone(_build_credential_alert(exit_code=1, marker_line=None, credential={"credential_id": 1}))

        alert = _build_credential_alert(
            exit_code=0,
            marker_line='ORKAFLOW_ALERTA_CREDENCIAL {"chave":"senha","motivo":"usuario_bloqueado","mensagem":"bloqueado"}',
            credential=None,
        )
        self.assertEqual(alert["credential_id"], None, "sem credencial resolvida o alerta vai mesmo assim")
        self.assertEqual((alert["chave"], alert["motivo"], alert["mensagem"]), ("senha", "usuario_bloqueado", "bloqueado"))

    def test_last_marker_line_from_stdout_is_kept(self):
        from app.runtime.task_executor import _note_credential_alert_line

        sink: list[str] = []
        _note_credential_alert_line(sink, "stdout", "log qualquer")
        _note_credential_alert_line(sink, "stderr", "ORKAFLOW_ALERTA_CREDENCIAL {\"chave\":\"no_stderr\"}")
        self.assertEqual(sink, [])

        _note_credential_alert_line(sink, "stdout", "ORKAFLOW_ALERTA_CREDENCIAL {\"chave\":\"primeira\"}")
        _note_credential_alert_line(sink, "stdout", "  ORKAFLOW_ALERTA_CREDENCIAL {\"chave\":\"ultima\"}")
        self.assertEqual(len(sink), 1)
        self.assertIn("ultima", sink[0])

    def test_send_failure_is_only_a_warning(self):
        from app.runtime.task_executor import _send_credential_alert_safely

        class _Api:
            def send_credential_alert(self, task_id, **kwargs):
                raise RuntimeError("backend fora")

        logger = logging.getLogger("t.alert")
        with self.assertLogs(logger, level="WARNING") as captured:
            _send_credential_alert_safely(_Api(), 1, {"credential_id": 1, "motivo": "invalida", "mensagem": "m"}, logger)
        self.assertTrue(any("Falha ao enviar alerta de credencial" in m for m in captured.output))


class CredentialMaskingTests(unittest.TestCase):
    class _Api:
        def __init__(self, dados_acesso):
            self.dados_acesso = dados_acesso
            self.calls = []

        def resolve_credential(self, credential_id, keys=None, task_id=None):
            self.calls.append({"credential_id": credential_id, "keys": keys, "task_id": task_id})
            return {"dados_acesso": self.dados_acesso}

    def _task(self):
        return {
            "task_id": 42,
            "parameters": [
                {
                    "parameter_name": "parameters_json",
                    "parameter_value": json.dumps(
                        {"dados_acesso": {"credential_id": 7, "itens": {"usuario": "", "senha": "", "pin": ""}}}
                    ),
                }
            ],
        }

    def test_resolved_values_go_into_the_masker_and_resolution_is_recorded(self):
        from app.runtime.task_executor import CredentialResolution, SecretMasker, _prepare_task_data_for_execution

        api = self._Api({"usuario": "joao.silva", "senha": "S3nh@Forte", "pin": "12"})
        resolution = CredentialResolution()

        prepared = _prepare_task_data_for_execution(self._task(), api, logging.getLogger("t"), resolution=resolution)

        self.assertEqual(api.calls, [{"credential_id": 7, "keys": ["usuario", "senha", "pin"], "task_id": 42}])
        self.assertEqual(resolution.as_dict(), {"credential_id": 7, "keys": ["usuario", "senha", "pin"]})
        self.assertEqual(sorted(resolution.secrets), ["S3nh@Forte", "joao.silva"], "valor curto (<4) fica de fora")

        dados = json.loads(prepared["parameters"][0]["parameter_value"])["dados_acesso"]
        self.assertEqual(dados["senha"], "S3nh@Forte", "o robô recebe o valor real")

        masker = SecretMasker(["segredo_env"]).with_extra(resolution.secrets)
        self.assertEqual(masker.mask("login joao.silva senha S3nh@Forte env segredo_env"), "login *** senha *** env ***")

    def test_task_without_credential_has_no_resolution(self):
        from app.runtime.task_executor import CredentialResolution, _prepare_task_data_for_execution

        api = self._Api({})
        resolution = CredentialResolution()
        _prepare_task_data_for_execution({"task_id": 1, "parameters": []}, api, logging.getLogger("t"), resolution=resolution)

        self.assertEqual(api.calls, [])
        self.assertIsNone(resolution.as_dict())
        self.assertEqual(resolution.secrets, [])


class TaskApiClientCredentialTests(unittest.TestCase):
    class _FakeClient:
        def __init__(self):
            self.posts = []

        def post(self, path, payload=None, **kwargs):
            self.posts.append((path, dict(payload or {})))
            return {"ok": True}

    def _api(self):
        from app.runtime.task_client import TaskApiClient

        client = self._FakeClient()
        return client, TaskApiClient(client=client, runner_uuid="uuid-1", runner_token="tok-1")

    def test_resolve_credential_sends_task_id(self):
        client, api = self._api()

        api.resolve_credential(credential_id=7, keys=["usuario", "senha"], task_id=42)

        self.assertEqual(
            client.posts,
            [("/api/v1/worker/credentials/7/resolve", {"uuid": "uuid-1", "token": "tok-1", "keys": ["usuario", "senha"], "task_id": 42})],
        )

    def test_resolve_credential_without_task_id_keeps_old_body(self):
        client, api = self._api()
        api.resolve_credential(credential_id=7, keys=["senha"])
        self.assertNotIn("task_id", client.posts[0][1])

    def test_send_credential_alert_contract(self):
        client, api = self._api()

        api.send_credential_alert(
            42, credential_id=7, chave="sap_senha", motivo="senha_expirada", mensagem="Senha expirou.", sistema="SAP"
        )

        self.assertEqual(
            client.posts,
            [
                (
                    "/api/v1/worker/tasks/42/credential-alert",
                    {
                        "uuid": "uuid-1",
                        "token": "tok-1",
                        "credential_id": 7,
                        "chave": "sap_senha",
                        "motivo": "senha_expirada",
                        "mensagem": "Senha expirou.",
                        "sistema": "SAP",
                    },
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
