import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from fastapi import HTTPException


# ============================================================
# CONFIGURAÇÕES
# ============================================================

# Campinas / São Paulo
TINY_TOKEN = os.getenv("TINY_TOKEN", "").strip()

# Pouso Alegre / Minas Gerais
TINY_API_KEY_MINAS = os.getenv("TINY_API_KEY_MINAS", "").strip()

TINY_BASE_URL = "https://api.tiny.com.br/api2"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
)

TABELA_PRODUTOS = "produtos_tributarios"

# Colunas que realmente existem na tabela produtos_tributarios.
COLUNAS_PRODUTOS_TRIBUTARIOS = {
    "id",
    "filial",
    "tiny_produto_id",
    "codigo",
    "sku",
    "descricao",
    "ncm",
    "cest",
    "origem_mercadoria",
    "unidade",
    "preco",
    "custo",
    "fornecedor",
    "ativo",
    "ultima_sincronizacao",
    "created_at",
    "updated_at",
    "status_ia",
    "percentual_confianca",
    "justificativa_ia",
    "data_analise",
    "lote",
}

# Campos que podem ser alterados pelo endpoint PATCH.
# id, filial, tiny_produto_id e created_at não devem ser modificados manualmente.
COLUNAS_EDITAVEIS = {
    "codigo",
    "sku",
    "descricao",
    "ncm",
    "cest",
    "origem_mercadoria",
    "unidade",
    "preco",
    "custo",
    "fornecedor",
    "ativo",
    "status_ia",
    "percentual_confianca",
    "justificativa_ia",
    "data_analise",
    "lote",
}


# ============================================================
# FUNÇÕES BÁSICAS
# ============================================================

def agora_iso() -> str:
    """Retorna data/hora UTC em ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def safe_str(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def dinheiro_para_float(valor: Any) -> float:
    """
    Converte números e valores monetários brasileiros para float.

    Exemplos:
    10 -> 10.0
    "10,50" -> 10.5
    "R$ 1.234,56" -> 1234.56
    """
    if valor is None:
        return 0.0

    if isinstance(valor, bool):
        return float(valor)

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = (
        str(valor)
        .replace("R$", "")
        .replace("\u00a0", "")
        .replace(" ", "")
        .strip()
    )

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
    """
    Normaliza o código da filial.

    sp  = Campinas
    mg  = Pouso Alegre
    all = consolidado, apenas para consultas
    """
    valor = safe_str(filial).lower()

    if valor in {"mg", "minas", "pouso_alegre", "pouso-alegre", "pouso alegre"}:
        return "mg"

    if valor in {"all", "todas", "todos", "consolidado"}:
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
            detail={
                "erro": "Variáveis de ambiente ausentes.",
                "faltando": faltando,
            },
        )


def obter_token_tiny(filial: str) -> str:
    """
    Seleciona o Tiny correto sem misturar as filiais.

    filial=sp -> TINY_TOKEN
    filial=mg -> TINY_API_KEY_MINAS
    """
    filial_normalizada = normalizar_filial(filial)

    if filial_normalizada == "all":
        raise HTTPException(
            status_code=400,
            detail="Para sincronizar, use filial=sp ou filial=mg.",
        )

    if filial_normalizada == "mg":
        token = TINY_API_KEY_MINAS
        nome_variavel = "TINY_API_KEY_MINAS"
    else:
        token = TINY_TOKEN
        nome_variavel = "TINY_TOKEN"

    if not token:
        raise HTTPException(
            status_code=500,
            detail=f"{nome_variavel} não configurado.",
        )

    return token


def ncm_limpo(valor: Any) -> str:
    """Mantém apenas os números do NCM."""
    return "".join(caractere for caractere in safe_str(valor) if caractere.isdigit())


def status_ncm(ncm: Any) -> str:
    """
    Status tributário calculado em memória.

    Este status NÃO é salvo porque a tabela atual não possui
    a coluna status_tributario.
    """
    valor = ncm_limpo(ncm)

    if not valor:
        return "sem_ncm"

    if len(valor) != 8:
        return "ncm_invalido"

    return "pendente"


def status_ia_inicial(ncm: Any) -> str:
    valor = ncm_limpo(ncm)

    if not valor:
        return "PENDENTE"

    if len(valor) != 8:
        return "REVISAO_MANUAL"

    return "PENDENTE"


def adicionar_status_tributario_virtual(
    registro: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Acrescenta status_tributario somente na resposta da API.

    Assim o frontend continua recebendo o campo, mas o Supabase
    não recebe uma coluna inexistente.
    """
    resultado = dict(registro)
    resultado["status_tributario"] = status_ncm(resultado.get("ncm"))
    return resultado


# ============================================================
# SUPABASE
# ============================================================

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

    try:
        dados = response.json()
    except ValueError:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Supabase não retornou JSON válido.",
                "resposta": response.text,
            },
        )

    return dados if isinstance(dados, list) else []


def limpar_payload_banco(dados: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remove campos que não existem na tabela.

    Também converte nomes antigos para os nomes reais:
    preco_venda -> preco
    preco_custo -> custo
    """
    origem = dict(dados)

    if "preco" not in origem and "preco_venda" in origem:
        origem["preco"] = origem.get("preco_venda")

    if "custo" not in origem and "preco_custo" in origem:
        origem["custo"] = origem.get("preco_custo")

    origem.pop("preco_venda", None)
    origem.pop("preco_custo", None)
    origem.pop("status_tributario", None)

    return {
        chave: valor
        for chave, valor in origem.items()
        if chave in COLUNAS_PRODUTOS_TRIBUTARIOS
    }


def supabase_upsert(payload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not payload:
        return []

    payload_limpo = [limpar_payload_banco(item) for item in payload]

    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{TABELA_PRODUTOS}",
        headers=supabase_headers(
            "resolution=merge-duplicates,return=representation"
        ),
        params={"on_conflict": "filial,tiny_produto_id"},
        json=payload_limpo,
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

    try:
        dados = response.json()
    except ValueError:
        return []

    return dados if isinstance(dados, list) else []


def supabase_patch(
    produto_id: str,
    dados: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Atualiza um produto sem permitir o envio de colunas inexistentes.
    """
    dados_normalizados = limpar_payload_banco(dados)

    dados_editaveis = {
        chave: valor
        for chave, valor in dados_normalizados.items()
        if chave in COLUNAS_EDITAVEIS
    }

    if not dados_editaveis:
        raise HTTPException(
            status_code=400,
            detail="Nenhum campo válido foi enviado para atualização.",
        )

    # Se o NCM foi alterado e o status da IA não veio no corpo,
    # recalcula o estado inicial.
    if "ncm" in dados_editaveis:
        ncm = ncm_limpo(dados_editaveis.get("ncm"))
        dados_editaveis["ncm"] = ncm or None

        if "status_ia" not in dados_editaveis:
            dados_editaveis["status_ia"] = status_ia_inicial(ncm)

    dados_editaveis["updated_at"] = agora_iso()

    response = requests.patch(
        f"{SUPABASE_URL}/rest/v1/{TABELA_PRODUTOS}",
        headers=supabase_headers("return=representation"),
        params={"id": f"eq.{produto_id}"},
        json=dados_editaveis,
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

    try:
        registros = response.json()
    except ValueError:
        registros = []

    return [
        adicionar_status_tributario_virtual(registro)
        for registro in registros
    ]


# ============================================================
# TINY
# ============================================================

def tiny_get(
    endpoint: str,
    params: Dict[str, Any],
    filial: str,
) -> Dict[str, Any]:
    parametros = {
        "token": obter_token_tiny(filial),
        "formato": "json",
        **params,
    }

    response = requests.get(
        f"{TINY_BASE_URL}/{endpoint}",
        params=parametros,
        timeout=90,
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
            detail={
                "erro": "Tiny não retornou JSON válido.",
                "resposta": response.text,
            },
        )

    retorno = dados.get("retorno", {})

    if safe_str(retorno.get("status")).upper() == "ERRO":
        codigo = safe_str(retorno.get("codigo_erro"))
        erros = retorno.get("erros") or []

        texto = " ".join(
            safe_str(item.get("erro") if isinstance(item, dict) else item)
            for item in erros
        ).lower()

        # O Tiny usa esse erro quando a página não possui registros.
        if (
            codigo == "20"
            or "não retornou registros" in texto
            or "nao retornou registros" in texto
        ):
            return {
                "retorno": {
                    "status": "OK",
                    "numero_paginas": 1,
                    "produtos": [],
                }
            }

        raise HTTPException(
            status_code=502,
            detail={
                "erro": "Tiny retornou erro.",
                "retorno": retorno,
            },
        )

    return dados


def buscar_pagina_produtos_tiny(
    pagina: int,
    filial: str,
) -> Dict[str, Any]:
    return tiny_get(
        "produtos.pesquisa.php",
        {
            "pagina": pagina,
            "situacao": "A",
        },
        filial,
    )


def obter_produto_tiny(
    produto_id: str,
    filial: str,
) -> Dict[str, Any]:
    dados = tiny_get(
        "produto.obter.php",
        {"id": produto_id},
        filial,
    )

    produto = dados.get("retorno", {}).get("produto", {})
    return produto if isinstance(produto, dict) else {}


def extrair_produtos_pagina(
    resposta: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], int]:
    retorno = resposta.get("retorno", {})
    produtos: List[Dict[str, Any]] = []

    for item in retorno.get("produtos", []) or []:
        if not isinstance(item, dict):
            continue

        produto = item.get("produto", item)

        if isinstance(produto, dict):
            produtos.append(produto)

    try:
        total_paginas = int(retorno.get("numero_paginas", 1) or 1)
    except (TypeError, ValueError):
        total_paginas = 1

    return produtos, max(total_paginas, 1)


# ============================================================
# NORMALIZAÇÃO DO PRODUTO
# ============================================================

def extrair_fornecedor(detalhe: Dict[str, Any]) -> Optional[str]:
    fornecedor = detalhe.get("fornecedor") or {}

    if isinstance(fornecedor, dict):
        nome = safe_str(
            fornecedor.get("nome")
            or fornecedor.get("nome_fantasia")
            or fornecedor.get("razao_social")
        )
    else:
        nome = safe_str(fornecedor)

    return nome or None


def normalizar_produto(
    produto_pesquisa: Dict[str, Any],
    filial: str,
    lote: int,
    produto_detalhado: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    detalhe = produto_detalhado or {}

    produto_id = safe_str(
        detalhe.get("id")
        or produto_pesquisa.get("id")
    )

    codigo = safe_str(
        detalhe.get("codigo")
        or produto_pesquisa.get("codigo")
    )

    descricao = safe_str(
        detalhe.get("nome")
        or detalhe.get("descricao")
        or produto_pesquisa.get("nome")
        or produto_pesquisa.get("descricao")
    )

    ncm = ncm_limpo(
        detalhe.get("ncm")
        or produto_pesquisa.get("ncm")
    )

    cest = safe_str(
        detalhe.get("cest")
        or produto_pesquisa.get("cest")
    )

    custo = dinheiro_para_float(
        detalhe.get("preco_custo")
        or detalhe.get("preco_custo_medio")
        or detalhe.get("custo")
        or produto_pesquisa.get("preco_custo")
        or produto_pesquisa.get("custo")
    )

    preco = dinheiro_para_float(
        detalhe.get("preco")
        or detalhe.get("preco_venda")
        or produto_pesquisa.get("preco")
        or produto_pesquisa.get("preco_venda")
    )

    agora = agora_iso()

    # ATENÇÃO:
    # O retorno abaixo usa somente colunas existentes no Supabase.
    # Não envia preco_custo, preco_venda nem status_tributario.
    return {
        "filial": normalizar_filial(filial),
        "tiny_produto_id": produto_id,
        "codigo": codigo or None,
        "sku": codigo or None,
        "descricao": descricao or None,
        "ncm": ncm or None,
        "cest": cest or None,
        "origem_mercadoria": (
            safe_str(
                detalhe.get("origem")
                or detalhe.get("origem_mercadoria")
                or produto_pesquisa.get("origem")
                or produto_pesquisa.get("origem_mercadoria")
            )
            or None
        ),
        "unidade": (
            safe_str(
                detalhe.get("unidade")
                or produto_pesquisa.get("unidade")
            )
            or None
        ),
        "preco": preco,
        "custo": custo,
        "fornecedor": extrair_fornecedor(detalhe),
        "ativo": True,
        "status_ia": status_ia_inicial(ncm),
        "lote": lote,
        "ultima_sincronizacao": agora,
        "updated_at": agora,
    }


# ============================================================
# SINCRONIZAÇÃO
# ============================================================

def sincronizar_produtos(
    filial: str,
    tamanho_lote: int = 50,
    detalhar: bool = True,
    limite_detalhes: int = 50,
    max_paginas: Optional[int] = None,
) -> Dict[str, Any]:
    filial_normalizada = normalizar_filial(filial)

    if filial_normalizada == "all":
        raise HTTPException(
            status_code=400,
            detail="Use filial=sp ou filial=mg.",
        )

    if tamanho_lote < 1 or tamanho_lote > 500:
        raise HTTPException(
            status_code=400,
            detail="tamanho_lote deve estar entre 1 e 500.",
        )

    if limite_detalhes < 0:
        raise HTTPException(
            status_code=400,
            detail="limite_detalhes não pode ser negativo.",
        )

    if max_paginas is not None and max_paginas < 1:
        raise HTTPException(
            status_code=400,
            detail="max_paginas deve ser maior ou igual a 1.",
        )

    pagina = 1
    paginas_processadas = 0
    indice_global = 0
    detalhes_executados = 0
    produtos_recebidos = 0
    produtos_salvos = 0
    produtos_ignorados_sem_id = 0
    sem_ncm = 0
    ncm_invalido = 0
    erros_detalhe: List[Dict[str, str]] = []

    while True:
        resposta = buscar_pagina_produtos_tiny(
            pagina,
            filial_normalizada,
        )

        produtos_pagina, total_paginas = extrair_produtos_pagina(resposta)
        paginas_processadas += 1
        produtos_recebidos += len(produtos_pagina)

        payload: List[Dict[str, Any]] = []

        for produto in produtos_pagina:
            indice_global += 1
            numero_lote = ((indice_global - 1) // tamanho_lote) + 1

            detalhe: Optional[Dict[str, Any]] = None
            produto_id = safe_str(produto.get("id"))

            precisa_detalhar = (
                detalhar
                and bool(produto_id)
                and detalhes_executados < limite_detalhes
            )

            if precisa_detalhar:
                try:
                    detalhe = obter_produto_tiny(
                        produto_id,
                        filial_normalizada,
                    )
                    detalhes_executados += 1
                    time.sleep(0.55)

                except HTTPException as exc:
                    erros_detalhe.append(
                        {
                            "tiny_produto_id": produto_id,
                            "erro": safe_str(exc.detail)[:300],
                        }
                    )

                except Exception as exc:
                    erros_detalhe.append(
                        {
                            "tiny_produto_id": produto_id,
                            "erro": str(exc)[:300],
                        }
                    )

            normalizado = normalizar_produto(
                produto_pesquisa=produto,
                filial=filial_normalizada,
                lote=numero_lote,
                produto_detalhado=detalhe,
            )

            if not normalizado.get("tiny_produto_id"):
                produtos_ignorados_sem_id += 1
                continue

            status_calculado = status_ncm(normalizado.get("ncm"))

            if status_calculado == "sem_ncm":
                sem_ncm += 1
            elif status_calculado == "ncm_invalido":
                ncm_invalido += 1

            payload.append(normalizado)

        # Divide o envio ao Supabase em blocos de até 100 registros.
        for inicio in range(0, len(payload), 100):
            lote_payload = payload[inicio: inicio + 100]
            salvos = supabase_upsert(lote_payload)

            # return=representation normalmente devolve os registros salvos.
            # Caso não devolva, contabilizamos o tamanho do payload enviado.
            produtos_salvos += len(salvos) if salvos else len(lote_payload)

        chegou_ultima_pagina = pagina >= total_paginas
        chegou_limite_paginas = (
            max_paginas is not None
            and paginas_processadas >= max_paginas
        )

        if chegou_ultima_pagina or chegou_limite_paginas:
            break

        pagina += 1
        time.sleep(0.6)

    total_lotes = (
        ((indice_global - 1) // tamanho_lote) + 1
        if indice_global
        else 0
    )

    return {
        "status": "ok",
        "filial": filial_normalizada,
        "tiny_utilizado": (
            "TINY_API_KEY_MINAS"
            if filial_normalizada == "mg"
            else "TINY_TOKEN"
        ),
        "produtos_recebidos": produtos_recebidos,
        "produtos_salvos": produtos_salvos,
        "produtos_ignorados_sem_id": produtos_ignorados_sem_id,
        "detalhes_consultados": detalhes_executados,
        "produtos_sem_ncm_nesta_execucao": sem_ncm,
        "produtos_com_ncm_invalido_nesta_execucao": ncm_invalido,
        "total_lotes_nesta_execucao": total_lotes,
        "tamanho_lote": tamanho_lote,
        "paginas_processadas": paginas_processadas,
        "ultima_pagina_lida": pagina,
        "erros_detalhe_amostra": erros_detalhe[:10],
        "observacao": (
            "A sincronização salva somente colunas existentes em "
            "produtos_tributarios. O status_tributario é calculado "
            "virtualmente nas respostas e não é gravado no banco."
        ),
    }


# ============================================================
# CONSULTAS
# ============================================================

def montar_filtro_busca(busca: str) -> str:
    """
    Prepara um termo simples para o filtro OR do PostgREST.
    """
    termo = safe_str(busca)
    termo = termo.replace(",", " ").replace("(", " ").replace(")", " ")
    termo = " ".join(termo.split())
    return termo


def listar_produtos(
    filial: str,
    status_tributario: Optional[str],
    status_ia: Optional[str],
    lote: Optional[int],
    busca: Optional[str],
    limite: int,
    offset: int,
) -> List[Dict[str, Any]]:
    if limite < 1:
        raise HTTPException(
            status_code=400,
            detail="limite deve ser maior ou igual a 1.",
        )

    if limite > 10000:
        limite = 10000

    if offset < 0:
        raise HTTPException(
            status_code=400,
            detail="offset não pode ser negativo.",
        )

    filial_normalizada = normalizar_filial(filial)

    # status_tributario é virtual. Quando esse filtro é usado,
    # buscamos uma faixa maior e filtramos em memória.
    usa_filtro_virtual = bool(status_tributario)

    limite_consulta = 10000 if usa_filtro_virtual else limite
    offset_consulta = 0 if usa_filtro_virtual else offset

    params: List[Tuple[str, str]] = [
        ("select", "*"),
        ("order", "lote.asc,descricao.asc"),
        ("limit", str(limite_consulta)),
        ("offset", str(offset_consulta)),
    ]

    if filial_normalizada != "all":
        params.append(("filial", f"eq.{filial_normalizada}"))

    if status_ia:
        params.append(("status_ia", f"eq.{safe_str(status_ia)}"))

    if lote is not None:
        params.append(("lote", f"eq.{lote}"))

    if busca:
        termo = montar_filtro_busca(busca)

        if termo:
            params.append(
                (
                    "or",
                    (
                        f"(descricao.ilike.*{termo}*,"
                        f"codigo.ilike.*{termo}*,"
                        f"sku.ilike.*{termo}*,"
                        f"ncm.ilike.*{termo}*)"
                    ),
                )
            )

    registros = [
        adicionar_status_tributario_virtual(registro)
        for registro in supabase_get(params)
    ]

    if status_tributario:
        status_desejado = safe_str(status_tributario).lower()

        registros = [
            registro
            for registro in registros
            if safe_str(registro.get("status_tributario")).lower()
            == status_desejado
        ]

        registros = registros[offset: offset + limite]

    return registros


def obter_resumo(filial: str) -> Dict[str, Any]:
    filial_normalizada = normalizar_filial(filial)

    params: List[Tuple[str, str]] = [
        (
            "select",
            (
                "id,ncm,lote,status_ia,percentual_confianca,"
                "data_analise,ativo"
            ),
        ),
        ("limit", "10000"),
    ]

    if filial_normalizada != "all":
        params.append(("filial", f"eq.{filial_normalizada}"))

    registros = supabase_get(params)

    por_status_tributario: Dict[str, int] = {}
    por_status_ia: Dict[str, int] = {}
    lotes = set()

    com_ncm = 0
    sem_ncm = 0
    ncm_invalido = 0
    ativos = 0
    analisados = 0
    confiancas: List[float] = []

    for registro in registros:
        status_tributario_calculado = status_ncm(registro.get("ncm"))
        por_status_tributario[status_tributario_calculado] = (
            por_status_tributario.get(status_tributario_calculado, 0) + 1
        )

        if status_tributario_calculado == "sem_ncm":
            sem_ncm += 1
        elif status_tributario_calculado == "ncm_invalido":
            ncm_invalido += 1
        else:
            com_ncm += 1

        status_ia_valor = (
            safe_str(registro.get("status_ia")).upper()
            or "SEM_STATUS"
        )

        por_status_ia[status_ia_valor] = (
            por_status_ia.get(status_ia_valor, 0) + 1
        )

        if registro.get("lote") is not None:
            lotes.add(registro.get("lote"))

        if registro.get("ativo") is True:
            ativos += 1

        if registro.get("data_analise"):
            analisados += 1

        confianca = registro.get("percentual_confianca")

        if confianca is not None:
            try:
                confiancas.append(float(confianca))
            except (TypeError, ValueError):
                pass

    total = len(registros)

    confianca_media = (
        round(sum(confiancas) / len(confiancas), 2)
        if confiancas
        else 0.0
    )

    return {
        "status": "ok",
        "filial": filial_normalizada,
        "total_produtos": total,
        "produtos_ativos": ativos,
        "produtos_com_ncm": com_ncm,
        "produtos_sem_ncm": sem_ncm,
        "produtos_com_ncm_invalido": ncm_invalido,
        "produtos_analisados": analisados,
        "percentual_confianca_medio": confianca_media,
        "total_lotes": len(lotes),
        "por_status_tributario": por_status_tributario,
        "por_status_ia": por_status_ia,
    }


def listar_lotes(filial: str) -> List[Dict[str, Any]]:
    registros = listar_produtos(
        filial=filial,
        status_tributario=None,
        status_ia=None,
        lote=None,
        busca=None,
        limite=10000,
        offset=0,
    )

    agrupado: Dict[int, Dict[str, Any]] = {}

    for registro in registros:
        try:
            numero = int(registro.get("lote") or 0)
        except (TypeError, ValueError):
            numero = 0

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
                "ncm_invalido": 0,
                "analisados": 0,
            },
        )

        item["total_produtos"] += 1

        status_ia_valor = safe_str(
            registro.get("status_ia")
        ).upper()

        status_tributario_valor = safe_str(
            registro.get("status_tributario")
        ).lower()

        if status_ia_valor == "PENDENTE":
            item["pendentes"] += 1
        elif status_ia_valor == "PROCESSANDO":
            item["processando"] += 1
        elif status_ia_valor in {"CONCLUIDO", "CONCLUÍDO"}:
            item["concluidos"] += 1
        elif status_ia_valor == "ERRO":
            item["erros"] += 1
        elif status_ia_valor == "REVISAO_MANUAL":
            item["revisao_manual"] += 1

        if status_tributario_valor == "sem_ncm":
            item["sem_ncm"] += 1
        elif status_tributario_valor == "ncm_invalido":
            item["ncm_invalido"] += 1

        if registro.get("data_analise"):
            item["analisados"] += 1

    resultado = list(agrupado.values())

    for item in resultado:
        if item["processando"] > 0:
            item["status"] = "PROCESSANDO"
        elif item["pendentes"] > 0:
            item["status"] = "PENDENTE"
        elif (
            item["erros"] > 0
            or item["revisao_manual"] > 0
            or item["ncm_invalido"] > 0
        ):
            item["status"] = "REVISAO"
        else:
            item["status"] = "CONCLUIDO"

    return sorted(
        resultado,
        key=lambda item: item["lote"],
    )


def obter_lote(
    filial: str,
    numero_lote: int,
) -> List[Dict[str, Any]]:
    """
    Função auxiliar para o endpoint GET /tributario/lote/{numero}.
    """
    return listar_produtos(
        filial=filial,
        status_tributario=None,
        status_ia=None,
        lote=numero_lote,
        busca=None,
        limite=10000,
        offset=0,
    )


def atualizar_produto(
    produto_id: str,
    dados: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Função auxiliar para o endpoint PATCH /tributario/produto/{produto_id}.
    """
    return supabase_patch(produto_id, dados)
