# app/services/knowledge_check.py

from app.crew.knowledge_crew import KnowledgeCheckCrew
from app.services.resposta import extrair_json


def run_knowledge_check(domain, theory_text, user_text):
    crew = KnowledgeCheckCrew(domain_role=domain).knowledge_check_crew()
    return extrair_json(crew.kickoff(inputs={
        "theory_text": theory_text,
        "user_text": user_text,
    }))
