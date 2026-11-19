# app/services/knowledge_check.py

from app import storage
from app.crew.knowledge_crew import KnowledgeCheckCrew
from app.services.resposta import extrair_json

# Modo "refazer": a pessoa reescreve só os pontos que errou. O bloco entra no
# prompt antes da teoria e troca a ETAPA 1 (extrair todos os conceitos) pela
# lista fechada; sem foco, o bloco é vazio e o prompt fica como sempre foi.
ESCOPO = """
────────────────────────
🎯 ESCOPO DESTA AVALIAÇÃO — LISTA FECHADA DE CONCEITOS:
O usuário está REFAZENDO apenas os conceitos listados abaixo, que errou ou
esqueceu na tentativa anterior. Nesta avaliação:
- NÃO faça a ETAPA 1: não extraia nenhum outro conceito da teoria.
- "detailed_analysis" deve ter EXATAMENTE um item para cada conceito da lista,
  na mesma ordem, com "concept" copiado IGUAL ao nome que está na lista.
- "total_concepts" é o tamanho da lista. O que a teoria tiver além da lista
  NÃO conta como conceito não lembrado.
- Use a teoria como gabarito para julgar cada conceito da lista.

CONCEITOS A AVALIAR:
{lista}
"""


def run_knowledge_check(domain, theory_text, user_text, focus=None):
    crew = KnowledgeCheckCrew(domain_role=domain).knowledge_check_crew()
    foco = [str(c).strip() for c in (focus or []) if str(c).strip()]
    bloco = ESCOPO.format(lista="\n".join(f"{i}. {c}" for i, c in enumerate(foco, 1))) if foco else ""
    # Última parada antes do modelo: a limpeza é refeita aqui, mesmo que quem
    # chamou já tenha feito, para imagem nenhuma sair por este caminho.
    return extrair_json(crew.kickoff(inputs={
        "theory_text": storage.para_modelo(theory_text),
        "user_text": storage.sem_imagens(user_text),
        "focus_block": storage.sem_imagens(bloco),
    }))
