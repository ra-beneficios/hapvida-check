"""10/11/2026 (André: "também não tem crítica de fora do portal?"): críticas dos arquivos (pedidos_situacao.criticas_contrato)."""
import sys
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path[:0] = ["/app", "/lifetools_service"]
import pedidos_situacao as ps  # noqa: E402

HOJE = date.today().isoformat()
VELHO = (date.today() - timedelta(days=60)).isoformat()


def fake_get(h, url, **p):
    if url.endswith("/movements-list"):
        return {"movements": [] if p["offset"] else [
            {"controlCode": "1", "createdAt": HOJE + "T10:00:00", "status": "FileProcessedWithReview"},
            {"controlCode": "2", "createdAt": HOJE + "T09:00:00", "status": "FileProcessed"},
            {"controlCode": "3", "createdAt": VELHO + "T09:00:00", "status": "FileProcessedWithReview"}]}
    if "/control-code/1/" in url:
        return {"total": 2, "data": [
            {"status": "Reviewed", "reviews": ["Negócio: cep não é válido"],
             "life": {"name": "A", "document": "123", "beneficiaryType": "Titular"}},
            {"status": "Processed", "reviews": [], "life": {"name": "B", "document": "456"}}]}
    raise AssertionError(f"não devia ler {url}")


class CriticasContrato(unittest.TestCase):
    def test_so_vidas_criticadas_dos_arquivos_revisados_no_periodo(self):
        with mock.patch.object(ps, "_sessao", return_value={}), mock.patch.object(ps, "_get", side_effect=fake_get):
            r = ps.criticas_contrato("4DFEV", 30)
        self.assertEqual(r, [{"controle": "1", "enviado_em": HOJE, "cpf": "00000000123", "nome": "A", "papel": "Titular",
                              "mensagens": ["Negócio: cep não é válido"]}])


if __name__ == "__main__":
    unittest.main()
