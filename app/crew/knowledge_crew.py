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
            goal=("Avaliar lacunas de conhecimento comparando teoria oficial com texto "
                  "do usuário, redigindo toda a avaliação em português do Brasil."),
            backstory=(
                "Você é um professor brasileiro, experiente, criterioso e técnico, "
                "especializado em diagnóstico de aprendizado. Você escreve sempre "
                "em português do Brasil e jamais responde em inglês, ainda que "
                "mantenha em inglês os termos técnicos consagrados da área."
            ),
            llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

    # 🧠 TASK
    @task
    def knowledge_check_task(self) -> Task:
        prompt = """
⚠️⚠️ IDIOMA — REGRA ABSOLUTA, ANTES DE QUALQUER OUTRA:
Escreva TODO texto que você mesmo redigir em PORTUGUÊS DO BRASIL. Isso vale
para "explanation", "concept" e "missing_concepts", e vale mesmo que a teoria
ou o texto do usuário estejam em outro idioma.
- É PROIBIDO redigir frases em inglês. Nunca escreva coisas como
  "The user did not mention..." ou "The user incorrectly stated that...".
  Escreva "Não mencionou..." ou "Disse, incorretamente, que...".
- Termo técnico consagrado em inglês fica como está, sem tradução forçada:
  throughput, Round Robin, FCFS, ACID, cache, swapping, deadlock, buffer.
  A REGRA É SOBRE A FRASE, não sobre o jargão.
- EXCEÇÃO: "evidence" e "reference" são CÓPIAS LITERAIS. Eles ficam no idioma
  original do texto de onde foram copiados. NUNCA traduza esses dois campos —
  traduzir quebra a localização do trecho no texto original.
- Escreva direto e curto, sem repetir "O usuário" em toda frase.

Compare o conhecimento do usuário com a teoria oficial.

ETAPA 1: Extraia TODOS os conceitos essenciais da teoria.

ETAPA 2: Para CADA conceito extraído, compare semanticamente com o texto do usuário.

ETAPA 3: Gere OBRIGATORIAMENTE uma entrada em "detailed_analysis" PARA CADA CONCEITO,
mesmo quando o conceito NÃO FOI MENCIONADO PELO USUÁRIO.

ETAPA 4: Preencha "evidence" com o TRECHO LITERAL do texto do usuário que trata
daquele conceito.

⚠️ REGRAS DO CAMPO "evidence":
- COPIE caractere por caractere do texto do usuário. Não reescreva, não corrija
  a ortografia, não resuma, não traduza, não junte pedaços separados.
- Copie apenas o trecho relevante: de uma frase a duas, não o texto inteiro.
- Se o usuário NÃO escreveu nada sobre o conceito, "evidence" DEVE ser "".
- O trecho tem que existir EXATAMENTE dentro do texto do usuário. Se você não
  conseguir copiar literalmente, deixe "".

ETAPA 5: Preencha "reference" com o TRECHO LITERAL DA TEORIA que define aquele
conceito — copiado da teoria, não do texto do usuário.

⚠️ REGRAS DO CAMPO "reference":
- COPIE da TEORIA, caractere por caractere. Não reescreva nem resuma.
- Traga o trecho completo o bastante para a pessoa ESTUDAR por ele: a definição
  inteira, com os exemplos e a enumeração que vierem junto.
- OBRIGATÓRIO para status "not_remembered", "partially_remembered" e
  "incorrect". Para "remembered" pode ficar "".

⚠️ VALORES DE "status" (use exatamente um destes):
- "remembered"            → explicou o conceito corretamente
- "partially_remembered"  → mencionou, mas incompleto ou impreciso
- "incorrect"             → escreveu algo que CONTRADIZ a teoria
- "not_remembered"        → não mencionou (então "evidence" é "")

⚠️ REGRA OBRIGATÓRIA:
- O tamanho de "detailed_analysis" DEVE SER IGUAL a "total_concepts".
- É PROIBIDO omitir conceitos da análise detalhada.
- "coverage_percentage" considera lembrados como 1 e parciais como 0,5;
  incorretos e não lembrados contam 0.

────────────────────────
📘 TEORIA:
{theory_text}

────────────────────────
✍️ TEXTO DO USUÁRIO:
{user_text}

────────────────────────
LEMBRETE FINAL: "explanation", "concept" e "missing_concepts" em PORTUGUÊS DO
BRASIL. "evidence" e "reference" copiados literalmente, sem tradução.

RETORNE APENAS JSON PURO:

{
  "total_concepts": 0,
  "remembered": 0,
  "partially_remembered": 0,
  "not_remembered": 0,
  "incorrect": 0,
  "coverage_percentage": 0.0,
  "missing_concepts": [],
  "detailed_analysis": [
    {
      "concept": "",
      "status": "",
      "explanation": "",
      "evidence": "",
      "reference": ""
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

