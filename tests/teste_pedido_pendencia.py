"""09/11/2026 (MOV-14 Angelo/Helloá): correção da data de nascimento no pedido Pendente (pedido_pendencia)."""
import sys
import unittest

sys.path[:0] = ["/app", "/lifetools_service"]
import pedido_pendencia as pp  # noqa: E402


class DataNoPedido(unittest.TestCase):
    def test_decide_a_data_e_monta_o_payload_como_a_tela(self):
        assert pp.decidir_data("11/11/2024", None, None, "11/11/2014") == ("11/11/2014", "Receita")
        assert pp.decidir_data("05/09/2014", None, "05/09/2017", "05/09/2014") == ("05/09/2017", "cadastro da Hapvida")
        assert pp.decidir_data("02/10/1997", None, None, "02/10/1997") == (None, "")
        assert pp.decidir_data("02/10/1997", "01/10/1997", "05/09/2017", None) == ("01/10/1997", "pós-vendas")
        deps={"holderCode":"1","holderName":"T","holderDocument":"16796248732","holderPartnerCompany":"4DFEV","holderUnitCompany":"1",
         "dependents":[{"code":"9","sequentialNumber":"2","name":"A","birthDate":"2024-11-11T00:00:00","document":"21695023781","beneficiaryType":{"beneficiaryTypeCode":"2"},"plan":{"startDate":"2026-11-05T00:00:00","healthPlan":{"healthPlanCode":"32551"}},"payWaitingPeriod":{"segmentation":{},"accommodation":{}}},
          {"code":"8","sequentialNumber":"1","name":"B","birthDate":"1991-10-17T00:00:00","document":"6127286760","plan":{}}]}
        c=pp.montar(deps,{},"21695023781","11/11/2014",{"name":"Cora","email":"c@x"})
        assert c["dependents"][0]["birthDate"]=="2014-11-11T00:00:00Z" and c["dependents"][0]["operationType"]==2
        assert c["dependents"][1]["operationType"]==0 and c["dependents"][1]["document"]=="06127286760"
        assert "payWaitingPeriod" not in c["dependents"][0]


if __name__ == "__main__":
    unittest.main()
