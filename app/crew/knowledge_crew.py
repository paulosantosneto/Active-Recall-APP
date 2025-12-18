# app/crew/knowledge_crew.py

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, task, crew
from dotenv import load_dotenv
import os

from app.schemas import KnowledgeCheckResult

load_dotenv()


@CrewBase
class KnowledgeCheckCrew:

    llm = LLM(
        model=os.getenv("MODEL_URL"),
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.2
    )

    def __init__(self, domain_role: str):
        self.domain_role = domain_role

    # 👨‍🏫 AGENT
    @agent
    def knowledge_evaluator(self) -> Agent:
        return Agent(
            role=f"Avaliador especialista em {self.domain_role}",
            goal="Avaliar lacunas de conhecimento comparando teoria oficial com texto do usuário.",
            backstory=(
                "Você é um professor experiente, criterioso e técnico, "
                "especializado em diagnóstico de aprendizado."
            ),
            llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

    # 🧠 TASK
    @task
    def knowledge_check_task(self) -> Task:
        prompt = """
Compare o conhecimento do usuário com a teoria oficial.

ETAPA 1: Extraia TODOS os conceitos essenciais da teoria.

ETAPA 2: Para CADA conceito extraído, compare semanticamente com o texto do usuário.

ETAPA 3: Gere OBRIGATORIAMENTE uma entrada em "detailed_analysis" PARA CADA CONCEITO,
mesmo quando o conceito NÃO FOI MENCIONADO PELO USUÁRIO.

⚠️ REGRA OBRIGATÓRIA:
- O tamanho de "detailed_analysis" DEVE SER IGUAL a "total_concepts".
- Conceitos não mencionados DEVEM aparecer com status "not_remembered".
- É PROIBIDO omitir conceitos da análise detalhada.

Se algum conceito estiver ausente do texto do usuário, ele DEVE constar explicitamente
em "detailed_analysis" com uma explicação clara do que faltou.

────────────────────────
📘 TEORIA:
{theory_text}

────────────────────────
✍️ TEXTO DO USUÁRIO:
{user_text}

────────────────────────
RETORNE APENAS JSON PURO:

{
  "total_concepts": 0,
  "remembered": 0,
  "partially_remembered": 0,
  "not_remembered": 0,
  "coverage_percentage": 0.0,
  "missing_concepts": [],
  "detailed_analysis": [
    {
      "concept": "",
      "status": "",
      "explanation": ""
    }
  ]
}
"""

        return Task(
            description=prompt,
            agent=self.knowledge_evaluator(),
            output_pydantic=KnowledgeCheckResult,
            expected_output="JSON conforme KnowledgeCheckResult"
        )

    # 🧩 CREW
    @crew
    def knowledge_check_crew(self) -> Crew:
        return Crew(
            agents=[self.knowledge_evaluator()],
            tasks=[self.knowledge_check_task()],
            process=Process.sequential,
            verbose=True
        )

