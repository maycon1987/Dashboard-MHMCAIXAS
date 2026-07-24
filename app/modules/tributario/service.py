
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import HTTPException


TINY_TOKEN = os.getenv("TINY_TOKEN", "").strip()
TINY_API_KEY_MINAS = os.getenv("TINY_API_KEY_MINAS", "").strip()
TINY_BASE_URL = "https://api.tiny.com.br/api2"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
)

TABELA_PRODUTOS = "produtos_tributarios"


def safe_str(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def dinheiro_para_float(valor: Any) -> float:
    if valor is None:
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).replace("R$", "").replace(" ", "").strip()
    if not texto:
        return 0.0
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    try:
        return float(texto)
    except (TypeError, ValueError):
        return 0.0


def normalizar_filial(filial: str) -> str:
    filial = safe_str(filial).lower()
    if filial in {"mg", "minas", "pouso_alegre", "pouso-alegre"}:
        return "mg"
    if filial in {"all", "todas", "todos", "consolidado"}:
        return "all"
    return "sp"


def validar_configuracao() -> None:
    faltando: List[str] = []
    if not SUPABASE_URL:
        faltando.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_ROLE_KEY:
        faltando.append("SUPABASE_SERVICE_ROLE_KEY ou SUPABASE_SERVICE_KEY")
    if faltando:
        raise HTTPException(
            status_code=500,
            detail={"erro": "Variáveis de ambiente ausentes.", "faltando": faltando},
        )


def obter_token_tiny(filial: str) -> str:
    filial = normalizar_filial(filial)
    token = TINY_API_KEY_MINAS if filial == "mg" else TINY_TOKEN
    if not token:
        nome = "TINY_API_KEY_MINAS" if filial == "mg" else "TINY_TOKEN"
        raise HTTPException(status_code=500, detail=f"{nome} não configurado.")
    return token


def supabase_headers(prefer: Optional[str] = None) -> Dict[str, str]:
    validar_configuracao()
    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    return headers


def supabase_get(params: Any) -> List[Dict[str, Any]]:
    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABELA_PRODUTOS}",
        headers=supabase_headers(),
        params=params,
        timeout=90,
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": f"Erro ao consultar {TABELA_PRODUTOS}.",
                "status_code": response.status_code,
                "resposta": response.text,
            },
        )
    return response.json()


def supabase_upsert(payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not payload:
        return []
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{TABELA_PRODUTOS}",
        headers=supabase_headers("resolution=merge-duplicates,return=representation"),
        params={"on_conflict": "filial,tiny_produto_id"},
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": f"Erro ao salvar em {TABELA_PRODUTOS}.",
                "status_code": response.status_code,
                "resposta": response.text,
            },
        )
    return response.json()


def supabase_patch(produto_id: str, dados: Dict[str, Any]) -> List[Dict[str, Any]]:
    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{TABELA_PRODUTOS}",
        headers=supabase_headers("return=representation"),
        params={"id": f"eq.{produto_id}"},
        json=dados,
        timeout=90,
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro ao atualizar produto tributário.",
                "status_code": response.status_code,
                "resposta": response.text,
            },
        )
    return response.json()


def tiny_get(endpoint: str, params: Dict[str, Any], filial: str) -> Dict[str, Any]:
    parametros = {
        "token": obter_token_tiny(filial),
        "formato": "json",
        **params,
    }
    response = requests.get(
        f"{TINY_BASE_URL}/{endpoint}", params=parametros, timeout=90
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail={
                "erro": "Erro HTTP ao consultar o Tiny.",
                "status_code": response.status_code,
                "resposta": response.text,
            },
        )
    try:
        dados = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail={"erro": "Tiny não retornou JSON válido.", "resposta": response.text},
        )

    retorno = dados.get("retorno", {})
    if safe_str(retorno.get("status")).upper() == "ERRO":
        codigo = safe_str(retorno.get("codigo_erro"))
        erros = retorno.get("erros") or []
        texto = " ".join(
            safe_str(item.get("erro") if isinstance(item, dict) else item)
            for item in erros
        ).lower()
        if codigo == "20" or "não retornou registros" in texto or "nao retornou registros" in texto:
            return {"retorno": {"status": "OK", "numero_paginas": 1, "produtos": []}}
        raise HTTPException(
            status_code=502,
            detail={"erro": "Tiny retornou erro.", "retorno": retorno},
        )
    return dados


def buscar_pagina_produtos_tiny(pagina: int, filial: str) -> Dict[str, Any]:
    return tiny_get(
        "produtos.pesquisa.php",
        {"pagina": pagina, "situacao": "A"},
        filial,
    )


def obter_produto_tiny(produto_id: str, filial: str) -> Dict[str, Any]:
    dados = tiny_get("produto.obter.php", {"id": produto_id}, filial)
    return dados.get("retorno", {}).get("produto", {}) or {}


def extrair_produtos_pagina(resposta: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], int]:
    retorno = resposta.get("retorno", {})
    produtos = []
    for item in retorno.get("produtos", []) or []:
        produtos.append(item.get("produto", item) if isinstance(item, dict) else {})
    total_paginas = int(retorno.get("numero_paginas", 1) or 1)
    return produtos, total_paginas


def ncm_limpo(valor: Any) -> str:
    return "".join(caractere for caractere in safe_str(valor) if caractere.isdigit())


def status_inicial_por_ncm(ncm: str) -> Tuple[str, str]:
    if not ncm:
        return "sem_ncm", "PENDENTE"
    if len(ncm) != 8:
        return "ncm_invalido", "REVISAO_MANUAL"
    return "pendente", "PENDENTE"


def normalizar_produto(
    produto_pesquisa: Dict[str, Any],
    filial: str,
    lote: int,
    produto_detalhado: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    detalhe = produto_detalhado or {}
    produto_id = safe_str(detalhe.get("id") or produto_pesquisa.get("id"))
    codigo = safe_str(detalhe.get("codigo") or produto_pesquisa.get("codigo"))
    descricao = safe_str(
        detalhe.get("nome")
        or detalhe.get("descricao")
        or produto_pesquisa.get("nome")
        or produto_pesquisa.get("descricao")
    )
    ncm = ncm_limpo(detalhe.get("ncm") or produto_pesquisa.get("ncm"))
    cest = safe_str(detalhe.get("cest") or produto_pesquisa.get("cest"))
    status_tributario, status_ia = status_inicial_por_ncm(ncm)

    fornecedor = detalhe.get("fornecedor") or {}
    if isinstance(fornecedor, dict):
        nome_fornecedor = safe_str(
            fornecedor.get("nome") or fornecedor.get("nome_fantasia")
        )
    else:
        nome_fornecedor = safe_str(fornecedor)

    agora = datetime.now().isoformat()
    return {
        "filial": normalizar_filial(filial),
        "tiny_produto_id": produto_id,
        "codigo": codigo,
        "sku": codigo,
        "descricao": descricao,
        "ncm": ncm or None,
        "cest": cest or None,
        "origem_mercadoria": safe_str(
            detalhe.get("origem") or detalhe.get("origem_mercadoria")
        ) or None,
        "unidade": safe_str(
            detalhe.get("unidade") or produto_pesquisa.get("unidade")
        ) or None,
        "preco_custo": dinheiro_para_float(
            detalhe.get("preco_custo") or detalhe.get("preco_custo_medio")
        ),
        "preco_venda": dinheiro_para_float(
            detalhe.get("preco") or produto_pesquisa.get("preco")
        ),
        "fornecedor": nome_fornecedor or None,
        "ativo": True,
        "status_tributario": status_tributario,
        "status_ia": status_ia,
        "lote": lote,
        "ultima_sincronizacao": agora,
        "updated_at": agora,
    }


def sincronizar_produtos(
    filial: str,
    tamanho_lote: int = 50,
    detalhar: bool = True,
    limite_detalhes: int = 50,
    max_paginas: Optional[int] = None,
) -> Dict[str, Any]:
    filial = normalizar_filial(filial)
    if filial == "all":
        raise HTTPException(status_code=400, detail="Use filial=sp ou filial=mg.")
    if tamanho_lote < 1 or tamanho_lote > 500:
        raise HTTPException(status_code=400, detail="tamanho_lote deve estar entre 1 e 500.")

    pagina = 1
    indice_global = 0
    detalhes_executados = 0
    produtos_recebidos = 0
    produtos_salvos = 0
    sem_ncm = 0
    ncm_invalido = 0
    erros_detalhe: List[Dict[str, str]] = []

    while True:
        resposta = buscar_pagina_produtos_tiny(pagina, filial)
        produtos_pagina, total_paginas = extrair_produtos_pagina(resposta)
        produtos_recebidos += len(produtos_pagina)
        payload: List[Dict[str, Any]] = []

        for produto in produtos_pagina:
            indice_global += 1
            lote = ((indice_global - 1) // tamanho_lote) + 1
            detalhe: Optional[Dict[str, Any]] = None
            produto_id = safe_str(produto.get("id"))

            precisa_detalhar = detalhar and produto_id and detalhes_executados < limite_detalhes
            if precisa_detalhar:
                try:
                    detalhe = obter_produto_tiny(produto_id, filial)
                    detalhes_executados += 1
                    time.sleep(0.55)
                except Exception as exc:
                    erros_detalhe.append({"tiny_produto_id": produto_id, "erro": str(exc)[:300]})

            normalizado = normalizar_produto(produto, filial, lote, detalhe)
            if not normalizado["tiny_produto_id"]:
                continue
            if normalizado["status_tributario"] == "sem_ncm":
                sem_ncm += 1
            elif normalizado["status_tributario"] == "ncm_invalido":
                ncm_invalido += 1
            payload.append(normalizado)

        for inicio in range(0, len(payload), 100):
            lote_payload = payload[inicio : inicio + 100]
            salvos = supabase_upsert(lote_payload)
            produtos_salvos += len(salvos)

        if pagina >= total_paginas:
            break
        if max_paginas and pagina >= max_paginas:
            break
        pagina += 1
        time.sleep(0.6)

    total_lotes = ((indice_global - 1) // tamanho_lote) + 1 if indice_global else 0
    return {
        "status": "ok",
        "filial": filial,
        "produtos_recebidos": produtos_recebidos,
        "produtos_salvos": produtos_salvos,
        "detalhes_consultados": detalhes_executados,
        "produtos_sem_ncm_nesta_execucao": sem_ncm,
        "produtos_com_ncm_invalido_nesta_execucao": ncm_invalido,
        "total_lotes_nesta_execucao": total_lotes,
        "tamanho_lote": tamanho_lote,
        "paginas_processadas": pagina,
        "erros_detalhe_amostra": erros_detalhe[:10],
        "observacao": (
            "A pesquisa básica foi importada. O parâmetro limite_detalhes controla quantos "
            "produtos recebem consulta completa ao Tiny nesta execução."
        ),
    }


def listar_produtos(
    filial: str,
    status_tributario: Optional[str],
    status_ia: Optional[str],
    lote: Optional[int],
    busca: Optional[str],
    limite: int,
    offset: int,
) -> List[Dict[str, Any]]:
    params: List[Tuple[str, str]] = [
        ("select", "*"),
        ("order", "lote.asc,descricao.asc"),
        ("limit", str(limite)),
        ("offset", str(offset)),
    ]
    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params.append(("filial", f"eq.{filial_normalizada}"))
    if status_tributario:
        params.append(("status_tributario", f"eq.{status_tributario}"))
    if status_ia:
        params.append(("status_ia", f"eq.{status_ia}"))
    if lote is not None:
        params.append(("lote", f"eq.{lote}"))
    if busca:
        termo = busca.replace(",", " ").strip()
        params.append(("or", f"(descricao.ilike.*{termo}*,codigo.ilike.*{termo}*,sku.ilike.*{termo}*,ncm.ilike.*{termo}*)"))
    return supabase_get(params)


def obter_resumo(filial: str) -> Dict[str, Any]:
    params: List[Tuple[str, str]] = [
        ("select", "id,ncm,lote,status_tributario,status_ia"),
        ("limit", "10000"),
    ]
    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params.append(("filial", f"eq.{filial_normalizada}"))
    registros = supabase_get(params)

    status_tributario: Dict[str, int] = {}
    status_ia: Dict[str, int] = {}
    lotes = set()
    com_ncm = 0
    for registro in registros:
        st = safe_str(registro.get("status_tributario")) or "sem_status"
        sia = safe_str(registro.get("status_ia")) or "sem_status"
        status_tributario[st] = status_tributario.get(st, 0) + 1
        status_ia[sia] = status_ia.get(sia, 0) + 1
        if registro.get("ncm"):
            com_ncm += 1
        if registro.get("lote") is not None:
            lotes.add(registro.get("lote"))

    total = len(registros)
    return {
        "status": "ok",
        "filial": filial_normalizada,
        "total_produtos": total,
        "produtos_com_ncm": com_ncm,
        "produtos_sem_ncm": total - com_ncm,
        "total_lotes": len(lotes),
        "por_status_tributario": status_tributario,
        "por_status_ia": status_ia,
    }


def listar_lotes(filial: str) -> List[Dict[str, Any]]:
    registros = listar_produtos(filial, None, None, None, None, 10000, 0)
    agrupado: Dict[int, Dict[str, Any]] = {}
    for registro in registros:
        numero = int(registro.get("lote") or 0)
        if numero <= 0:
            continue
        item = agrupado.setdefault(
            numero,
            {
                "lote": numero,
                "total_produtos": 0,
                "pendentes": 0,
                "processando": 0,
                "concluidos": 0,
                "erros": 0,
                "revisao_manual": 0,
                "sem_ncm": 0,
            },
        )
        item["total_produtos"] += 1
        status_ia = safe_str(registro.get("status_ia")).upper()
        status_tributario = safe_str(registro.get("status_tributario")).lower()
        if status_ia == "PENDENTE":
            item["pendentes"] += 1
        elif status_ia == "PROCESSANDO":
            item["processando"] += 1
        elif status_ia == "CONCLUIDO":
            item["concluidos"] += 1
        elif status_ia == "ERRO":
            item["erros"] += 1
        elif status_ia == "REVISAO_MANUAL":
            item["revisao_manual"] += 1
        if status_tributario == "sem_ncm":
            item["sem_ncm"] += 1

    resultado = list(agrupado.values())
    for item in resultado:
        if item["processando"]:
            item["status"] = "PROCESSANDO"
        elif item["pendentes"]:
            item["status"] = "PENDENTE"
        elif item["erros"] or item["revisao_manual"]:
            item["status"] = "REVISAO"
        else:
            item["status"] = "CONCLUIDO"
    return sorted(resultado, key=lambda item: item["lote"])
