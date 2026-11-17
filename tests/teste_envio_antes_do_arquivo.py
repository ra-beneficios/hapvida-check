"""17/11/2026 (MOV-36/MOV-37, 14:58): o portal não mostrou "Nova movimentação" — nada foi anexado, mas o registro ficava
ENVIANDO/INCERTO e barrava a nova tentativa como "mesmo arquivo" por 2 h. Falha antes de anexar = FALHOU_UPLOAD."""
import sys
import unittest
from unittest import mock

sys.path.append("/app")              # na imagem o teste roda de /app
import movimentacao_saude as ms  # noqa: E402
import portal_envio  # noqa: E402


def falha(antes_do_arquivo):
    e = TimeoutError("Locator.click: Timeout 15000ms exceeded.")
    if antes_do_arquivo:
        e.antes_do_arquivo = True
    return e


class AntesDoArquivo(unittest.TestCase):
    def _rodar(self, erro):
        with mock.patch.object(portal_envio, "antes", return_value=1401), \
                mock.patch.object(portal_envio, "depois") as depois, \
                mock.patch.object(ms, "get_credentials", return_value={"usuario": "u", "senha": "s"}), \
                mock.patch.object(ms, "enviar_movimentacao", side_effect=erro):
            with self.assertRaises(TimeoutError):
                ms.run_inclusao("p", "4DFEV", "/tmp/x.txt")
        return depois

    def test_falha_antes_de_anexar_vira_falhou_upload(self):
        d = self._rodar(falha(True))
        d.assert_called_once()
        self.assertEqual(d.call_args[0][0], 1401)
        self.assertIsNone(d.call_args[0][1].get("status"), "sem status 200 = FALHOU_UPLOAD")

    def test_falha_depois_de_anexar_fica_incerta(self):
        self._rodar(falha(False)).assert_not_called()


if __name__ == "__main__":
    unittest.main()
