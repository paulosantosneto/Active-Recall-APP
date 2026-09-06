# app/schemas.py
from pydantic import BaseModel
from typing import List


class KnowledgeGap(BaseModel):
    concept: str
    status: str  # remembered | partially_remembered | incorrect | not_remembered
    explanation: str
    # Trecho LITERAL do texto do usuário que trata deste conceito. É o que
    # permite marcar o texto na interface; vazio quando o usuário não escreveu
    # nada sobre o conceito.
    evidence: str = ""
    # Trecho LITERAL da teoria que define este conceito. Serve para estudar o
    # que faltou sem sair da análise.
    reference: str = ""


class OutlineBlock(BaseModel):
    """Um agrupamento de termos do roteiro: 'Modelos de árvore' e, dentro,
    'árvore de decisão', 'random forest'..."""
    title: str
    terms: List[str]


class TopicOutline(BaseModel):
    blocks: List[OutlineBlock]


class KnowledgeCheckResult(BaseModel):
    total_concepts: int
    remembered: int
    partially_remembered: int
    not_remembered: int
    incorrect: int = 0
    coverage_percentage: float
    missing_concepts: List[str]
    detailed_analysis: List[KnowledgeGap]
