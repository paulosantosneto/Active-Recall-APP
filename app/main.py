from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
from urllib.parse import quote

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
    # modo "refazer": só estes conceitos são avaliados e nada vai para o histórico
    focus: list[str] = []


class DraftUpdate(BaseModel):
    discipline: str
    topic: str
    text: str = ""
    delta: dict | None = None    # conteúdo do editor com a formatação


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


# --- RASCUNHO DO RESUMO ---

@app.get("/draft")
def get_draft(discipline: str, topic: str):
    try:
        d = storage.ler_rascunho(discipline, topic)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return d or {"text": "", "delta": None}


@app.post("/draft")
def save_draft(data: DraftUpdate):
    try:
        storage.gravar_rascunho(data.discipline, data.topic, data.text, data.delta)
    except FileNotFoundError:
        raise HTTPException(404, "Tópico não encontrado")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# --- BUSCA ---

@app.get("/search")
def search(q: str):
    return storage.buscar(q)


# --- EXPORTAÇÃO E IMPORTAÇÃO ---
#
# O .zip sai daqui; o PDF é montado no navegador, que já sabe desenhar a
# teoria (negrito, fórmulas, imagens) do jeito que ela aparece na prévia.

@app.get("/export/zip")
def export_zip(discipline: str):
    try:
        dados = storage.exportar_zip(discipline)
        nome = storage.nome_seguro(discipline) + ".zip"
    except FileNotFoundError:
        raise HTTPException(404, "Disciplina não encontrada")
    except ValueError as e:
        raise HTTPException(400, str(e))
    # cabeçalho HTTP é latin-1: o nome com acento vai no filename* (RFC 5987)
    simples = nome.encode("ascii", "ignore").decode().replace('"', "").strip() or "disciplina.zip"
    return Response(dados, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename=\"{simples}\"; filename*=UTF-8''{quote(nome)}"})


# O corpo é o .zip cru, como no envio de imagem. `name` é o nome do arquivo
# sem a extensão: vira a disciplina dos .txt que vierem soltos na raiz.

@app.post("/import/zip")
async def import_zip(request: Request, name: str = "", overwrite: bool = False):
    try:
        padrao = storage.nome_seguro(name)
    except ValueError:
        padrao = storage.DISCIPLINA_PADRAO
    try:
        return storage.importar_zip(await request.body(), padrao, overwrite)
    except ValueError as e:
        raise HTTPException(400, str(e))


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
    teoria = storage.para_modelo(_teoria(discipline, topic))
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
    foco = [c.strip()[:200] for c in request.focus if c.strip()][:80]
    resultado = run_knowledge_check(
        domain=f"{request.discipline} (tópico: {request.topic})",
        # imagens e asteriscos de ênfase saem aqui: nada disso vai para o modelo
        theory_text=storage.para_modelo(teoria),
        user_text=request.user_text,
        focus=foco,
    )
    if foco:
        return _so_o_foco(resultado, foco)
    reg = storage.registrar(request.discipline, request.topic, resultado, request.mode,
                            request.user_text)
    # analisado, o texto mora na tentativa; o rascunho já cumpriu o papel
    storage.apagar_rascunho(request.discipline, request.topic)
    # a tela mostra a mesma cobertura que foi para o histórico, recalculada a
    # partir das contagens: a que o modelo devolve vem com frequência na
    # escala errada (1 querendo dizer 100%)
    resultado["coverage_percentage"] = reg["coverage"]
    return resultado


def _so_o_foco(resultado: dict, foco: list[str]) -> dict:
    """Resultado do modo "refazer": só os conceitos pedidos, com as contagens e
    a cobertura refeitas sobre eles. Não vai para o histórico nem para a agenda
    de revisão — reescrever logo depois de ler a correção não mede memória."""
    itens = [it for it in resultado.get("detailed_analysis") or [] if isinstance(it, dict)]
    pedidos = {storage._canonico(c) for c in foco}
    # o modelo às vezes devolve conceitos a mais; se ele renomeou todos, não há
    # como separar e fica o que veio
    dentro = [it for it in itens if storage._canonico(it.get("concept")) in pedidos]
    itens = dentro or itens
    conta = lambda k: sum(storage.chave_status(it.get("status")) == k for it in itens)
    resultado.update({
        "detailed_analysis": itens, "missing_concepts": [],
        "total_concepts": len(itens), "remembered": conta("ok"),
        "partially_remembered": conta("partial"), "incorrect": conta("incorrect"),
        "not_remembered": conta("missing"),
    })
    resultado["coverage_percentage"] = storage.cobertura(resultado)
    return resultado


# --- ESTATÍSTICAS ---

@app.get("/stats")
def stats():
    return storage.estatisticas()


@app.get("/history")
def history(discipline: str, topic: str | None = None):
    return storage.historico(discipline, topic)


@app.get("/attempt")
def attempt(id: str):
    t = storage.tentativa(id)
    if t is None:
        raise HTTPException(404, "Tentativa não encontrada")
    return t


# a fila "revisar hoje": o que a revisão espaçada marcou para hoje ou antes
@app.get("/review")
def review():
    return storage.revisar()
