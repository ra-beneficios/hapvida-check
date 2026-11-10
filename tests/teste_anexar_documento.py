"""pedidos_situacao.anexar (09–10/11/2026): documento do titular e do dependente, como a tela da Hapvida — e cada falha
diz o que já aconteceu (nada subiu / pode ter subido / subiu e não confirmou)."""
import sys
import unittest
from unittest import mock

sys.path.append("/app")              # na imagem o teste roda de /app
import httpx  # noqa: E402
import pedidos_situacao as ps  # noqa: E402

T, D = "11111111111", "22222222222"
CONJ = "DOCUMENTO COMPROBATORIO CONJUGE"


def req(dep_anexado=False, dep_nome="cert.pdf"):
    return [{"controlNumber": 69000001, "document": T, "type": "Titular",
             "documents": [{"description": "DECLARACAO DE SAUDE", "required": True, "fileName": "ds.pdf", "path": "p/ds", "type": "pdf"}]},
            {"controlNumber": 69000002, "document": D, "type": "Dependente",
             "documents": [{"description": CONJ, "required": True,
                            "fileName": dep_nome if dep_anexado else None, "path": "p/c" if dep_anexado else None, "type": "pdf"}]}]


def resp(ok=True, status=200, content=None, text=""):
    m = mock.Mock(is_success=ok, status_code=status, text=text)
    m.json.return_value = {"content": content} if content is not None else {}
    return m


UP = resp(content={"fileName": "DOCUMENTO_COMPROBATORIO_-Certidao.pdf", "name": "Certidao.pdf", "path": "p/novo",
                   "contentType": "application/pdf"})


class Anexar(unittest.TestCase):
    def setUp(self):
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ps, "_sessao", return_value={"Accept": "json", "Authorization": "x"}).start()
        self.docs = mock.patch.object(ps, "_docs", side_effect=[req(), req(), req(True)]).start()
        self.post = mock.patch.object(ps.httpx, "post", side_effect=[UP, resp()]).start()

    def anexar(self, cpf=D, tipo=CONJ):
        return ps.anexar("4DFEV", "69000001", cpf, tipo, "Certidão.pdf", "application/pdf", b"%PDF")

    def test_dependente_sobe_no_numero_dele_e_confirma_com_a_resposta_do_upload(self):
        r = self.anexar()
        self.assertTrue(r["ok"])
        up, conf = self.post.call_args_list
        self.assertTrue(up.args[0].endswith("/69000002/document"))
        self.assertEqual(up.kwargs["data"], {"holderControlNumber": "69000001", "type": CONJ, "holderDocumentNumber": T})
        self.assertEqual(up.kwargs["files"]["file"][0], "DOCUMENTO_COMPROBATORIO_-Certidao.pdf", "sem acento, com o espaço")
        corpo = conf.kwargs["json"]
        self.assertEqual(corpo["holder"]["controlCode"], 69000001)
        self.assertEqual([a["fileName"] for a in corpo["holder"]["attachments"]], ["ds.pdf"], "os anexos de antes vão junto")
        self.assertEqual(corpo["dependents"], [{"controlCode": 69000002, "attachments": [
            {"documentType": CONJ, "fileName": "DOCUMENTO_COMPROBATORIO_-Certidao.pdf", "name": "Certidao.pdf",
             "path": "p/novo", "type": "application/pdf"}]}], "o arquivo novo vem da resposta do upload, como na tela")
        self.assertEqual(r["faltam"], [])

    def test_espaco_ja_preenchido_e_substituido(self):
        self.docs.side_effect = [req(True, "velho.pdf"), req(True, "velho.pdf"), req(True)]
        r = self.anexar()
        self.assertTrue(r["ok"])
        dep = self.post.call_args_list[1].kwargs["json"]["dependents"][0]["attachments"]
        self.assertEqual([a["fileName"] for a in dep], ["DOCUMENTO_COMPROBATORIO_-Certidao.pdf"])

    def test_espaco_que_a_hapvida_nao_abriu(self):
        r = self.anexar(T)
        self.assertTrue(r["sem_espaco"])
        self.post.assert_not_called()

    def test_upload_sem_resposta_e_incerto(self):
        self.post.side_effect = httpx.ReadTimeout("x")
        r = self.anexar()
        self.assertTrue(r["incerto"])
        self.assertFalse(r.get("anexado"))

    def test_upload_recusado_nada_subiu(self):
        self.post.side_effect = [resp(False, 400, text="arquivo invalido")]
        r = self.anexar()
        self.assertFalse(r["ok"])
        self.assertFalse(r.get("anexado") or r.get("incerto"))

    def test_confirmacao_recusada_subiu(self):
        self.post.side_effect = [UP, resp(False, 400, text="O documento obrigatório não foi incluído")]
        self.assertTrue(self.anexar()["anexado"])

    def test_falha_depois_do_upload_subiu(self):
        self.post.side_effect = [UP, httpx.ReadTimeout("x")]
        r = self.anexar()
        self.assertTrue(r["anexado"])
        self.assertFalse(r["ok"])

    def test_confirmou_mas_o_espaco_continua_vazio(self):
        self.docs.side_effect = [req(), req(), req()]
        r = self.anexar()
        self.assertFalse(r["ok"])
        self.assertTrue(r["anexado"])
        self.assertIn("continua vazio", r["erro"])


if __name__ == "__main__":
    unittest.main()
