"""17/11/2026 (André, MOV-36): 12 titulares do Premium recusados com "040-Cep endereço não é válido" — a cidade da
planilha (129) era de outra cidade e o robô escrevia por cima da que o Legado preenche pelo CEP. Vale a do CEP."""
import sys
import unittest

sys.path.append("/app")              # na imagem o teste roda de /app
import odonto_premium as op  # noqa: E402


class CidadeDoCep(unittest.TestCase):
    def test_ramon_itaborai_nao_vitoria(self):
        r = {"cep": [{"total": "1", "nm_logradouro": "SALVADOR DE MENDONCA", "cidade": "ITABORAI", "bairro": "CENTRO",
                      "uf": "RJ", "cd_tipo_logradouro": "R"}]}
        self.assertEqual(op.cidade_do_cep(r), {"cidade": "ITABORAI", "uf": "RJ"})

    def test_cep_que_o_legado_nao_conhece_fica_com_a_nossa(self):
        self.assertIsNone(op.cidade_do_cep({"total": "0", "cidade": "", "uf": ""}))
        self.assertIsNone(op.cidade_do_cep(None))


if __name__ == "__main__":
    unittest.main()
