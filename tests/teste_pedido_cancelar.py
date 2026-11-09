"""pedido_cancelar (09/11/2026): qual pedido, quando pode cancelar, e o titular que levaria dependentes junto."""
import sys
import unittest
from unittest import mock

sys.path.append("/app")              # na imagem o teste roda de /app
import pedido_cancelar as pc  # noqa: E402

T, D, F = "11111111111", "22222222222", "33333333333"


def mov(cpf, cn, dep=0, st="1", tipo="Inclusion", data="01/11/2026"):
    return {"cpfNumber": cpf, "controlNumber": cn, "dependentControlNumber": dep, "processingStatus": st,
            "processingStatusDescription": "x", "operationDescription": tipo, "typingDate": data, "name": cpf}


class Regras(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(pc._iso("10/05/1985"), "1985-05-10")
        self.assertEqual(pc._iso("1985-05-10T00:00:00Z"), "1985-05-10")
        self.assertIsNone(pc._iso(None))

    def test_escolhe_o_mais_recente_ou_o_dado(self):
        m = [mov(T, 100, data="01/10/2026"), mov(T, 200, data="05/11/2026"), mov(T, 300, tipo="Alteration")]
        self.assertEqual(pc.escolher(m, T)["controlNumber"], 200)
        self.assertEqual(pc.escolher(m, T, "100")["controlNumber"], 100)
        self.assertIsNone(pc.escolher(m, D))

    def test_titular_leva_os_dependentes_abertos(self):
        m = [mov(T, 100), mov(D, 100, dep=5), mov(F, 100, dep=6, st="2")]
        self.assertEqual([x["cpfNumber"] for x in pc.quem_vai_junto(m, m[0])], [D])
        self.assertEqual(pc.quem_vai_junto(m, m[1]), [])


class Cancelar(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(pc.ps, "_sessao", return_value={"Authorization": "x"}).start()
        self.movs = [mov(T, 100), mov(D, 100, dep=5)]
        mock.patch.object(pc.ps, "_pedidos", side_effect=lambda *a: self.movs).start()
        mock.patch.object(pc.ps, "_get", side_effect=lambda h, url: (
            {"holder": {"birthDate": "10/05/1985"}} if url.endswith("/100")
            else {"dependents": [{"document": D, "birthDate": "1990-01-02T00:00:00"}]})).start()
        self.post = mock.patch.object(pc.httpx, "post").start()

    def test_titular_com_dependente_que_entra_nao_cancela(self):
        r = pc.cancelar("4DFEV", T, executar=True)
        self.assertEqual(r["bloqueado"], [{"nome": D, "cpf": D}])
        self.post.assert_not_called()

    def test_dependente_manda_o_corpo_da_tela(self):
        r = pc.cancelar("4DFEV", D)
        self.assertEqual(r["corpo"], {"birthDate": "1990-01-02", "document": D, "dependentCode": "5"})
        self.post.assert_not_called()                  # sem executar só mostra

    def test_executa_e_confere_cancelado(self):
        self.post.return_value = mock.Mock(is_success=True, status_code=200, text="{}")
        mock.patch.object(pc.time, "sleep").start()
        def depois(*a):
            return [mov(T, 100, st="2"), mov(D, 100, dep=5, st="2")]
        pc.ps._pedidos.side_effect = [self.movs, depois()]
        r = pc.cancelar("4DFEV", T, pode_cancelar=[D], executar=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["situacao_codigo"], "2")
        self.assertEqual(self.post.call_args.kwargs["json"], {"birthDate": "1985-05-10", "document": T, "dependentCode": "0"})

    def test_aprovado_ou_cancelado_nao_mexe(self):
        self.movs = [mov(T, 100, st="9")]
        self.assertTrue(pc.cancelar("4DFEV", T, executar=True)["nao_cancelavel"])
        self.movs = [mov(T, 100, st="2")]
        self.assertTrue(pc.cancelar("4DFEV", T, executar=True)["ja_cancelado"])
        self.movs = []
        self.assertTrue(pc.cancelar("4DFEV", T, executar=True)["sem_pedido"])
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
