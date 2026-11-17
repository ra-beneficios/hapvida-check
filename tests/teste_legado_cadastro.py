"""legado_cadastro (17/11/2026, André MOV-15): a base certificada da Hapvida pelo Legado (o que a ficha preenche)."""
import sys
import unittest

sys.path.append("/app")              # na imagem o teste roda de /app
import legado_cadastro as lc  # noqa: E402

MAURICIO = ('{"cpf":[ {"total":"1","nm_beneficiario":"MAURICIO NONATO DA SILVA FILHO","dt_nascimento":"26/07/1969",'
            '"nm_mae":"MARIA SANTANA  RODRIGUES COSTA","cpf_higienizado":"S"}]}\n')


class Resposta(unittest.TestCase):
    def test_le_nome_nascimento_e_mae(self):
        self.assertEqual(lc.ler_resposta(MAURICIO), {"nome": "MAURICIO NONATO DA SILVA FILHO", "nascimento": "26/07/1969",
                                                    "mae": "MARIA SANTANA RODRIGUES COSTA", "higienizado": True})

    def test_cpf_que_a_base_nao_tem(self):
        self.assertIsNone(lc.ler_resposta('{"cpf":[]}'))
        self.assertIsNone(lc.ler_resposta('{"cpf":[{"total":"0","nm_beneficiario":""}]}'))
        self.assertIsNone(lc.ler_resposta(""))


class Cep(unittest.TestCase):
    def test_cidade_do_cep(self):
        r = ('{"cep":[ {"total":"1","nm_logradouro":"SALVADOR DE MENDONCA","cidade":"ITABORAI","bairro":"CENTRO",'
             '"uf":"RJ","cd_tipo_logradouro":"R"}]}')
        self.assertEqual(lc.ler_cep(r), {"cidade": "ITABORAI", "uf": "RJ", "bairro": "CENTRO",
                                         "logradouro": "SALVADOR DE MENDONCA"})

    def test_cep_que_o_legado_nao_conhece(self):
        self.assertIsNone(lc.ler_cep('{"total":"0","nm_logradouro":"","cidade":"","bairro":"","uf":""}'))


if __name__ == "__main__":
    unittest.main()
