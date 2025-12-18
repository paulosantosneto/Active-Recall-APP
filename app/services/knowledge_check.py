# app/services/knowledge_check.py

import json
import re
from app.crew.knowledge_crew import KnowledgeCheckCrew

def run_knowledge_check(domain, theory_text, user_text):
    crew_instance = KnowledgeCheckCrew(domain_role=domain)
    crew = crew_instance.knowledge_check_crew()

    result = crew.kickoff(
        inputs={
            "theory_text": theory_text,
            "user_text": user_text
        }
    )

    # 🔥 EXTRAÇÃO CORRETA DO RESULTADO
    raw_output = None

    # Caso padrão do CrewAI
    if hasattr(result, "tasks_output") and result.tasks_output:
        raw_output = result.tasks_output[0].raw

    # Fallback
    elif hasattr(result, "raw"):
        raw_output = result.raw

    if not raw_output:
        raise ValueError("Nenhum output válido retornado pela Crew")

    # Remove ```json ``` se existirem
    clean_json = re.sub(r"```json|```", "", raw_output).strip()

    return json.loads(clean_json)
