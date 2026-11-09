"""pedidos_situacao.anexar (09/11/2026): documento do titular e do dependente, como a tela da Hapvida."""
import sys
import unittest
from unittest import mock

sys.path.append("/app")              # na imagem o teste roda de /app
import pedidos_situacao as ps  # noqa: E402

T, D = "11111111111", "22222222222"


def req(dep_anexado=False):
    return [{"controlNumber": 69000001, "document": T, "type": "Titular",
             "documents": [{"description": "DECLARACAO DE SAUDE", "required": True, "fileName": "ds.pdf", "path": "p/ds", "type": "pdf"}]},
            {"controlNumber": 69000002, "document": D, "type": "Dependente",
             "documents": [{"description": "DOCUMENTO COMPROBATORIO CONJUGE", "required": True,
                            "fileName": "cert.pdf" if dep_anexado else None, "path": "p/c" if dep_anexado else None, "type": "pdf"}]}]


class Anexar(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ps, "_sessao", return_value={"Accept": "json", "Authorization": "x"}).start()
        self.docs = mock.patch.object(ps, "_docs", side_effect=[req(), req(True)]).start()
        self.post = mock.patch.object(ps.httpx, "post", return_value=mock.Mock(is_success=True, status_code=200, text="")).start()

    def test_dependente_sobe_no_numero_dele_com_o_titular_no_formulario(self):
        r = ps.anexar("4DFEV", "69000001", D, "DOCUMENTO COMPROBATORIO CONJUGE", "Certidão.pdf", "application/pdf", b"%PDF")
        self.assertTrue(r["ok"])
        up, conf = self.post.call_args_list
        self.assertTrue(up.args[0].endswith("/69000002/document"))
        self.assertEqual(up.kwargs["data"], {"holderControlNumber": "69000001", "type": "DOCUMENTO COMPROBATORIO CONJUGE",
                                             "holderDocumentNumber": T})
        self.assertEqual(up.kwargs["files"]["file"][0], "Certidao.pdf")
        corpo = conf.kwargs["json"]
        self.assertEqual(corpo["holder"]["controlCode"], 69000001)
        self.assertEqual([a["fileName"] for a in corpo["holder"]["attachments"]], ["ds.pdf"], "os anexos de antes vão junto")
        self.assertEqual(corpo["dependents"], [{"controlCode": 69000002, "attachments": [
            {"documentType": "DOCUMENTO COMPROBATORIO CONJUGE", "fileName": "cert.pdf", "name": "", "path": "p/c", "type": "pdf"}]}])
        self.assertEqual(r["faltam"], [])

    def test_espaco_que_a_hapvida_nao_abriu(self):
        r = ps.anexar("4DFEV", "69000001", T, "DOCUMENTO COMPROBATORIO CONJUGE", "x.pdf", "application/pdf", b"%PDF")
        self.assertTrue(r["sem_espaco"])
        self.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
