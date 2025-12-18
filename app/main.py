from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from app.services.knowledge_check import run_knowledge_check

app = FastAPI()

# Servir arquivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

THEORY_PATH = Path("data/teoria.txt")

class KnowledgeCheckRequest(BaseModel):
    domain: str
    user_text: str

@app.get("/", response_class=HTMLResponse)
def index():
    return Path("static/index.html").read_text(encoding="utf-8")

@app.post("/check-knowledge")
def check_knowledge(request: KnowledgeCheckRequest):
    theory_text = THEORY_PATH.read_text(encoding="utf-8")

    result = run_knowledge_check(
        domain=request.domain,
        theory_text=theory_text,
        user_text=request.user_text
    )

    return result

