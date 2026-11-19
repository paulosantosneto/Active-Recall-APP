"""Armazenamento em disco.

Layout:
    data/<disciplina>/<topico>.txt   -> a teoria de referência
    data/<disciplina>/_resumo/       -> rascunho do resumo ainda não analisado
    data/history.json                -> histórico de tentativas (com o texto de cada uma)

As teorias continuam sendo .txt legíveis e editáveis fora do app; só o
histórico é estruturado, porque é ele que alimenta a aba de estatísticas.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import unicodedata
import uuid
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

DATA = Path("data")
HISTORY = DATA / "history.json"
DISCIPLINA_PADRAO = "Geral"

# as imagens de um tópico ficam em data/<disciplina>/_img/<tópico>/; o nome
# começa com "_" para não colidir com um tópico e listar() só olha *.txt
IMAGENS = "_img"
ROTEIROS = "_roteiro"
RESUMOS = "_resumo"
EXTENSAO = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
LIMITE_IMAGEM = 8 * 1024 * 1024

_INVALIDO = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def nome_seguro(nome: str) -> str:
    """Impede que um nome vire travessia de diretório ou arquivo oculto."""
    limpo = _INVALIDO.sub("", (nome or "").strip()).strip(". ")
    if not limpo:
        raise ValueError("nome vazio ou inválido")
    return limpo


# ------------------------------------------------------------------ migração


def migrar_soltos() -> list[str]:
    """Move os .txt da raiz de data/ para data/Geral/ (layout antigo).

    Idempotente e não destrutivo: nunca sobrescreve um arquivo existente.
    """
    DATA.mkdir(parents=True, exist_ok=True)
    soltos = [p for p in DATA.glob("*.txt") if p.is_file()]
    if not soltos:
        return []

    destino = DATA / DISCIPLINA_PADRAO
    destino.mkdir(exist_ok=True)
    movidos = []
    for p in soltos:
        alvo = destino / p.name
        if alvo.exists():
            alvo = destino / f"{p.stem} (raiz){p.suffix}"
        p.rename(alvo)
        movidos.append(f"{p.name} -> {DISCIPLINA_PADRAO}/{alvo.name}")
    return movidos


# ----------------------------------------------------------------- teorias


def caminho(disciplina: str, topico: str) -> Path:
    return DATA / nome_seguro(disciplina) / f"{nome_seguro(topico)}.txt"


def listar() -> list[dict]:
    """Disciplinas (pastas) com seus tópicos (arquivos), em ordem alfabética."""
    DATA.mkdir(parents=True, exist_ok=True)
    saida = []
    for d in sorted((p for p in DATA.iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
        topicos = sorted((f.stem for f in d.glob("*.txt")), key=str.lower)
        saida.append({"name": d.name, "topics": topicos})
    return saida


def ler(disciplina: str, topico: str) -> str | None:
    p = caminho(disciplina, topico)
    return p.read_text(encoding="utf-8") if p.exists() else None


def gravar(disciplina: str, topico: str, conteudo: str) -> None:
    p = caminho(disciplina, topico)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(conteudo, encoding="utf-8")


def apagar_topico(disciplina: str, topico: str) -> int:
    """Remove o .txt e as tentativas do tópico. Devolve quantas foram apagadas."""
    p = caminho(disciplina, topico)
    if not p.is_file():
        raise FileNotFoundError(topico)
    p.unlink()
    shutil.rmtree(pasta_imagens(disciplina, topico), ignore_errors=True)
    caminho_roteiro(disciplina, topico).unlink(missing_ok=True)
    apagar_rascunho(disciplina, topico)
    return _remover_do_historico(
        lambda t: t.get("discipline") == disciplina and t.get("topic") == topico
    )


def apagar_disciplina(disciplina: str) -> tuple[int, int]:
    """Remove a pasta inteira. Devolve (tópicos apagados, tentativas apagadas)."""
    d = DATA / nome_seguro(disciplina)
    if not d.is_dir():
        raise FileNotFoundError(disciplina)

    # trava de segurança: só apaga em massa uma pasta que contenha apenas
    # tópicos (e a pasta de imagens deles, que é do próprio app)
    estranhos = [p.name for p in d.iterdir()
                 if not (p.is_file() and p.suffix == ".txt")
                 and not (p.is_dir() and p.name in (IMAGENS, ROTEIROS, RESUMOS))]
    if estranhos:
        raise ValueError(
            "a pasta tem itens que não são tópicos, apague manualmente: "
            + ", ".join(estranhos[:5])
        )

    topicos = len(list(d.glob("*.txt")))
    shutil.rmtree(d)
    return topicos, _remover_do_historico(lambda t: t.get("discipline") == disciplina)


# -------------------------------------------------------------- exportação

def exportar_zip(disciplina: str) -> bytes:
    """A disciplina num .zip: o texto de cada tópico e as imagens deles.

    Os caminhos repetem os de data/ (<disciplina>/<tópico>.txt e
    <disciplina>/_img/<tópico>/<arquivo>): descompactar dentro de data/
    devolve a disciplina ao app. Roteiros ficam de fora — são cache, o app
    refaz.
    """
    d = DATA / nome_seguro(disciplina)
    if not d.is_dir():
        raise FileNotFoundError(disciplina)
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for txt in sorted(d.glob("*.txt"), key=lambda f: f.name.lower()):
            z.write(txt, f"{d.name}/{txt.name}")
            for nome in listar_imagens(d.name, txt.stem):
                z.write(pasta_imagens(d.name, txt.stem) / nome,
                        f"{d.name}/{IMAGENS}/{txt.stem}/{nome}")
    return saida.getvalue()


LIMITE_ZIP = 300 * 1024 * 1024        # descompactado
LIMITE_TEXTO = 5 * 1024 * 1024


def _texto_do_zip(dados: bytes) -> str:
    try:
        return dados.decode("utf-8-sig")
    except UnicodeDecodeError:
        return dados.decode("cp1252", "replace")      # .txt salvo pelo Bloco de Notas antigo


def importar_zip(dados: bytes, padrao: str = DISCIPLINA_PADRAO, sobrescrever: bool = False) -> dict:
    """Traz tópicos (e as imagens deles) de um .zip para dentro de data/.

    Aceita o que exportar_zip() gera — <disciplina>/<tópico>.txt e
    <disciplina>/_img/<tópico>/<arquivo> — e também .txt soltos na raiz, que
    vão para a disciplina `padrao`. Nada é extraído pelo caminho que vem no
    zip: cada nome passa por nome_seguro() e o destino é montado aqui.

    Tópico que já existe com outro texto só é trocado com `sobrescrever`;
    sem isso ele volta em "conflicts" e fica como estava.
    """
    try:
        z = zipfile.ZipFile(io.BytesIO(dados))
    except zipfile.BadZipFile:
        raise ValueError("o arquivo não é um .zip válido")

    textos: dict[tuple[str, str], zipfile.ZipInfo] = {}
    imagens: dict[tuple[str, str], list[tuple[str, zipfile.ZipInfo]]] = {}
    ignorados, total = 0, 0
    for info in z.infolist():
        if info.is_dir():
            continue
        partes = [x for x in info.filename.replace("\\", "/").split("/") if x not in ("", ".")]
        if not partes or ".." in partes or "__MACOSX" in partes or partes[-1].startswith("."):
            continue
        total += info.file_size
        if total > LIMITE_ZIP:
            raise ValueError(f"zip grande demais (mais de {LIMITE_ZIP // (1024 * 1024)} MB descompactado)")
        try:
            if IMAGENS in partes[:-1]:
                # .../<disciplina>/_img/<tópico>/<arquivo>
                k = partes.index(IMAGENS)
                if len(partes) != k + 3 or Path(partes[-1]).suffix.lower() not in set(EXTENSAO.values()) \
                        or info.file_size > LIMITE_IMAGEM:
                    ignorados += 1
                    continue
                disc = nome_seguro(partes[k - 1] if k else padrao)
                imagens.setdefault((disc, nome_seguro(partes[k + 1])), []).append(
                    (nome_seguro(partes[-1]), info))
            elif partes[-1].lower().endswith(".txt") and not any(x.startswith("_") for x in partes[:-1]) \
                    and info.file_size <= LIMITE_TEXTO:
                disc = nome_seguro(partes[-2] if len(partes) > 1 else padrao)
                textos[(disc, nome_seguro(partes[-1][:-4]))] = info
            else:
                ignorados += 1
        except ValueError:
            ignorados += 1                      # nome que não sobra nada depois de limpo

    if not textos:
        raise ValueError("nenhum tópico (.txt) encontrado no zip")

    saida = {"imported": [], "replaced": [], "conflicts": [], "same": 0, "images": 0, "ignored": ignorados}
    for (disc, topico), info in sorted(textos.items()):
        novo = _texto_do_zip(z.read(info))
        if not novo.strip():
            saida["ignored"] += 1
            continue
        atual = ler(disc, topico)
        if atual is None:
            gravar(disc, topico, novo)
            saida["imported"].append([disc, topico])
        elif atual == novo:
            saida["same"] += 1
        elif sobrescrever:
            gravar(disc, topico, novo)
            saida["replaced"].append([disc, topico])
        else:
            saida["conflicts"].append([disc, topico])
            continue                            # em conflito: nem as imagens entram
        d = pasta_imagens(disc, topico)
        for nome, img in imagens.get((disc, topico), []):
            alvo = d / nome
            if not alvo.exists():
                d.mkdir(parents=True, exist_ok=True)
                alvo.write_bytes(z.read(img))
                saida["images"] += 1
    return saida


# ------------------------------------------------------------------- busca

def _dobrar(s: str) -> tuple[str, list[int]]:
    """Texto para comparar — minúsculas, sem acento, espaços colapsados — e, de
    cada caractere dele, a posição no original. Espelha o normalizar() do
    frontend: os dois precisam contar as ocorrências do mesmo jeito, porque a
    tela reencontra a n-ésima para selecioná-la."""
    out, mapa, espaco = [], [], True
    for i, ch in enumerate(s):
        if ch.isspace():
            if not espaco:
                out.append(" ")
                mapa.append(i)
                espaco = True
            continue
        c = "".join(x for x in unicodedata.normalize("NFD", ch) if not unicodedata.combining(x)).lower()
        if not c:
            continue
        out.append(c[0])
        mapa.append(i)
        espaco = False
    return "".join(out), mapa


def buscar(consulta: str, limite: int = 60, trechos: int = 3) -> dict:
    """Procura em todas as teorias e nos nomes de tópico, sem diferenciar
    maiúsculas nem acentos. Devolve, por tópico, quantas vezes aparece e os
    primeiros trechos, cada um com `n`: a posição dele entre as ocorrências."""
    alvo = _dobrar(consulta)[0].strip()
    if len(alvo) < 2:
        return {"query": consulta, "total": 0, "results": []}
    resultados, total = [], 0
    for d in listar():
        for topico in d["topics"]:
            texto = ler(d["name"], topico) or ""
            dobrado, mapa = _dobrar(texto)
            achados, i = [], dobrado.find(alvo)
            while i >= 0:
                achados.append(i)
                i = dobrado.find(alvo, i + len(alvo))
            no_nome = alvo in _dobrar(topico)[0]
            if not achados and not no_nome:
                continue
            total += len(achados)
            hits = []
            for n, i in enumerate(achados[:trechos]):
                a, b = mapa[i], mapa[i + len(alvo) - 1] + 1
                ini = max(texto.rfind("\n", 0, a) + 1, a - 28)
                fim = texto.find("\n", b)
                fim = min(len(texto) if fim < 0 else fim, b + 48)
                limpa = lambda t: re.sub(r"\s+", " ", t)
                hits.append({
                    "n": n,
                    "before": ("…" if ini > 0 and texto[ini - 1] != "\n" else "") + limpa(texto[ini:a]).lstrip(),
                    "match": limpa(texto[a:b]),
                    "after": limpa(texto[b:fim]).rstrip() + ("…" if fim < len(texto) and texto[fim] != "\n" else ""),
                })
            resultados.append({"discipline": d["name"], "topic": topico,
                               "count": len(achados), "in_name": no_nome, "hits": hits})
    # nome que bate vem antes; depois, quem tem mais ocorrências
    resultados.sort(key=lambda r: (not r["in_name"], -r["count"], r["topic"].lower()))
    return {"query": consulta, "total": total, "topics": len(resultados), "results": resultados[:limite]}


# ---------------------------------------------------------------- rascunho
#
# O resumo que está sendo escrito e ainda não foi analisado. Fica no disco
# para sobreviver a fechar a página; quando a análise roda, o texto passa a
# morar na tentativa (history.json) e o rascunho é apagado — da próxima vez o
# editor abre vazio, porque reler o resumo antigo antes de escrever o novo
# estraga o exercício de lembrar.

def caminho_rascunho(disciplina: str, topico: str) -> Path:
    return DATA / nome_seguro(disciplina) / RESUMOS / f"{nome_seguro(topico)}.json"


def ler_rascunho(disciplina: str, topico: str) -> dict | None:
    p = caminho_rascunho(disciplina, topico)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return d if isinstance(d, dict) and str(d.get("text") or "").strip() else None


def gravar_rascunho(disciplina: str, topico: str, texto: str, delta=None) -> None:
    """`delta` é o conteúdo do editor com a formatação (negrito, listas);
    `texto` é o mesmo conteúdo em texto puro. Vazio apaga o rascunho."""
    if not caminho(disciplina, topico).is_file():
        raise FileNotFoundError(topico)
    if not (texto or "").strip():
        return apagar_rascunho(disciplina, topico)
    p = caminho_rascunho(disciplina, topico)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "at": datetime.now().isoformat(timespec="seconds"), "text": texto, "delta": delta,
    }, ensure_ascii=False), encoding="utf-8")


def apagar_rascunho(disciplina: str, topico: str) -> None:
    caminho_rascunho(disciplina, topico).unlink(missing_ok=True)


# ----------------------------------------------------------------- imagens
#
# Um print colado não cabe no .txt: se fosse embutido em base64 iria inteiro
# para o prompt, dezenas de milhares de caracteres de ruído. Então o arquivo
# vai para o disco e a teoria guarda só um marcador curto, "[imagem: x.png]",
# que sem_imagens() remove antes de o texto chegar ao modelo.
#
# A garantia de que imagem nenhuma chega ao modelo não depende de quem chama:
# a limpeza é refeita em app/services, na única porta de saída para o modelo
# (run_knowledge_check e run_outline). O arquivo da imagem em si nunca é lido
# para montar o prompt — só o texto da teoria é.

LINHA_IMAGEM = re.compile(r"^[ \t]*\[imagem:[^\]\n]*\][ \t]*\n?", re.M | re.I)
MARCADOR = re.compile(r"[ \t]*\[imagem:[^\]\n]*\][ \t]*", re.I)
# Imagem ligada a um trecho: "[trecho](imagem:a.png)" ou, com mais de uma,
# "[trecho](imagem:a.png,b.png)". Para o modelo fica só o trecho, que é texto
# da teoria; a parte "(imagem:...)" some inteira.
LIGADA = re.compile(r"\[([^\[\]\n]+)\]\(\s*imagem:[^()\n]*\)", re.I)

# Imagem que entra por outro caminho, colada junto com texto de uma página ou
# de um Markdown: ![alt](endereço), <img ...> e a imagem inteira embutida como
# "data:image/...;base64,...". Nada disso é teoria para o modelo ler.
IMAGEM_EMBUTIDA = re.compile(
    r"!\[[^\]\n]*\]\([^)\n]*\)"
    r"|<img\b[^>]*>"
    r"|data:image/[^\s\"'<>)\]]+", re.I)


def sem_imagens(texto: str) -> str:
    """Tira toda referência a imagem, deixando só o que o modelo deve ler.

    Marcador sozinho na linha leva a linha junto; no meio de uma frase vira um
    espaço, senão as palavras vizinhas grudam e o trecho literal que o modelo
    copia para "reference" deixa de existir no texto original.
    """
    t = LIGADA.sub(r"\1", texto or "")
    t = LINHA_IMAGEM.sub("", t)
    t = MARCADOR.sub(" ", t)
    t = IMAGEM_EMBUTIDA.sub(" ", t)
    return re.sub(r"\n{3,}", "\n\n", t)


# Negrito, itálico e destaque são marcação para quem lê (**assim**, *assim* e
# ==assim==). Para o modelo são ruído: ele copia trechos literais da teoria
# ("reference", termos do roteiro) e uma marca no meio quebra essa
# correspondência. Fórmulas ($...$ e $$...$$) e código (`...`) ficam como
# estão: é assim que o modelo lê matemática e código, e um "*" dentro deles é
# conteúdo, não ênfase.
#
# A marca precisa estar solta de letras e números por fora: "2**3 e 4**5" e
# "a==b e c==d" são código, e tirar as marcas dali corromperia a teoria.
# As mesmas regras estão em static/index.html (RE_FORMULA e enfase()).

PROTEGIDO = re.compile(r"\$\$.+?\$\$|\$(?!\s)[^$\n]+?(?<!\s)\$(?!\d)|`[^`\n]+`", re.S)
NEGRITO = re.compile(r"(?<![\w*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?![\w*])")
ITALICO = re.compile(r"(?<![\w*])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
DESTAQUE = re.compile(r"(?<![\w=])==(?=\S)(.+?)(?<=\S)==(?![\w=])")


def sem_enfase(texto: str) -> str:
    """Tira as marcas de negrito, itálico e destaque, fora de fórmula e código."""
    def limpa(t: str) -> str:
        return DESTAQUE.sub(r"\1", ITALICO.sub(r"\1", NEGRITO.sub(r"\1", t)))

    texto = texto or ""
    partes, fim = [], 0
    for m in PROTEGIDO.finditer(texto):
        partes += [limpa(texto[fim:m.start()]), m.group(0)]
        fim = m.end()
    partes.append(limpa(texto[fim:]))
    return "".join(partes)


def para_modelo(texto: str) -> str:
    """A teoria como o modelo deve ler: sem imagens e sem ênfase."""
    return sem_enfase(sem_imagens(texto))


def pasta_imagens(disciplina: str, topico: str) -> Path:
    return DATA / nome_seguro(disciplina) / IMAGENS / nome_seguro(topico)


def caminho_imagem(disciplina: str, topico: str, nome: str) -> Path:
    return pasta_imagens(disciplina, topico) / nome_seguro(nome)


def salvar_imagem(disciplina: str, topico: str, dados: bytes, tipo: str) -> tuple[str, bool]:
    """Grava a imagem e devolve (nome do arquivo, já existia).

    O nome vem do conteúdo, então colar o mesmo print duas vezes reaproveita
    o arquivo em vez de duplicá-lo.
    """
    ext = EXTENSAO.get((tipo or "").split(";")[0].strip().lower())
    if ext is None:
        raise ValueError("formato não suportado: use PNG, JPEG, GIF ou WebP")
    if not dados:
        raise ValueError("imagem vazia")
    if len(dados) > LIMITE_IMAGEM:
        raise ValueError(f"imagem acima de {LIMITE_IMAGEM // (1024 * 1024)} MB")

    d = pasta_imagens(disciplina, topico)
    d.mkdir(parents=True, exist_ok=True)
    nome = hashlib.sha1(dados).hexdigest()[:12] + ext
    alvo = d / nome
    existia = alvo.exists()
    if not existia:
        alvo.write_bytes(dados)
    return nome, existia


def listar_imagens(disciplina: str, topico: str) -> list[str]:
    d = pasta_imagens(disciplina, topico)
    if not d.is_dir():
        return []
    return sorted((p.name for p in d.iterdir()
                   if p.is_file() and p.suffix.lower() in set(EXTENSAO.values())),
                  key=lambda n: (d / n).stat().st_mtime)


def apagar_imagem(disciplina: str, topico: str, nome: str) -> None:
    p = caminho_imagem(disciplina, topico, nome)
    if not p.is_file():
        raise FileNotFoundError(nome)
    p.unlink()


# ----------------------------------------------------------------- roteiro
#
# O roteiro do modo "com dicas" custa uma chamada ao modelo, então fica em
# cache por tópico. A assinatura é o hash da teoria: editar a teoria não
# invalida o roteiro na marra — ele continua servindo, marcado como
# desatualizado, porque quem estava usando a lista não pode perdê-la do nada.


def _achatar(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def fora_da_teoria(blocos, teoria) -> int:
    """Quantos termos NÃO aparecem na teoria com essas palavras.

    O termo deveria ser copiado da teoria. Quando o modelo traduz — devolve
    "Uncontrolled Redundancy" para uma teoria que diz "Redundância não
    controlada" — quase nada casa, e é assim que a interface percebe que o
    roteiro saiu no idioma errado, sem precisar adivinhar idioma.
    """
    alvo = _achatar(teoria)          # a teoria é longa: normaliza uma vez só
    return sum(1 for b in blocos or [] for t in b.get("terms") or []
               if _achatar(t) not in alvo)


def caminho_roteiro(disciplina: str, topico: str) -> Path:
    return DATA / nome_seguro(disciplina) / ROTEIROS / f"{nome_seguro(topico)}.json"


def _assinatura(teoria: str) -> str:
    # sobre o texto que o modelo realmente lê: acrescentar uma imagem, um
    # negrito ou uma linha em branco no fim não muda o conteúdo e não pode
    # invalidar o roteiro
    return hashlib.sha1(para_modelo(teoria).strip().encode("utf-8")).hexdigest()[:16]


def ler_roteiro(disciplina: str, topico: str, teoria: str | None) -> dict | None:
    p = caminho_roteiro(disciplina, topico)
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not d.get("blocks"):
        return None
    d["stale"] = teoria is not None and d.get("signature") != _assinatura(teoria)
    # roteiros gravados antes deste campo existir também merecem o aviso
    if "off" not in d and teoria is not None:
        d["off"] = fora_da_teoria(d["blocks"], teoria)
    return d


def gravar_roteiro(disciplina: str, topico: str, teoria: str, blocos: list[dict],
                   fora: int = 0) -> dict:
    d = {
        "signature": _assinatura(teoria),
        "at": datetime.now().isoformat(timespec="seconds"),
        # termos que não aparecem na teoria; muitos significam que o modelo
        # traduziu em vez de copiar
        "off": fora,
        "blocks": blocos,
    }
    p = caminho_roteiro(disciplina, topico)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return {**d, "stale": False}


# ---------------------------------------------------------------- histórico


def _tentativas() -> list[dict]:
    if not HISTORY.exists():
        return []
    try:
        dados = json.loads(HISTORY.read_text(encoding="utf-8"))
        return dados.get("attempts", [])
    except (json.JSONDecodeError, OSError):
        # histórico corrompido não pode derrubar o app; a análise vale mais
        return []


def _gravar_historico(tentativas: list[dict]) -> None:
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(
        json.dumps({"attempts": tentativas}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _remover_do_historico(casa) -> int:
    """Apaga as tentativas que satisfazem o predicado. Devolve quantas saíram."""
    todas = _tentativas()
    restantes = [t for t in todas if not casa(t)]
    removidas = len(todas) - len(restantes)
    if removidas:
        _gravar_historico(restantes)
    return removidas


def _num(resultado: dict, chave: str, tipo=float):
    try:
        return tipo(resultado.get(chave) or 0)
    except (TypeError, ValueError):
        return tipo(0)


def _conceitos(resultado: dict) -> list[dict]:
    # o detalhe por conceito é o que permite descobrir, depois, qual termo você
    # erra sempre; sem ele o histórico só tem números agregados
    conceitos = []
    for it in resultado.get("detailed_analysis") or []:
        if not isinstance(it, dict):
            continue
        nome = str(it.get("concept") or "").strip()
        if not nome:
            continue
        conceitos.append({
            "concept": nome[:200],
            "status": chave_status(it.get("status")),
            "explanation": str(it.get("explanation") or "").strip()[:240],
        })
    return conceitos


def cobertura(resultado: dict, conceitos: list[dict] | None = None) -> float:
    """Percentual de 0 a 100, com lembrado valendo 1 e parcial meio ponto.

    O modelo erra a escala de coverage_percentage com frequência (devolve 0.5
    quando queria dizer 50%). As contagens são confiáveis, então a cobertura
    é derivada delas sempre que fecharem com o total.
    """
    num = lambda chave, tipo=float: _num(resultado, chave, tipo)
    if conceitos is None:
        conceitos = _conceitos(resultado)
    total = num("total_concepts", int)
    partes = (num("remembered", int) + num("partially_remembered", int)
              + num("not_remembered", int) + num("incorrect", int))
    if total > 0 and partes == total:
        c = (num("remembered", int) + 0.5 * num("partially_remembered", int)) / total * 100
    elif conceitos:
        # contagens que não fecham: vale a lista de conceitos, que é o que a
        # tela mostra um a um
        pontos = sum({"ok": 1, "partial": 0.5}.get(c["status"], 0) for c in conceitos)
        c = pontos / len(conceitos) * 100
    else:
        c = num("coverage_percentage")
    return round(max(0.0, min(100.0, c)), 1)


LIMITE_RESUMO = 200_000


def registrar(disciplina: str, topico: str, resultado: dict, modo: str = "livre",
              texto: str = "") -> dict:
    num = lambda chave, tipo=float: _num(resultado, chave, tipo)
    conceitos = _conceitos(resultado)

    reg = {
        "id": uuid.uuid4().hex[:12],
        "discipline": disciplina,
        "topic": topico,
        "at": datetime.now().isoformat(timespec="seconds"),
        # com o roteiro à vista a tarefa é outra; sem isso gravado, uma nota
        # tirada com dicas some no meio das tiradas de memória limpa
        "mode": "dicas" if modo == "dicas" else "livre",
        "coverage": cobertura(resultado, conceitos),
        "remembered": num("remembered", int),
        "partially_remembered": num("partially_remembered", int),
        "not_remembered": num("not_remembered", int),
        "incorrect": num("incorrect", int),
        "total_concepts": num("total_concepts", int),
    }
    reg["concepts"] = conceitos
    # o resumo como foi escrito: é o que deixa reler uma tentativa antiga
    reg["text"] = (texto or "")[:LIMITE_RESUMO]

    todas = _tentativas()
    todas.append(reg)
    _gravar_historico(todas)
    return reg


def historico(disciplina: str, topico: str | None = None) -> dict:
    """Tentativas e desempenho acumulado por conceito.

    Sem `topico`, agrega a disciplina inteira. Tentativas gravadas antes desta
    versão não têm o detalhe por conceito e entram só na linha do tempo.
    """
    sel = [t for t in _tentativas()
           if t.get("discipline") == disciplina
           and (topico is None or t.get("topic") == topico)]
    sel.sort(key=lambda x: x.get("at", ""))

    grupos: dict[str, dict] = {}
    for t in sel:
        for c in t.get("concepts") or []:
            chave = _canonico(c.get("concept"))
            if not chave:
                continue
            g = grupos.setdefault(chave, {
                "name": c["concept"], "topic": t.get("topic"), "seen": 0,
                "ok": 0, "partial": 0, "incorrect": 0, "missing": 0,
                "history": [], "note": "",
            })
            st = c.get("status", "missing")
            g["seen"] += 1
            g[st] = g.get(st, 0) + 1
            g["history"].append({"at": t.get("at"), "status": st})
            g["name"] = c["concept"]          # fica com a grafia mais recente
            g["topic"] = t.get("topic")
            if st != "ok" and c.get("explanation"):
                g["note"] = c["explanation"]

    conceitos = []
    for g in grupos.values():
        g["missed"] = g["incorrect"] + g["missing"]
        g["rate"] = round(g["missed"] / g["seen"] * 100) if g["seen"] else 0
        g["last_status"] = g["history"][-1]["status"]
        conceitos.append(g)
    # o que mais dói primeiro: mais erros, depois maior taxa
    conceitos.sort(key=lambda g: (-g["missed"], -g["rate"], g["name"].lower()))

    return {
        "discipline": disciplina,
        "topic": topico,
        "attempts": [{
            "id": t.get("id"), "has_text": bool((t.get("text") or "").strip()),
            "at": t.get("at"), "topic": t.get("topic"), "coverage": t.get("coverage"),
            "mode": t.get("mode", "livre"),
            "remembered": t.get("remembered"), "partially_remembered": t.get("partially_remembered"),
            "incorrect": t.get("incorrect", 0), "not_remembered": t.get("not_remembered"),
            "total_concepts": t.get("total_concepts"),
            "detailed": bool(t.get("concepts")),
        } for t in sel],
        "concepts": conceitos,
    }


def tentativa(ident: str) -> dict | None:
    """Uma tentativa inteira, com o texto, e as vizinhas do mesmo tópico que
    também têm texto guardado (para ir de uma à outra)."""
    todas = _tentativas()
    t = next((x for x in todas if x.get("id") == ident), None)
    if t is None:
        return None
    irmas = sorted((x for x in todas
                    if x.get("discipline") == t.get("discipline") and x.get("topic") == t.get("topic")
                    and (x.get("text") or "").strip()), key=lambda x: x.get("at", ""))
    ids = [x.get("id") for x in irmas]
    i = ids.index(ident) if ident in ids else -1
    return {**t, "mode": t.get("mode", "livre"),
            "prev": ids[i - 1] if i > 0 else None,
            "next": ids[i + 1] if 0 <= i < len(ids) - 1 else None}


# ------------------------------------------------------- revisão espaçada
#
# Cada tópico sobe uma escada de intervalos: 1, 3, 7, 14, 30 e 60 dias.
#   - foi bem (>= BOM) numa revisão feita no dia marcado ou depois: sobe um degrau
#   - foi mal (< RUIM): volta para o primeiro
#   - no meio, ou revisou antes da hora, ou fez com dicas: fica no mesmo degrau
# A próxima revisão é o dia da última tentativa mais o intervalo do degrau.
# De cada dia vale só a última tentativa: repetir dez vezes na mesma tarde não
# é dez revisões espaçadas. BOM e RUIM são as mesmas faixas de cor da tela.

DEGRAUS = [1, 3, 7, 14, 30, 60]
BOM, RUIM = 70, 40


def _agendar(tentativas: list[dict]) -> tuple[date, int] | None:
    """Tentativas de um tópico → (dia da próxima revisão, intervalo em dias)."""
    dias: dict[date, dict] = {}
    for t in sorted(tentativas, key=lambda x: x.get("at", "")):
        try:
            dias[datetime.fromisoformat(t["at"]).date()] = t
        except (KeyError, TypeError, ValueError):
            continue
    if not dias:
        return None
    nivel, vence = 0, None
    for dia in sorted(dias):
        t = dias[dia]
        nota = t.get("coverage") or 0
        if nota < RUIM:
            nivel = 0
        elif nota >= BOM and t.get("mode") != "dicas" and vence is not None and dia >= vence:
            nivel = min(nivel + 1, len(DEGRAUS) - 1)
        vence = dia + timedelta(days=DEGRAUS[nivel])
    return vence, DEGRAUS[nivel]


def revisar() -> dict:
    """A fila do dia: tópicos com revisão para hoje ou atrasada, do mais
    atrasado para o menos. `next` diz em quantos dias vence o próximo."""
    fila, proximo = [], None
    for d in estatisticas()["disciplines"]:
        for t in d["topics"]:
            if t["days_left"] is None:
                continue
            if t["days_left"] <= 0:
                fila.append({"discipline": d["name"], "topic": t["name"], "days_left": t["days_left"],
                             "last": t["last"], "last_at": t["last_at"]})
            elif proximo is None or t["days_left"] < proximo:
                proximo = t["days_left"]
    fila.sort(key=lambda x: (x["days_left"], x["last"] if x["last"] is not None else 0))
    return {"due": fila, "next": proximo}


def chave_status(valor) -> str:
    """Espelha o statusKey() do frontend. A ordem dos testes importa:
    'not_remembered' contém 'remembered', e 'incorrect' não contém 'not'."""
    t = str(valor or "").lower()
    if "incorrect" in t or "incorret" in t or "errad" in t:
        return "incorrect"
    if "partial" in t or "parcial" in t:
        return "partial"
    if "not" in t or "não" in t or "nao" in t:
        return "missing"
    if "remember" in t or "lembr" in t:
        return "ok"
    return "missing"


def _canonico(nome: str) -> str:
    """Chave de agrupamento: o modelo varia a grafia do mesmo conceito entre
    tentativas ('Propriedades ACID' e 'propriedades acid')."""
    base = unicodedata.normalize("NFKD", str(nome or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", "", base.lower()).strip()


def _media(valores: list[float]) -> float | None:
    return round(sum(valores) / len(valores), 1) if valores else None


def estatisticas() -> dict:
    """Desempenho por tópico e o agregado da disciplina.

    A linha da disciplina resume seus tópicos praticados: as colunas de
    percentual são médias entre tópicos (não entre tentativas), para que uma
    matéria com muitos tópicos fracos não seja mascarada por um tópico
    praticado à exaustão.
    """
    por_topico: dict[tuple[str, str], list[dict]] = {}
    for t in _tentativas():
        por_topico.setdefault((t.get("discipline"), t.get("topic")), []).append(t)

    disciplinas = []
    for d in listar():
        topicos = []
        for nome in d["topics"]:
            ts = sorted(por_topico.get((d["name"], nome), []), key=lambda x: x["at"])
            cob = [t["coverage"] for t in ts]
            agenda = _agendar(ts)
            topicos.append({
                "name": nome,
                "attempts": len(ts),
                "first": cob[0] if cob else None,
                "last": cob[-1] if cob else None,
                "best": max(cob) if cob else None,
                "avg": _media(cob),
                "trend": cob[-12:],
                "last_at": ts[-1]["at"] if ts else None,
                # revisão espaçada: quando volta e quantos dias faltam (<= 0: é hoje ou passou)
                "due": agenda[0].isoformat() if agenda else None,
                "days_left": (agenda[0] - date.today()).days if agenda else None,
                "interval": agenda[1] if agenda else None,
            })
        pr = [t for t in topicos if t["attempts"]]
        disciplinas.append({
            "name": d["name"],
            "topics": topicos,
            "practiced": len(pr),
            "attempts": sum(t["attempts"] for t in topicos),
            "first": _media([t["first"] for t in pr]),
            "last": _media([t["last"] for t in pr]),
            "best": max([t["best"] for t in pr]) if pr else None,
            "avg": _media([t["avg"] for t in pr]),
            # a disciplina é tão recente quanto o tópico praticado mais recentemente
            "last_at": max([t["last_at"] for t in pr]) if pr else None,
        })
    return {"disciplines": disciplinas}
