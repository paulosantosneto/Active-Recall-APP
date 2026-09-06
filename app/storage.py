"""Armazenamento em disco.

Layout:
    data/<disciplina>/<topico>.txt   -> a teoria de referência
    data/history.json                -> histórico de tentativas

As teorias continuam sendo .txt legíveis e editáveis fora do app; só o
histórico é estruturado, porque é ele que alimenta a aba de estatísticas.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

DATA = Path("data")
HISTORY = DATA / "history.json"
DISCIPLINA_PADRAO = "Geral"

# as imagens de um tópico ficam em data/<disciplina>/_img/<tópico>/; o nome
# começa com "_" para não colidir com um tópico e listar() só olha *.txt
IMAGENS = "_img"
ROTEIROS = "_roteiro"
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
                 and not (p.is_dir() and p.name in (IMAGENS, ROTEIROS))]
    if estranhos:
        raise ValueError(
            "a pasta tem itens que não são tópicos, apague manualmente: "
            + ", ".join(estranhos[:5])
        )

    topicos = len(list(d.glob("*.txt")))
    shutil.rmtree(d)
    return topicos, _remover_do_historico(lambda t: t.get("discipline") == disciplina)


# ----------------------------------------------------------------- imagens
#
# Um print colado não cabe no .txt: se fosse embutido em base64 iria inteiro
# para o prompt, dezenas de milhares de caracteres de ruído. Então o arquivo
# vai para o disco e a teoria guarda só um marcador curto, "[imagem: x.png]",
# que sem_imagens() remove antes de o texto chegar ao modelo.

LINHA_IMAGEM = re.compile(r"^[ \t]*\[imagem:[^\]\n]*\][ \t]*\n?", re.M)
MARCADOR = re.compile(r"[ \t]*\[imagem:[^\]\n]*\][ \t]*")


def sem_imagens(texto: str) -> str:
    """Tira os marcadores de imagem, deixando só o que o modelo deve ler.

    Marcador sozinho na linha leva a linha junto; no meio de uma frase vira um
    espaço, senão as palavras vizinhas grudam e o trecho literal que o modelo
    copia para "reference" deixa de existir no texto original.
    """
    t = LINHA_IMAGEM.sub("", texto or "")
    t = MARCADOR.sub(" ", t)
    return re.sub(r"\n{3,}", "\n\n", t)


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
    # sobre o texto que o modelo realmente lê: acrescentar uma imagem ou uma
    # linha em branco no fim não muda o conteúdo e não pode invalidar o roteiro
    return hashlib.sha1(sem_imagens(teoria).strip().encode("utf-8")).hexdigest()[:16]


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


def registrar(disciplina: str, topico: str, resultado: dict, modo: str = "livre") -> dict:
    def num(chave, tipo=float):
        try:
            return tipo(resultado.get(chave) or 0)
        except (TypeError, ValueError):
            return tipo(0)

    # O modelo erra a escala de coverage_percentage com frequência (devolve 0.5
    # quando queria dizer 50%). As contagens são confiáveis, então a cobertura
    # é derivada delas sempre que fecharem com o total; parcial vale meio ponto.
    total = num("total_concepts", int)
    partes = (num("remembered", int) + num("partially_remembered", int)
              + num("not_remembered", int) + num("incorrect", int))
    if total > 0 and partes == total:
        cobertura = (num("remembered", int) + 0.5 * num("partially_remembered", int)) / total * 100
    else:
        cobertura = num("coverage_percentage")

    reg = {
        "id": uuid.uuid4().hex[:12],
        "discipline": disciplina,
        "topic": topico,
        "at": datetime.now().isoformat(timespec="seconds"),
        # com o roteiro à vista a tarefa é outra; sem isso gravado, uma nota
        # tirada com dicas some no meio das tiradas de memória limpa
        "mode": "dicas" if modo == "dicas" else "livre",
        "coverage": round(max(0.0, min(100.0, cobertura)), 1),
        "remembered": num("remembered", int),
        "partially_remembered": num("partially_remembered", int),
        "not_remembered": num("not_remembered", int),
        "incorrect": num("incorrect", int),
        "total_concepts": num("total_concepts", int),
    }
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
    reg["concepts"] = conceitos

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
            "at": t.get("at"), "topic": t.get("topic"), "coverage": t.get("coverage"),
            "mode": t.get("mode", "livre"),
            "remembered": t.get("remembered"), "partially_remembered": t.get("partially_remembered"),
            "incorrect": t.get("incorrect", 0), "not_remembered": t.get("not_remembered"),
            "total_concepts": t.get("total_concepts"),
            "detailed": bool(t.get("concepts")),
        } for t in sel],
        "concepts": conceitos,
    }


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
            topicos.append({
                "name": nome,
                "attempts": len(ts),
                "first": cob[0] if cob else None,
                "last": cob[-1] if cob else None,
                "best": max(cob) if cob else None,
                "avg": _media(cob),
                "trend": cob[-12:],
                "last_at": ts[-1]["at"] if ts else None,
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
