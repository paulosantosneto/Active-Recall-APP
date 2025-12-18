# app/schemas.py
from pydantic import BaseModel
from typing import List

class KnowledgeGap(BaseModel):
    concept: str
    status: str  # lembrado | parcial | não lembrado
    explanation: str

class KnowledgeCheckResult(BaseModel):
    total_concepts: int
    remembered: int
    partially_remembered: int
    not_remembered: int
    coverage_percentage: float
    missing_concepts: List[str]
    detailed_analysis: List[KnowledgeGap]

