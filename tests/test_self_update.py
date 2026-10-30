import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.runtime import self_update


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.object(self_update, "HANDLED_FILE", Path(self.tmp.name) / "update_handled.txt")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        self_update._pending_id = None
        supervised = mock.patch.object(self_update, "under_supervisor", return_value=True)
        supervised.start()
        self.addCleanup(supervised.stop)

    def tearDown(self):
        self_update._pending_id = None

    def test_sem_pedido_nada_acontece(self):
        self_update.on_heartbeat_response({"ok": True})
        self.assertFalse(self_update.draining())
        self.assertFalse(self_update.restart_now_if_idle(0))

    def test_resposta_de_backend_antigo_sem_campos_e_ignorada(self):
        self_update.on_heartbeat_response({"ok": True, "runner_id": 1})
        self_update.on_heartbeat_response(None)
        self.assertFalse(self_update.draining())

    def test_com_task_rodando_so_para_de_pegar_novas(self):
        self_update.on_heartbeat_response({"update_requested": True, "update_request_id": "A1"})
        self.assertTrue(self_update.draining())
        self.assertFalse(self_update.restart_now_if_idle(2))
        self.assertTrue(self_update.draining())

    def test_ocioso_reinicia_uma_vez_e_grava_o_codigo(self):
        self_update.on_heartbeat_response({"update_requested": True, "update_request_id": "A1"})
        self.assertTrue(self_update.restart_now_if_idle(0))
        self.assertEqual(self_update.handled_id(), "A1")

        # Backend ainda repete o mesmo pedido (relógio errado, ack atrasado): não reinicia de novo.
        self_update.on_heartbeat_response({"update_requested": True, "update_request_id": "A1"})
        self.assertFalse(self_update.draining())
        self.assertFalse(self_update.restart_now_if_idle(0))

        # Pedido novo vale.
        self_update.on_heartbeat_response({"update_requested": True, "update_request_id": "A2"})
        self.assertTrue(self_update.restart_now_if_idle(0))

    def test_pedido_cancelado_no_painel_libera_o_worker(self):
        self_update.on_heartbeat_response({"update_requested": True, "update_request_id": "A1"})
        self.assertTrue(self_update.draining())
        self_update.on_heartbeat_response({"update_requested": False, "update_request_id": None})
        self.assertFalse(self_update.draining())

    def test_fora_do_supervisor_ignora_o_pedido(self):
        with mock.patch.object(self_update, "under_supervisor", return_value=False):
            self_update.on_heartbeat_response({"update_requested": True, "update_request_id": "A1"})
        self.assertFalse(self_update.draining())
        self.assertFalse(self_update.restart_now_if_idle(0))

    def test_falha_ao_gravar_cancela_o_reinicio_em_vez_de_travar(self):
        self_update.on_heartbeat_response({"update_requested": True, "update_request_id": "A1"})
        with mock.patch.object(Path, "write_text", side_effect=OSError("disco cheio")):
            self.assertFalse(self_update.restart_now_if_idle(0))

    def test_resposta_malformada_nao_levanta(self):
        self_update.on_heartbeat_response({"update_requested": "sim", "update_request_id": 123})
        self_update.on_heartbeat_response("lixo")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
