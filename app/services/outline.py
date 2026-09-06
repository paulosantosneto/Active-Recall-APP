# app/services/outline.py

import re

from app.crew.outline_crew import TopicOutlineCrew
from app.services.resposta import extrair_json

# O modelo escorrega e cola a definição no termo ("overfitting: quando o
# modelo decora"). Isso entrega a resposta e mata o exercício, então o corte
# é feito aqui também, sem depender da boa vontade dele.
_DEFINICAO = re.compile(r"\s*[:—–]\s.*$|\s*\((?=[^)]{18,}).*$", re.S)


def _limpar_termo(t: str) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip(" .;,-*•")
    t = _DEFINICAO.sub("", t).strip()
    return t[:60]


def _limpar(blocos) -> list[dict]:
    saida, vistos = [], set()
    for b in blocos or []:
        if not isinstance(b, dict):
            continue
        titulo = re.sub(r"\s+", " ", str(b.get("title") or "")).strip()[:80]
        termos = []
        for t in b.get("terms") or []:
            termo = _limpar_termo(t)
            chave = termo.lower()
            # o mesmo termo em dois blocos vira ruído no checklist
            if termo and chave not in vistos:
                vistos.add(chave)
                termos.append(termo)
        if titulo and termos:
            saida.append({"title": titulo, "terms": termos})
    return saida


def run_outline(domain, theory_text):
    crew = TopicOutlineCrew(domain_role=domain).outline_crew()
    dados = extrair_json(crew.kickoff(inputs={"theory_text": theory_text}))

    blocos = _limpar(dados.get("blocks") if isinstance(dados, dict) else None)
    if not blocos:
        raise ValueError("O modelo não devolveu nenhum termo utilizável")
    return blocos
