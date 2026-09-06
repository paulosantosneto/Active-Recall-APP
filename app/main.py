from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

from app import storage
from app.services.knowledge_check import run_knowledge_check
from app.services.outline import run_outline

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

movidos = storage.migrar_soltos()
if movidos:
    print(f"[storage] layout antigo migrado para pastas de disciplina: {movidos}")


class TheoryUpdate(BaseModel):
    discipline: str
    topic: str
    content: str


class KnowledgeCheckRequest(BaseModel):
    discipline: str
    topic: str
    user_text: str
    mode: str = "livre"          # "livre" (sem dicas) ou "dicas" (com roteiro)


# --- INTERFACE ---

@app.get("/", response_class=HTMLResponse)
def index():
    p = Path("static/index.html")
    if not p.exists():
        raise HTTPException(500, "static/index.html não encontrado")
    return p.read_text(encoding="utf-8")


# --- DISCIPLINAS E TEORIAS ---

@app.get("/disciplines")
def disciplines():
    return {"disciplines": storage.listar()}


@app.get("/theory")
def get_theory(discipline: str, topic: str):
    try:
        conteudo = storage.ler(discipline, topic)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if conteudo is None:
        raise HTTPException(404, "Tópico não encontrado")
    return {"content": conteudo}


@app.delete("/theory")
def delete_theory(discipline: str, topic: str):
    try:
        tentativas = storage.apagar_topico(discipline, topic)
    except FileNotFoundError:
        raise HTTPException(404, "Tópico não encontrado")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": f"'{topic}' apagado.", "attempts_removed": tentativas}


@app.delete("/discipline")
def delete_discipline(discipline: str):
    try:
        topicos, tentativas = storage.apagar_disciplina(discipline)
    except FileNotFoundError:
        raise HTTPException(404, "Disciplina não encontrada")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": f"'{discipline}' apagada.",
            "topics_removed": topicos, "attempts_removed": tentativas}


@app.post("/theory")
def save_theory(data: TheoryUpdate):
    if not data.content.strip():
        raise HTTPException(400, "Conteúdo vazio")
    try:
        storage.gravar(data.discipline, data.topic, data.content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": f"'{data.topic}' salvo em {data.discipline}."}


# --- IMAGENS DA TEORIA ---
#
# O corpo é a imagem crua e o Content-Type diz o formato: o clipboard entrega
# um Blob, que o fetch manda direto sem precisar embrulhar em multipart.

@app.post("/image")
async def upload_image(request: Request, discipline: str, topic: str):
    try:
        nome, existia = storage.salvar_imagem(
            discipline, topic, await request.body(),
            request.headers.get("content-type", ""),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"name": nome, "existed": existia}


@app.get("/images")
def list_images(discipline: str, topic: str):
    try:
        return {"images": storage.listar_imagens(discipline, topic)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/image")
def get_image(discipline: str, topic: str, name: str):
    try:
        p = storage.caminho_imagem(discipline, topic, name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not p.is_file():
        raise HTTPException(404, "Imagem não encontrada")
    return FileResponse(p)


@app.delete("/image")
def delete_image(discipline: str, topic: str, name: str):
    try:
        storage.apagar_imagem(discipline, topic, name)
    except FileNotFoundError:
        raise HTTPException(404, "Imagem não encontrada")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"message": f"'{name}' removida."}


# --- ROTEIRO DE DICAS ---
#
# GET só devolve o que já está em cache: gerar custa uma chamada ao modelo e
# não pode acontecer por acidente, ao trocar de aba. Quem gera é o POST.

def _teoria(discipline: str, topic: str) -> str:
    try:
        t = storage.ler(discipline, topic)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if t is None:
        raise HTTPException(404, "Tópico não encontrado")
    return t


@app.get("/outline")
def get_outline(discipline: str, topic: str):
    d = storage.ler_roteiro(discipline, topic, _teoria(discipline, topic))
    return d or {"blocks": None}


@app.post("/outline")
def make_outline(discipline: str, topic: str):
    teoria = storage.sem_imagens(_teoria(discipline, topic))
    if len(teoria.strip()) < 80:
        raise HTTPException(400, "A teoria é curta demais para virar um roteiro.")
    try:
        blocos = run_outline(domain=f"{discipline} (tópico: {topic})", theory_text=teoria)
    except ValueError as e:
        raise HTTPException(502, str(e))
    return storage.gravar_roteiro(discipline, topic, teoria, blocos,
                                  storage.fora_da_teoria(blocos, teoria))


# --- ANÁLISE ---

@app.post("/check-knowledge")
def check_knowledge(request: KnowledgeCheckRequest):
    try:
        teoria = storage.ler(request.discipline, request.topic)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if teoria is None:
        raise HTTPException(404, "Tópico não encontrado")

    # a disciplina é o domínio de especialidade do avaliador; o tópico entra
    # como recorte dentro dela
    resultado = run_knowledge_check(
        domain=f"{request.discipline} (tópico: {request.topic})",
        # os marcadores de imagem saem aqui: a imagem nunca vai para o modelo
        theory_text=storage.sem_imagens(teoria),
        user_text=request.user_text,
    )
    storage.registrar(request.discipline, request.topic, resultado, request.mode)
    return resultado


# --- ESTATÍSTICAS ---

@app.get("/stats")
def stats():
    return storage.estatisticas()


@app.get("/history")
def history(discipline: str, topic: str | None = None):
    return storage.historico(discipline, topic)
