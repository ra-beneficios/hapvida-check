"""10/11/2026 (MOV-4 Magnolia): incluir dependente no pedido Pendente do titular (pedido_dependente)."""
import sys
import unittest

sys.path[:0] = ["/app", "/lifetools_service"]
import pedido_dependente as pd  # noqa: E402

# o que a Hapvida devolveu no pedido 68953885 (10/11, só leitura), sem dado pessoal
HOLDER = {"controlNumber": 68953885, "company": {"partnerCompany": "PEG8N", "unit": 1},
          "movementStatus": {"code": "1", "description": "Pendente"},
          "holder": {"name": "TITULAR", "document": "25418114549", "plan": {"startDate": "2026-09-30T00:00:00",
                     "healthPlan": {"healthPlanCode": "32551"}, "dentalPlan": {}}}}
DEPS = {"holderPartnerCompany": "PEG8N", "holderMovementStatusCode": "1"}
PERG = [{"numero": "1", "titulo": "APARELHO DIGESTIVO", "texto": ""}, {"numero": "2", "titulo": "ORTOPÉDICAS", "texto": ""}]
NOVO = {"nome": "MARIA TESTE", "cpf": "1234567890", "nascimento": "30/12/1973", "sexo": "F", "estado_civil": "1",
        "mae": "MAE TESTE", "tipo": "1", "casamento": "21/03/2003",
        "declaracao": [{"questionNumber": "1", "response": False, "description": ""},
                       {"questionNumber": "2", "response": True, "description": "hérnia de disco desde 2019"}]}


class IncluirDependente(unittest.TestCase):
    def test_payload_como_a_tela_novo_vai_added_no_plano_do_titular(self):
        c = pd.montar(DEPS, HOLDER, NOVO, "PEG8N", {"name": "Cora", "email": "c@x"})
        d = c["dependents"][0]
        self.assertEqual((d["operationType"], d["document"], d["beneficiaryType"], d["civilStatus"]), (1, "01234567890", 1, 1))
        self.assertEqual(d["birthDate"], "1973-12-30T00:00:00Z")
        self.assertEqual(d["marriageDate"], "2003-03-21T00:00:00Z")
        self.assertEqual(d["plan"]["health"]["code"], "32551")
        self.assertEqual(d["plan"]["startDate"], "2026-09-30T00:00:00Z")
        self.assertEqual(d["plan"]["startDateType"], "4")
        self.assertEqual([q["response"] for q in d["healthDeclarations"]], [False, True])
        self.assertEqual(c["holderCode"], "68953885")
        self.assertEqual(c["holderDocument"], "25418114549")
        self.assertEqual(c["company"]["partnerCompany"], "PEG8N")
        self.assertNotIn("code", d)

    def test_os_que_ja_estao_no_pedido_vao_iguais(self):
        deps = dict(DEPS, dependents=[{"code": "9", "sequentialNumber": "1", "name": "FILHO", "document": "11111111111",
                                       "birthDate": "2010-01-01T00:00:00", "plan": {}}])
        c = pd.montar(deps, HOLDER, NOVO, "PEG8N", {})
        self.assertEqual([x["operationType"] for x in c["dependents"]], [0, 1])
        self.assertEqual(c["dependents"][1]["sequentialNumber"], 2)

    def test_nada_e_inventado_sem_resposta_ou_dado_nao_inclui(self):
        self.assertEqual(pd.validar(NOVO, PERG), [])
        sem = dict(NOVO, declaracao=NOVO["declaracao"][:1], casamento="")
        self.assertEqual(pd.validar(sem, PERG), ["casamento", "pergunta 2"])
        sim_vazio = dict(NOVO, declaracao=[{"questionNumber": "1", "response": False}, {"questionNumber": "2", "response": True}])
        self.assertEqual(pd.validar(sim_vazio, PERG), ["descrição da pergunta 2"])

    def test_perguntas_da_tela(self):
        p = pd.perguntas_de({"data": [{"code": "1", "description": "A", "complement": "x", "isDisplay": True},
                                      {"code": "2", "description": "B", "isDisplay": False}]})
        self.assertEqual(p, [{"numero": "1", "titulo": "A", "texto": "x"}])


if __name__ == "__main__":
    unittest.main()
