# app/services/resposta.py
#
# O CrewAI devolve o texto cru do modelo em lugares diferentes conforme a
# versão, e modelos gratuitos gostam de embrulhar o JSON em ```json. Os dois
# serviços passam por aqui para não duplicar esse tratamento.

import json
import re


def extrair_json(result):
    bruto = None

    # caso padrão do CrewAI
    if hasattr(result, "tasks_output") and result.tasks_output:
        bruto = result.tasks_output[0].raw
    elif hasattr(result, "raw"):
        bruto = result.raw

    if not bruto:
        raise ValueError("Nenhum output válido retornado pela Crew")

    return json.loads(re.sub(r"```json|```", "", bruto).strip())
