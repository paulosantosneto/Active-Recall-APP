# app/crew/outline_crew.py
#
# Gera o roteiro do modo "com dicas": a lista de termos que o resumo deveria
# tocar, agrupados por assunto. É o oposto da análise — aqui o modelo lê só a
# teoria e não vê nada do que o usuário escreveu.

from crewai import Agent, Crew, Process, Task, LLM
from crewai.project import CrewBase, agent, task, crew
from dotenv import load_dotenv
import os

from app.schemas import TopicOutline

load_dotenv()


@CrewBase
class TopicOutlineCrew:

    llm = LLM(
        model=os.getenv("MODEL_URL"),
        base_url=os.getenv("BASE_URL"),
        api_key=os.getenv("OPENAI_API_KEY"),
        temperature=0.2
    )

    def __init__(self, domain_role: str):
        self.domain_role = domain_role

    @agent
    def outliner(self) -> Agent:
        return Agent(
            role=f"Organizador de roteiro de estudo em {self.domain_role}",
            goal=("Listar, em português do Brasil, os termos que um bom resumo do "
                  "conteúdo precisa mencionar, sem jamais explicá-los."),
            backstory=(
                "Você é um professor brasileiro que monta checklists de revisão. "
                "Você lista o que precisa ser lembrado, nunca a resposta: quem "
                "estuda tem que puxar o conteúdo da própria memória. Escreve "
                "sempre em português do Brasil, mantendo em inglês os termos "
                "técnicos consagrados da área."
            ),
            llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

    @task
    def outline_task(self) -> Task:
        prompt = """
⚠️⚠️ REGRA NÚMERO UM — A DICA NÃO PODE ENTREGAR A RESPOSTA.
Este roteiro é lido por alguém ANTES de escrever um resumo de memória. Ele
serve para lembrar O QUE citar, nunca O QUE É. Um termo com definição junto
destrói o exercício.
- ERRADO: "overfitting: quando o modelo decora o treino e erra em dados novos"
- ERRADO: "árvore de decisão (divide o espaço em regiões por perguntas)"
- CERTO:  "overfitting"
- CERTO:  "árvore de decisão"
Nada de dois pontos, parênteses explicativos, travessões ou qualquer aposto
descrevendo o termo. Só o nome dele.

⚠️⚠️ REGRA NÚMERO DOIS — O TERMO SAI COMO A TEORIA ESCREVE.
Cada termo é COPIADO da teoria, na mesma forma e no mesmo idioma em que
aparece lá. É PROIBIDO traduzir.
- A teoria escreve "Redundância não controlada"? O termo é "Redundância não
  controlada". Jamais "Uncontrolled Redundancy".
- A teoria escreve "Acesso concorrente"? É "Acesso concorrente", nunca
  "Concurrent Access".
- A teoria escreve "NoSQL", "Big Data", "Key-Value"? Então fica assim mesmo,
  porque é assim que a teoria escreve. Você não traduziu: você copiou.
Se a teoria está em português, o roteiro sai em português. Traduzir destrói o
exercício — quem estuda precisa reconhecer o termo do próprio material.

Os TÍTULOS dos blocos são a única coisa que você redige: escreva-os em
PORTUGUÊS DO BRASIL.

Leia a teoria abaixo e monte o roteiro.

ETAPA 1: identifique os grandes assuntos da teoria. Cada um vira um bloco, com
um título curto (2 a 5 palavras) que diga o assunto sem entregar o conteúdo.

ETAPA 2: em cada bloco, liste os termos, nomes, modelos, propriedades,
algoritmos, fórmulas ou distinções que o resumo precisa mencionar. Cada termo
é uma entrada curta — no máximo 5 palavras.

REGRAS:
- Entre 2 e 8 blocos, com 2 a 10 termos cada.
- Só entra o que EXISTE na teoria. Não invente termos relacionados que a
  teoria não cita.
- Nada de repetir o mesmo termo em blocos diferentes.
- Ordene os blocos na ordem em que os assuntos aparecem na teoria.

────────────────────────
📘 TEORIA:
{theory_text}

────────────────────────
LEMBRETE FINAL: cada termo é só o NOME, COPIADO DA TEORIA no idioma dela. Sem
definição, sem explicação, sem exemplo, sem tradução. Antes de responder,
confira duas coisas em cada termo:
1. ele aparece com essas palavras na teoria acima?
2. sobrou algum dois-pontos, parêntese explicativo ou travessão? Apague o que
   veio depois.

RETORNE APENAS JSON PURO:

{
  "blocks": [
    {
      "title": "",
      "terms": ["", ""]
    }
  ]
}
"""

        return Task(
            description=prompt,
            agent=self.outliner(),
            output_pydantic=TopicOutline,
            expected_output="JSON conforme TopicOutline"
        )

    @crew
    def outline_crew(self) -> Crew:
        return Crew(
            agents=[self.outliner()],
            tasks=[self.outline_task()],
            process=Process.sequential,
            verbose=True
        )
