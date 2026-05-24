import os
import requests
from datetime import date, datetime
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


# =========================
# CONFIGURAÇÕES
# =========================

TINY_TOKEN = os.getenv("TINY_TOKEN", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()

TINY_BASE_URL = "https://api.tiny.com.br/api2"


app = FastAPI(
    title="MHM Dashboard Tiny API",
    description="Backend online para dashboard MHM com Tiny/Olist, Supabase e Lovable.",
    version="1.0.2",
)


# =========================
# CORS
# =========================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# HELPERS GERAIS
# =========================

def hoje_iso() -> str:
    return date.today().strftime("%Y-%m-%d")


def inicio_mes_iso() -> str:
    hoje = date.today()
    return hoje.replace(day=1).strftime("%Y-%m-%d")


def formatar_data_br(data_iso: str) -> str:
    try:
        dt = datetime.strptime(data_iso, "%Y-%m-%d")
        return dt.strftime("%d/%m/%Y")
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Data inválida. Use o formato YYYY-MM-DD. Exemplo: 2026-05-23"
        )


def parse_float(valor: Any) -> float:
    if valor is None:
        return 0.0

    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()

    if not texto:
        return 0.0

    texto = texto.replace("R$", "").replace(" ", "")

    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return 0.0


def verificar_tiny():
    if not TINY_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="TINY_TOKEN não configurado no Railway."
        )


def verificar_supabase():
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_URL ou SUPABASE_SERVICE_KEY não configurados no Railway."
        )


# =========================
# SUPABASE REST
# =========================

def supabase_headers() -> Dict[str, str]:
    verificar_supabase()

    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }


def supabase_insert(
    tabela: str,
    dados: Any,
    on_conflict: Optional[str] = None
) -> Any:
    verificar_supabase()

    url = f"{SUPABASE_URL}/rest/v1/{tabela}"
    headers = supabase_headers()

    if on_conflict:
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        url += f"?on_conflict={on_conflict}"

    try:
        response = requests.post(
            url,
            headers=headers,
            json=dados,
            timeout=30
        )

        if response.status_code not in [200, 201]:
            raise HTTPException(
                status_code=500,
                detail={
                    "mensagem": f"Erro ao salvar na tabela {tabela}.",
                    "status_code": response.status_code,
                    "resposta": response.text
                }
            )

        return response.json()

    except requests.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao comunicar com Supabase: {str(e)}"
        )


def supabase_get(
    tabela: str,
    query: str = ""
) -> Any:
    verificar_supabase()

    url = f"{SUPABASE_URL}/rest/v1/{tabela}"

    if query:
        url += f"?{query}"

    try:
        response = requests.get(
            url,
            headers=supabase_headers(),
            timeout=30
        )

        if response.status_code != 200:
            raise HTTPException(
                status_code=500,
                detail={
                    "mensagem": f"Erro ao buscar na tabela {tabela}.",
                    "status_code": response.status_code,
                    "resposta": response.text
                }
            )

        return response.json()

    except requests.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao comunicar com Supabase: {str(e)}"
        )


def supabase_delete(
    tabela: str,
    query: str
) -> Any:
    verificar_supabase()

    url = f"{SUPABASE_URL}/rest/v1/{tabela}?{query}"

    try:
        response = requests.delete(
            url,
            headers=supabase_headers(),
            timeout=30
        )

        if response.status_code not in [200, 204]:
            raise HTTPException(
                status_code=500,
                detail={
                    "mensagem": f"Erro ao apagar dados da tabela {tabela}.",
                    "status_code": response.status_code,
                    "resposta": response.text
                }
            )

        if response.text:
            return response.json()

        return {"status": "apagado"}

    except requests.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao comunicar com Supabase: {str(e)}"
        )


# =========================
# TINY API V2
# =========================

def tiny_post(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    verificar_tiny()

    url = f"{TINY_BASE_URL}/{endpoint}"

    data = {
        "token": TINY_TOKEN,
        "formato": "json",
        **payload
    }

    try:
        response = requests.post(url, data=data, timeout=40)
        response.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Erro ao comunicar com Tiny/Olist: {str(e)}"
        )

    try:
        json_data = response.json()
    except ValueError:
        raise HTTPException(
            status_code=502,
            detail="Tiny/Olist retornou uma resposta que não é JSON."
        )

    retorno = json_data.get("retorno", {})
    status = str(retorno.get("status", "")).lower()

    if status == "erro":
        raise HTTPException(
            status_code=400,
            detail={
                "mensagem": "Tiny/Olist retornou erro.",
                "retorno": retorno
            }
        )

    return json_data


def pesquisar_pedidos_tiny(
    data_inicial_iso: str,
    data_final_iso: str,
    max_paginas: int = 10
) -> List[Dict[str, Any]]:

    data_inicial_br = formatar_data_br(data_inicial_iso)
    data_final_br = formatar_data_br(data_final_iso)

    todos = []

    for pagina in range(1, max_paginas + 1):
        payload = {
            "dataInicial": data_inicial_br,
            "dataFinal": data_final_br,
            "pagina": pagina,
            "sort": "DESC"
        }

        try:
            resposta = tiny_post("pedidos.pesquisa.php", payload)
            retorno = resposta.get("retorno", {})
        except HTTPException as e:
            detail = e.detail

            if isinstance(detail, dict):
                retorno_erro = detail.get("retorno", {})
                codigo_erro = str(retorno_erro.get("codigo_erro", ""))
                erros = retorno_erro.get("erros", [])

                mensagem_erro = ""
                if erros and isinstance(erros, list):
                    mensagem_erro = str(erros[0].get("erro", ""))

                # Código 20 no Tiny/Olist = consulta sem registros
                if codigo_erro == "20" or "não retornou registros" in mensagem_erro.lower():
                    return []

            raise e

        pedidos = retorno.get("pedidos", []) or []

        for item in pedidos:
            pedido = item.get("pedido", item)
            todos.append(pedido)

        numero_paginas = int(retorno.get("numero_paginas", 1) or 1)

        if pagina >= numero_paginas:
            break

    return todos


def obter_pedido_tiny(id_pedido: Any) -> Dict[str, Any]:
    resposta = tiny_post("pedido.obter.php", {"id": id_pedido})
    retorno = resposta.get("retorno", {})
    return retorno.get("pedido", {}) or {}


def extrair_itens_pedido(pedido: Dict[str, Any]) -> List[Dict[str, Any]]:
    itens_raw = pedido.get("itens", []) or []
    itens = []

    for item_wrapper in itens_raw:
        item = item_wrapper.get("item", item_wrapper)

        produto_id = (
            item.get("id_produto")
            or item.get("idProduto")
            or item.get("produto_id")
            or ""
        )

        nome = (
            item.get("descricao")
            or item.get("nome")
            or item.get("produto")
            or "Produto sem nome"
        )

        sku = (
            item.get("codigo")
            or item.get("sku")
            or item.get("codigo_produto")
            or ""
        )

        quantidade = parse_float(
            item.get("quantidade")
            or item.get("qtde")
            or item.get("qtd")
            or 0
        )

        valor_unitario = parse_float(
            item.get("valor_unitario")
            or item.get("valorUnitario")
            or item.get("preco")
            or item.get("valor")
            or 0
        )

        valor_total = round(quantidade * valor_unitario, 2)

        itens.append({
            "tiny_produto_id": str(produto_id) if produto_id else "",
            "sku": str(sku) if sku else "",
            "nome_produto": nome,
            "quantidade": quantidade,
            "valor_unitario": valor_unitario,
            "valor_total": valor_total
        })

    return itens


def obter_estoque_tiny(id_produto: str) -> Optional[Dict[str, Any]]:
    if not id_produto:
        return None

    try:
        resposta = tiny_post("produto.obter.estoque.php", {"id": id_produto})
        retorno = resposta.get("retorno", {})
        produto = retorno.get("produto", {}) or {}

        return {
            "tiny_id": str(produto.get("id") or id_produto),
            "nome": produto.get("nome"),
            "sku": produto.get("codigo"),
            "estoque_atual": parse_float(produto.get("saldo")),
        }
    except Exception:
        return None


# =========================
# CÁLCULO DE RANKING
# =========================

def calcular_rankings(
    data_inicial: str,
    data_final: str,
    max_paginas: int = 10
) -> Dict[str, Any]:

    pedidos_resumidos = pesquisar_pedidos_tiny(
        data_inicial_iso=data_inicial,
        data_final_iso=data_final,
        max_paginas=max_paginas
    )

    produtos = defaultdict(lambda: {
        "tiny_produto_id": "",
        "sku": "",
        "nome_produto": "",
        "quantidade_vendida": 0.0,
        "valor_total_vendido": 0.0,
        "percentual_participacao": 0.0,
        "pedidos_count": 0
    })

    pedidos_para_salvar = []
    itens_para_salvar = []

    total_pedidos = 0
    faturamento_total = 0.0
    total_unidades = 0.0
    erros = []

    for pedido_resumo in pedidos_resumidos:
        tiny_pedido_id = str(pedido_resumo.get("id") or "")

        if not tiny_pedido_id:
            continue

        try:
            pedido = obter_pedido_tiny(tiny_pedido_id)

            numero = str(
                pedido.get("numero")
                or pedido_resumo.get("numero")
                or ""
            )

            data_pedido = (
                pedido.get("data_pedido")
                or pedido.get("data")
                or pedido_resumo.get("data_pedido")
                or data_final
            )

            if isinstance(data_pedido, str) and "/" in data_pedido:
                try:
                    data_pedido = datetime.strptime(
                        data_pedido,
                        "%d/%m/%Y"
                    ).strftime("%Y-%m-%d")
                except Exception:
                    data_pedido = data_final

            cliente = pedido.get("cliente", {}) or {}
            cliente_nome = cliente.get("nome") or pedido.get("nome_cliente") or ""

            situacao = pedido.get("situacao") or pedido_resumo.get("situacao") or ""

            itens = extrair_itens_pedido(pedido)

            valor_total_pedido = sum(i["valor_total"] for i in itens)

            pedidos_para_salvar.append({
                "tiny_id": tiny_pedido_id,
                "numero": numero,
                "data_pedido": data_pedido,
                "cliente_nome": cliente_nome,
                "situacao": situacao,
                "canal_venda": "Tiny/Olist",
                "valor_total": round(valor_total_pedido, 2)
            })

            total_pedidos += 1
            faturamento_total += valor_total_pedido

            for item in itens:
                chave = item["tiny_produto_id"] or item["sku"] or item["nome_produto"]

                produtos[chave]["tiny_produto_id"] = item["tiny_produto_id"]
                produtos[chave]["sku"] = item["sku"]
                produtos[chave]["nome_produto"] = item["nome_produto"]
                produtos[chave]["quantidade_vendida"] += item["quantidade"]
                produtos[chave]["valor_total_vendido"] += item["valor_total"]
                produtos[chave]["pedidos_count"] += 1

                total_unidades += item["quantidade"]

                itens_para_salvar.append({
                    "tiny_pedido_id": tiny_pedido_id,
                    "tiny_produto_id": item["tiny_produto_id"],
                    "sku": item["sku"],
                    "nome_produto": item["nome_produto"],
                    "quantidade": item["quantidade"],
                    "valor_unitario": item["valor_unitario"],
                    "valor_total": item["valor_total"],
                    "data_pedido": data_pedido
                })

        except Exception as e:
            erros.append({
                "tiny_pedido_id": tiny_pedido_id,
                "erro": str(e)
            })

    lista_produtos = []

    for produto in produtos.values():
        produto["quantidade_vendida"] = round(produto["quantidade_vendida"], 3)
        produto["valor_total_vendido"] = round(produto["valor_total_vendido"], 2)

        if faturamento_total > 0:
            produto["percentual_participacao"] = round(
                (produto["valor_total_vendido"] / faturamento_total) * 100,
                2
            )
        else:
            produto["percentual_participacao"] = 0.0

        lista_produtos.append(produto)

    ranking_quantidade = sorted(
        lista_produtos,
        key=lambda p: p["quantidade_vendida"],
        reverse=True
    )

    ranking_valor = sorted(
        lista_produtos,
        key=lambda p: p["valor_total_vendido"],
        reverse=True
    )

    for index, produto in enumerate(ranking_quantidade, start=1):
        produto["posicao_quantidade"] = index

    mapa_posicao_valor = {}

    for index, produto in enumerate(ranking_valor, start=1):
        chave = produto["tiny_produto_id"] or produto["sku"] or produto["nome_produto"]
        mapa_posicao_valor[chave] = index

    for produto in lista_produtos:
        chave = produto["tiny_produto_id"] or produto["sku"] or produto["nome_produto"]
        produto["posicao_valor"] = mapa_posicao_valor.get(chave)

    ticket_medio = faturamento_total / total_pedidos if total_pedidos else 0

    return {
        "status": "ok",
        "periodo": {
            "data_inicial": data_inicial,
            "data_final": data_final
        },
        "resumo": {
            "total_pedidos": total_pedidos,
            "faturamento_total": round(faturamento_total, 2),
            "total_unidades_vendidas": round(total_unidades, 3),
            "ticket_medio": round(ticket_medio, 2),
            "total_produtos_diferentes": len(lista_produtos)
        },
        "pedidos_para_salvar": pedidos_para_salvar,
        "itens_para_salvar": itens_para_salvar,
        "produtos_ranking": lista_produtos,
        "top_10_por_quantidade": ranking_quantidade[:10],
        "top_10_por_valor": ranking_valor[:10],
        "erros": erros
    }


# =========================
# SALVAR NO SUPABASE
# =========================

def salvar_sync_supabase(resultado: Dict[str, Any]) -> Dict[str, Any]:
    pedidos = resultado.get("pedidos_para_salvar", [])
    itens = resultado.get("itens_para_salvar", [])

    if pedidos:
        supabase_insert(
            "pedidos",
            pedidos,
            on_conflict="tiny_id"
        )

    data_inicial = resultado["periodo"]["data_inicial"]
    data_final = resultado["periodo"]["data_final"]

    supabase_delete(
        "itens_pedido",
        f"data_pedido=gte.{data_inicial}&data_pedido=lte.{data_final}"
    )

    if itens:
        supabase_insert(
            "itens_pedido",
            itens
        )

    return {
        "pedidos_salvos": len(pedidos),
        "itens_salvos": len(itens)
    }


def salvar_ranking_diario(data_ranking: str, produtos: List[Dict[str, Any]]) -> int:
    supabase_delete(
        "ranking_diario",
        f"data_ranking=eq.{data_ranking}"
    )

    registros = []

    for produto in produtos:
        registros.append({
            "data_ranking": data_ranking,
            "tiny_produto_id": produto.get("tiny_produto_id"),
            "sku": produto.get("sku"),
            "nome_produto": produto.get("nome_produto"),
            "quantidade_vendida": produto.get("quantidade_vendida", 0),
            "valor_total_vendido": produto.get("valor_total_vendido", 0),
            "percentual_participacao": produto.get("percentual_participacao", 0),
            "pedidos_count": produto.get("pedidos_count", 0),
            "posicao_quantidade": produto.get("posicao_quantidade"),
            "posicao_valor": produto.get("posicao_valor")
        })

    if registros:
        supabase_insert("ranking_diario", registros)

    return len(registros)


def salvar_ranking_mensal(data_referencia: str, produtos: List[Dict[str, Any]]) -> int:
    dt = datetime.strptime(data_referencia, "%Y-%m-%d")
    ano = dt.year
    mes = dt.month

    supabase_delete(
        "ranking_mensal",
        f"ano=eq.{ano}&mes=eq.{mes}"
    )

    registros = []

    for produto in produtos:
        registros.append({
            "ano": ano,
            "mes": mes,
            "tiny_produto_id": produto.get("tiny_produto_id"),
            "sku": produto.get("sku"),
            "nome_produto": produto.get("nome_produto"),
            "quantidade_vendida": produto.get("quantidade_vendida", 0),
            "valor_total_vendido": produto.get("valor_total_vendido", 0),
            "percentual_participacao": produto.get("percentual_participacao", 0),
            "pedidos_count": produto.get("pedidos_count", 0),
            "posicao_quantidade": produto.get("posicao_quantidade"),
            "posicao_valor": produto.get("posicao_valor")
        })

    if registros:
        supabase_insert("ranking_mensal", registros)

    return len(registros)


# =========================
# ROTAS PRINCIPAIS
# =========================

@app.get("/")
def home():
    return {
        "status": "online",
        "app": "MHM Dashboard Tiny API",
        "versao": "1.0.2",
        "rotas": [
            "/health",
            "/teste/tiny-pedidos",
            "/teste/supabase",
            "/dashboard/top-dia",
            "/dashboard/top-mes",
            "/dashboard/periodo",
            "/sync/tiny-dia",
            "/sync/tiny-mes",
            "/db/ranking-diario",
            "/db/ranking-mensal"
        ]
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "tiny_token_configurado": bool(TINY_TOKEN),
        "supabase_url_configurado": bool(SUPABASE_URL),
        "supabase_service_key_configurado": bool(SUPABASE_SERVICE_KEY),
        "data": hoje_iso()
    }


@app.get("/teste/tiny-pedidos")
def teste_tiny_pedidos(
    data_inicial: str = Query(default_factory=hoje_iso),
    data_final: str = Query(default_factory=hoje_iso)
):
    pedidos = pesquisar_pedidos_tiny(
        data_inicial_iso=data_inicial,
        data_final_iso=data_final,
        max_paginas=1
    )

    return {
        "status": "ok",
        "data_inicial": data_inicial,
        "data_final": data_final,
        "total_retornado": len(pedidos),
        "amostra": pedidos[:5]
    }


@app.get("/teste/supabase")
def teste_supabase():
    produtos = supabase_get("produtos", "select=*&limit=5")

    return {
        "status": "ok",
        "mensagem": "Conexão com Supabase funcionando.",
        "amostra_produtos": produtos
    }


@app.get("/dashboard/top-dia")
def dashboard_top_dia(
    data: str = Query(default_factory=hoje_iso),
    max_paginas: int = Query(10, ge=1, le=50)
):
    resultado = calcular_rankings(
        data_inicial=data,
        data_final=data,
        max_paginas=max_paginas
    )

    return {
        "status": "ok",
        "tipo": "top_dia",
        "data": data,
        "resumo": resultado["resumo"],
        "top_10_por_quantidade": resultado["top_10_por_quantidade"],
        "top_10_por_valor": resultado["top_10_por_valor"],
        "erros": resultado["erros"]
    }


@app.get("/dashboard/top-mes")
def dashboard_top_mes(
    max_paginas: int = Query(30, ge=1, le=100)
):
    data_inicial = inicio_mes_iso()
    data_final = hoje_iso()

    resultado = calcular_rankings(
        data_inicial=data_inicial,
        data_final=data_final,
        max_paginas=max_paginas
    )

    return {
        "status": "ok",
        "tipo": "top_mes",
        "periodo": {
            "data_inicial": data_inicial,
            "data_final": data_final
        },
        "resumo": resultado["resumo"],
        "top_10_por_quantidade": resultado["top_10_por_quantidade"],
        "top_10_por_valor": resultado["top_10_por_valor"],
        "erros": resultado["erros"]
    }


@app.get("/dashboard/periodo")
def dashboard_periodo(
    data_inicial: str = Query(..., description="Formato YYYY-MM-DD"),
    data_final: str = Query(..., description="Formato YYYY-MM-DD"),
    max_paginas: int = Query(20, ge=1, le=100)
):
    resultado = calcular_rankings(
        data_inicial=data_inicial,
        data_final=data_final,
        max_paginas=max_paginas
    )

    return {
        "status": "ok",
        "tipo": "periodo",
        "periodo": resultado["periodo"],
        "resumo": resultado["resumo"],
        "top_10_por_quantidade": resultado["top_10_por_quantidade"],
        "top_10_por_valor": resultado["top_10_por_valor"],
        "erros": resultado["erros"]
    }


# =========================
# ROTAS DE SYNC PARA SUPABASE
# =========================

@app.post("/sync/tiny-dia")
def sync_tiny_dia(
    data: str = Query(default_factory=hoje_iso),
    max_paginas: int = Query(10, ge=1, le=50)
):
    resultado = calcular_rankings(
        data_inicial=data,
        data_final=data,
        max_paginas=max_paginas
    )

    salvamento = salvar_sync_supabase(resultado)

    total_ranking = salvar_ranking_diario(
        data_ranking=data,
        produtos=resultado["produtos_ranking"]
    )

    return {
        "status": "ok",
        "mensagem": "Sincronização diária concluída.",
        "data": data,
        "resumo": resultado["resumo"],
        "salvamento": salvamento,
        "ranking_diario_salvo": total_ranking,
        "top_10_por_quantidade": resultado["top_10_por_quantidade"],
        "top_10_por_valor": resultado["top_10_por_valor"],
        "erros": resultado["erros"]
    }


@app.post("/sync/tiny-mes")
def sync_tiny_mes(
    max_paginas: int = Query(30, ge=1, le=100)
):
    data_inicial = inicio_mes_iso()
    data_final = hoje_iso()

    resultado = calcular_rankings(
        data_inicial=data_inicial,
        data_final=data_final,
        max_paginas=max_paginas
    )

    salvamento = salvar_sync_supabase(resultado)

    total_ranking = salvar_ranking_mensal(
        data_referencia=data_final,
        produtos=resultado["produtos_ranking"]
    )

    return {
        "status": "ok",
        "mensagem": "Sincronização mensal concluída.",
        "periodo": resultado["periodo"],
        "resumo": resultado["resumo"],
        "salvamento": salvamento,
        "ranking_mensal_salvo": total_ranking,
        "top_10_por_quantidade": resultado["top_10_por_quantidade"],
        "top_10_por_valor": resultado["top_10_por_valor"],
        "erros": resultado["erros"]
    }


# =========================
# ROTAS PARA LOVABLE LER DO SUPABASE
# =========================

@app.get("/db/ranking-diario")
def db_ranking_diario(
    data: str = Query(default_factory=hoje_iso),
    limite: int = Query(10, ge=1, le=100)
):
    query = (
        "select=*"
        f"&data_ranking=eq.{data}"
        "&order=posicao_quantidade.asc"
        f"&limit={limite}"
    )

    dados = supabase_get("ranking_diario", query)

    return {
        "status": "ok",
        "data": data,
        "ranking": dados
    }


@app.get("/db/ranking-mensal")
def db_ranking_mensal(
    ano: Optional[int] = None,
    mes: Optional[int] = None,
    limite: int = Query(10, ge=1, le=100)
):
    hoje = date.today()

    ano_final = ano or hoje.year
    mes_final = mes or hoje.month

    query = (
        "select=*"
        f"&ano=eq.{ano_final}"
        f"&mes=eq.{mes_final}"
        "&order=posicao_quantidade.asc"
        f"&limit={limite}"
    )

    dados = supabase_get("ranking_mensal", query)

    return {
        "status": "ok",
        "ano": ano_final,
        "mes": mes_final,
        "ranking": dados
    }
