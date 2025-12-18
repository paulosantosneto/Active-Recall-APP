from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import os  # <--- Faltava esta importação

from app.services.knowledge_check import run_knowledge_check

app = FastAPI()

# Servir arquivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

# Configuração do diretório de dados
THEORY_DIR = Path("data")
THEORY_DIR.mkdir(parents=True, exist_ok=True)

class TheoryUpdate(BaseModel):
    name: str
    content: str

class KnowledgeCheckRequest(BaseModel):
    theory_name: str
    user_text: str
    domain: str

# --- ROTAS DE INTERFACE ---

@app.get("/", response_class=HTMLResponse)
def index():
    # Caminho para o seu arquivo HTML na pasta static
    index_path = Path("static/index.html")
    if index_path.exists():
        return index_path.read_text(encoding="utf-8")
    return "Erro: static/index.html não encontrado."

# --- ROTAS DE TEORIA ---

@app.get("/list-theories")
def list_theories():
    # Lista arquivos .txt ignorando a extensão para exibir no select
    files = [f.replace(".txt", "") for f in os.listdir(THEORY_DIR) if f.endswith(".txt")]
    return {"theories": files}

@app.get("/get-theory/{name}")
def get_theory(name: str):
    file_path = THEORY_DIR / f"{name}.txt"
    if file_path.exists():
        return {"content": file_path.read_text(encoding="utf-8")}
    return {"content": ""}

@app.post("/save-theory")
def save_theory(data: TheoryUpdate):
    # Salva o arquivo com o nome vindo da interface
    file_path = THEORY_DIR / f"{data.name}.txt"
    file_path.write_text(data.content, encoding="utf-8")
    return {"message": f"Teoria '{data.name}' salva com sucesso!"}

# --- ROTA DE ANÁLISE ---

@app.post("/check-knowledge")
def check_knowledge(request: KnowledgeCheckRequest):
    theory_path = THEORY_DIR / f"{request.theory_name}.txt"
    
    if not theory_path.exists():
        return {"detail": "Teoria não encontrada"}, 404
        
    theory_text = theory_path.read_text(encoding="utf-8")
    
    # Chama o serviço que integra com a CrewAI
    result = run_knowledge_check(
        domain=request.domain,
        theory_text=theory_text,
        user_text=request.user_text
    )
    return result