import os
import time
import requests
from datetime import date, datetime, timedelta
from calendar import monthrange
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="MHM Dashboard Tiny API",
    version="2.0.1",
    description="API para sincronizar Tiny/Olist com Supabase e alimentar dashboard Lovable."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# ENV
# ============================================================

TINY_TOKEN = os.getenv("TINY_TOKEN", "").strip()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")

# Aceita os dois nomes, para não quebrar se o Railway tiver uma ou outra variável
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
)

TINY_BASE_URL = "https://api.tiny.com.br/api2"


# ============================================================
# HELPERS GERAIS
# ============================================================

def hoje_br() -> date:
    return datetime.now().date()


def parse_data(data_str: str) -> date:
    try:
        return datetime.strptime(data_str, "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Data inválida. Use o formato YYYY-MM-DD."
        )


def data_br_para_iso(data_br: str) -> Optional[str]:
    """
    Tiny normalmente retorna data como DD/MM/YYYY.
    Converte para YYYY-MM-DD.
    """
    if not data_br:
        return None

    try:
        return datetime.strptime(data_br, "%d/%m/%Y").date().isoformat()
    except Exception:
        return None


def data_iso_para_br(data_iso: str) -> str:
    return datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")


def dinheiro_para_float(valor: Any) -> float:
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
    except Exception:
        return 0.0


def safe_str(valor: Any) -> str:
    if valor is None:
        return ""
    return str(valor)


def validar_env():
    faltando = []

    if not TINY_TOKEN:
        faltando.append("TINY_TOKEN")

    if not SUPABASE_URL:
        faltando.append("SUPABASE_URL")

    if not SUPABASE_SERVICE_ROLE_KEY:
        faltando.append("SUPABASE_SERVICE_ROLE_KEY ou SUPABASE_SERVICE_KEY")

    if faltando:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Variáveis de ambiente ausentes no Railway.",
                "faltando": faltando
            }
        )


# ============================================================
# SUPABASE REST
# ============================================================

def supabase_headers(prefer: Optional[str] = None) -> Dict[str, str]:
    validar_env()

    headers = {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

    return headers


def supabase_get(
    tabela: str,
    params: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    url = f"{SUPABASE_URL}/rest/v1/{tabela}"

    response = requests.get(
        url,
        headers=supabase_headers(),
        params=params or {},
        timeout=60
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": f"Erro ao consultar Supabase tabela {tabela}",
                "status_code": response.status_code,
                "resposta": response.text
            }
        )

    return response.json()


def supabase_insert(
    tabela: str,
    dados: Any,
    upsert: bool = False,
    on_conflict: Optional[str] = None
) -> Any:
    """
    Insere ou faz upsert no Supabase.

    Quando usar upsert=True e tiver coluna única, informe:
    on_conflict="tiny_id"
    """
    url = f"{SUPABASE_URL}/rest/v1/{tabela}"

    prefer = "return=representation"
    params = {}

    if upsert:
        prefer = "resolution=merge-duplicates,return=representation"

        if on_conflict:
            params["on_conflict"] = on_conflict

    response = requests.post(
        url,
        headers=supabase_headers(prefer=prefer),
        params=params,
        json=dados,
        timeout=90
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": f"Erro ao salvar no Supabase tabela {tabela}",
                "status_code": response.status_code,
                "resposta": response.text,
                "dados_enviados": dados
            }
        )

    try:
        return response.json()
    except Exception:
        return {"status": "ok"}


def supabase_patch(
    tabela: str,
    filtros: Dict[str, str],
    dados: Dict[str, Any]
) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{tabela}"

    response = requests.patch(
        url,
        headers=supabase_headers(prefer="return=representation"),
        params=filtros,
        json=dados,
        timeout=60
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": f"Erro ao atualizar Supabase tabela {tabela}",
                "status_code": response.status_code,
                "resposta": response.text
            }
        )

    try:
        return response.json()
    except Exception:
        return {"status": "ok"}


def supabase_delete(
    tabela: str,
    params: Dict[str, str]
) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/{tabela}"

    response = requests.delete(
        url,
        headers=supabase_headers(prefer="return=representation"),
        params=params,
        timeout=60
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": f"Erro ao deletar no Supabase tabela {tabela}",
                "status_code": response.status_code,
                "resposta": response.text
            }
        )

    try:
        return response.json()
    except Exception:
        return {"status": "ok"}


def salvar_configuracao(chave: str, valor: str):
    """
    Usa tabela configuracoes.

    Se ela ainda não existir, crie no Supabase:

    create table if not exists configuracoes (
      id uuid primary key default gen_random_uuid(),
      chave text unique not null,
      valor text,
      created_at timestamptz default now(),
      updated_at timestamptz default now()
    );
    """

    existente = supabase_get(
        "configuracoes",
        {
            "chave": f"eq.{chave}",
            "select": "*"
        }
    )

    payload = {
        "chave": chave,
        "valor": valor,
        "updated_at": datetime.now().isoformat()
    }

    if existente:
        return supabase_patch(
            "configuracoes",
            {"chave": f"eq.{chave}"},
            payload
        )

    return supabase_insert("configuracoes", payload)


def buscar_configuracao(chave: str) -> Optional[str]:
    resultado = supabase_get(
        "configuracoes",
        {
            "chave": f"eq.{chave}",
            "select": "*",
            "limit": "1"
        }
    )

    if not resultado:
        return None

    return resultado[0].get("valor")


# ============================================================
# TINY
# ============================================================

def tiny_get(endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    validar_env()

    url = f"{TINY_BASE_URL}/{endpoint}"

    params_base = {
        "token": TINY_TOKEN,
        "formato": "json",
    }

    params_base.update(params)

    response = requests.get(url, params=params_base, timeout=90)

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro HTTP ao consultar Tiny.",
                "status_code": response.status_code,
                "resposta": response.text
            }
        )

    try:
        dados = response.json()
    except Exception:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Tiny não retornou JSON válido.",
                "resposta": response.text
            }
        )

    retorno = dados.get("retorno", {})

    status = retorno.get("status")
    if status and str(status).upper() == "ERRO":
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Tiny retornou erro.",
                "retorno": retorno
            }
        )

    return dados


def pesquisar_pedidos_tiny(
    data_inicial: date,
    data_final: date,
    pagina: int = 1
) -> Dict[str, Any]:
    """
    Pesquisa pedidos no Tiny por período.
    """
    return tiny_get(
        "pedidos.pesquisa.php",
        {
            "dataInicial": data_inicial.strftime("%d/%m/%Y"),
            "dataFinal": data_final.strftime("%d/%m/%Y"),
            "pagina": pagina
        }
    )


def obter_pedido_tiny(id_pedido: str) -> Dict[str, Any]:
    return tiny_get(
        "pedido.obter.php",
        {
            "id": id_pedido
        }
    )


def extrair_lista_pedidos(resposta_tiny: Dict[str, Any]) -> List[Dict[str, Any]]:
    retorno = resposta_tiny.get("retorno", {})
    pedidos_raw = retorno.get("pedidos", [])

    pedidos = []

    for item in pedidos_raw:
        pedido = item.get("pedido", item)
        pedidos.append(pedido)

    return pedidos


def buscar_pedidos_periodo_tiny(
    data_inicial: date,
    data_final: date,
    pausa_segundos: float = 0.8
) -> List[Dict[str, Any]]:
    """
    Busca pedidos paginando.
    """
    todos = []
    pagina = 1

    while True:
        resposta = pesquisar_pedidos_tiny(data_inicial, data_final, pagina)
        retorno = resposta.get("retorno", {})

        pedidos = extrair_lista_pedidos(resposta)
        todos.extend(pedidos)

        numero_paginas = int(retorno.get("numero_paginas", 1) or 1)

        if pagina >= numero_paginas:
            break

        pagina += 1
        time.sleep(pausa_segundos)

    return todos


def pedido_tem_venda_valida(pedido: Dict[str, Any]) -> bool:
    """
    Evita contar orçamento/cancelado quando o Tiny retorna situação.
    Ajuste aqui depois se quiser filtrar outros status.
    """
    situacao = str(pedido.get("situacao", "")).lower().strip()

    status_invalidos = [
        "cancelado",
        "cancelada",
        "orçamento",
        "orcamento"
    ]

    for invalido in status_invalidos:
        if invalido in situacao:
            return False

    return True


def normalizar_pedido_resumo(pedido: Dict[str, Any]) -> Dict[str, Any]:
    id_pedido = safe_str(
        pedido.get("id")
        or pedido.get("numero")
        or pedido.get("numero_ecommerce")
        or ""
    )

    data_pedido_iso = (
        data_br_para_iso(pedido.get("data_pedido", ""))
        or data_br_para_iso(pedido.get("data", ""))
        or data_br_para_iso(pedido.get("data_criacao", ""))
    )

    valor = dinheiro_para_float(
        pedido.get("total_pedido")
        or pedido.get("valor")
        or pedido.get("total")
        or 0
    )

    cliente = pedido.get("nome") or pedido.get("cliente") or ""

    return {
        "tiny_id": id_pedido,
        "numero": safe_str(pedido.get("numero", "")),
        "numero_ecommerce": safe_str(pedido.get("numero_ecommerce", "")),
        "data_pedido": data_pedido_iso,
        "cliente": cliente,
        "situacao": pedido.get("situacao", ""),
        "valor_total": valor,
        "raw": pedido,
        "updated_at": datetime.now().isoformat()
    }


def extrair_itens_do_pedido_completo(
    pedido_completo: Dict[str, Any],
    pedido_id: str,
    data_pedido: Optional[str]
) -> List[Dict[str, Any]]:
    retorno = pedido_completo.get("retorno", {})
    pedido = retorno.get("pedido", {})
    itens_raw = pedido.get("itens", [])

    itens = []

    for item_wrap in itens_raw:
        item = item_wrap.get("item", item_wrap)

        quantidade = dinheiro_para_float(item.get("quantidade", 0))
        valor_unitario = dinheiro_para_float(item.get("valor_unitario", 0))
        valor_total = quantidade * valor_unitario

        produto_nome = item.get("descricao", "") or item.get("nome", "")
        codigo = item.get("codigo", "")

        itens.append({
    "pedido_tiny_id": safe_str(pedido_id),
    "data_pedido": data_pedido,

    # Mantém compatibilidade com banco novo e banco antigo
    "produto_nome": produto_nome,
    "nome_produto": produto_nome,

    "codigo": safe_str(codigo),
    "sku": safe_str(codigo),
    "quantidade": quantidade,
    "valor_unitario": valor_unitario,
    "valor_total": valor_total,
    "raw": item,
    "updated_at": datetime.now().isoformat()
})

    return itens


# ============================================================
# CÁLCULOS
# ============================================================

def calcular_resumo_e_ranking(
    pedidos: List[Dict[str, Any]],
    itens: List[Dict[str, Any]],
    data_inicio: date,
    data_fim: date
) -> Dict[str, Any]:
    faturamento = sum(float(p.get("valor_total") or 0) for p in pedidos)
    total_pedidos = len(pedidos)

    ticket_medio = faturamento / total_pedidos if total_pedidos else 0

    produtos: Dict[str, Dict[str, Any]] = {}

    for item in itens:
        nome = item.get("produto_nome") or "Produto sem nome"
        sku = item.get("sku") or item.get("codigo") or ""

        chave = f"{sku}::{nome}"

        if chave not in produtos:
            produtos[chave] = {
                "produto_nome": nome,
                "sku": sku,
                "quantidade_total": 0.0,
                "valor_total": 0.0,
            }

        produtos[chave]["quantidade_total"] += float(item.get("quantidade") or 0)
        produtos[chave]["valor_total"] += float(item.get("valor_total") or 0)

    ranking = list(produtos.values())
    ranking.sort(key=lambda x: x["valor_total"], reverse=True)

    for posicao, produto in enumerate(ranking, start=1):
        produto["posicao"] = posicao
        produto["percentual_participacao"] = (
            produto["valor_total"] / faturamento * 100
            if faturamento > 0
            else 0
        )

    return {
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "faturamento": round(faturamento, 2),
        "total_pedidos": total_pedidos,
        "ticket_medio": round(ticket_medio, 2),
        "ranking": ranking
    }


# ============================================================
# SALVAR SINCRONIZAÇÃO
# ============================================================

def salvar_sync_log(
    tipo: str,
    data_inicio: date,
    data_fim: date,
    status: str,
    mensagem: str,
    total_pedidos: int = 0,
    faturamento: float = 0.0
):
    payload = {
        "tipo": tipo,
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "status": status,
        "mensagem": mensagem,
        "total_pedidos": total_pedidos,
        "faturamento": faturamento,
        "created_at": datetime.now().isoformat()
    }

    try:
        supabase_insert("sync_logs", payload)
    except Exception:
        pass


def limpar_itens_dos_pedidos(pedidos_normalizados: List[Dict[str, Any]]):
    """
    Remove itens antigos dos pedidos que estão sendo sincronizados,
    para evitar duplicar itens quando rodar a mesma data novamente.
    """
    ids = [
        p.get("tiny_id")
        for p in pedidos_normalizados
        if p.get("tiny_id")
    ]

    if not ids:
        return

    # Divide em lotes para não criar URL muito grande
    tamanho_lote = 50

    for i in range(0, len(ids), tamanho_lote):
        lote = ids[i:i + tamanho_lote]
        ids_formatados = ",".join([f'"{x}"' for x in lote])

        try:
            supabase_delete(
                "itens_pedido",
                {
                    "pedido_tiny_id": f"in.({ids_formatados})"
                }
            )
        except Exception:
            # Se não conseguir deletar, segue mesmo assim.
            # Depois podemos criar uma constraint única para itens.
            pass


def limpar_ranking_periodo(data_inicio: date, data_fim: date, tipo: str):
    try:
        supabase_delete(
            "ranking_periodo",
            {
                "data_inicio": f"eq.{data_inicio.isoformat()}",
                "data_fim": f"eq.{data_fim.isoformat()}",
                "tipo": f"eq.{tipo}"
            }
        )
    except Exception:
        pass
def sincronizar_periodo(
    data_inicio: date,
    data_fim: date,
    tipo: str = "periodo",
    buscar_itens: bool = True
) -> Dict[str, Any]:
    """
    Busca pedidos no Tiny, salva pedidos, itens e resumos no Supabase.
    """

    pedidos_tiny = buscar_pedidos_periodo_tiny(data_inicio, data_fim)

    pedidos_normalizados = []
    itens_normalizados = []

    for pedido_raw in pedidos_tiny:
        if not pedido_tem_venda_valida(pedido_raw):
            continue

        pedido_norm = normalizar_pedido_resumo(pedido_raw)

        if not pedido_norm.get("tiny_id"):
            continue

        if not pedido_norm.get("data_pedido"):
            pedido_norm["data_pedido"] = data_inicio.isoformat()

        pedidos_normalizados.append(pedido_norm)

        if buscar_itens:
            try:
                pedido_completo = obter_pedido_tiny(pedido_norm["tiny_id"])
                itens = extrair_itens_do_pedido_completo(
                    pedido_completo,
                    pedido_norm["tiny_id"],
                    pedido_norm["data_pedido"]
                )
                itens_normalizados.extend(itens)
                time.sleep(0.7)
            except Exception:
                continue

    if pedidos_normalizados:
        supabase_insert(
            "pedidos",
            pedidos_normalizados,
            upsert=True,
            on_conflict="tiny_id"
        )

    if itens_normalizados:
        limpar_itens_dos_pedidos(pedidos_normalizados)

        supabase_insert(
            "itens_pedido",
            itens_normalizados,
            upsert=False
        )

    calculado = calcular_resumo_e_ranking(
        pedidos_normalizados,
        itens_normalizados,
        data_inicio,
        data_fim
    )

    # -------------------------------------------------------
    # GRAVAR RESUMO_DIARIO — sempre, para qualquer tipo.
    # Agrupa pedidos por data e grava 1 linha por dia.
    # Isso garante que blocos históricos (/sync/tiny-periodo)
    # também populem a resumo_diario corretamente.
    # -------------------------------------------------------
    pedidos_por_dia: Dict[str, List[Dict[str, Any]]] = {}
    itens_por_dia: Dict[str, List[Dict[str, Any]]] = {}

    for p in pedidos_normalizados:
        dia = p.get("data_pedido") or data_inicio.isoformat()
        pedidos_por_dia.setdefault(dia, []).append(p)

    for it in itens_normalizados:
        dia = it.get("data_pedido") or data_inicio.isoformat()
        itens_por_dia.setdefault(dia, []).append(it)

    dias_para_gravar = set(pedidos_por_dia.keys())

    # Garante que todos os dias do período sejam gravados (dias sem venda ficam zerados)
    total_dias_periodo = (data_fim - data_inicio).days + 1
    for i in range(total_dias_periodo):
        dia_str = (data_inicio + timedelta(days=i)).isoformat()
        dias_para_gravar.add(dia_str)

    for dia_str in sorted(dias_para_gravar):
        pedidos_dia = pedidos_por_dia.get(dia_str, [])
        itens_dia = itens_por_dia.get(dia_str, [])

        fat_dia = round(sum(float(p.get("valor_total") or 0) for p in pedidos_dia), 2)
        qtd_pedidos_dia = len(pedidos_dia)
        ticket_dia = round(fat_dia / qtd_pedidos_dia, 2) if qtd_pedidos_dia else 0.0
        unidades_dia = round(sum(float(it.get("quantidade") or 0) for it in itens_dia), 2)
        produtos_diferentes = len(set(
            it.get("sku") or it.get("codigo") or it.get("produto_nome", "")
            for it in itens_dia
        ))

        resumo_diario_payload = {
            "data": dia_str,
            "data_resumo": dia_str,
            "faturamento_total": fat_dia,
            "total_pedidos": qtd_pedidos_dia,
            "ticket_medio": ticket_dia,
            "total_unidades_vendidas": unidades_dia,
            "total_produtos_diferentes": produtos_diferentes,
            "origem": "Tiny/Olist",
            "updated_at": datetime.now().isoformat()
        }

        try:
            supabase_insert(
                "resumo_diario",
                resumo_diario_payload,
                upsert=True,
                on_conflict="data_resumo"
            )
        except Exception:
            pass

    if tipo == "mes":
        resumo_mensal = {
            "ano": data_inicio.year,
            "mes": data_inicio.month,
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
            "faturamento": calculado["faturamento"],
            "total_pedidos": calculado["total_pedidos"],
            "ticket_medio": calculado["ticket_medio"],
            "updated_at": datetime.now().isoformat()
        }

        supabase_insert(
            "resumo_mensal",
            resumo_mensal,
            upsert=False
        )

    elif tipo == "ano":
        resumo_anual = {
            "ano": data_inicio.year,
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
            "faturamento": calculado["faturamento"],
            "total_pedidos": calculado["total_pedidos"],
            "ticket_medio": calculado["ticket_medio"],
            "updated_at": datetime.now().isoformat()
        }

        supabase_insert(
            "resumo_anual",
            resumo_anual,
            upsert=False
        )

    ranking_periodo_payload = []

    for item in calculado["ranking"]:
        ranking_periodo_payload.append({
            "data_inicio": data_inicio.isoformat(),
            "data_fim": data_fim.isoformat(),
            "tipo": tipo,
            "posicao": item["posicao"],
            "produto_nome": item["produto_nome"],
            "sku": item["sku"],
            "quantidade_total": item["quantidade_total"],
            "valor_total": item["valor_total"],
            "percentual_participacao": item["percentual_participacao"],
            "updated_at": datetime.now().isoformat()
        })

    if ranking_periodo_payload:
        limpar_ranking_periodo(data_inicio, data_fim, tipo)

        try:
            supabase_insert(
                "ranking_periodo",
                ranking_periodo_payload,
                upsert=False
            )
        except Exception:
            pass

    salvar_sync_log(
        tipo=tipo,
        data_inicio=data_inicio,
        data_fim=data_fim,
        status="ok",
        mensagem="Sincronização concluída.",
        total_pedidos=calculado["total_pedidos"],
        faturamento=calculado["faturamento"]
    )

    return {
        "status": "ok",
        "tipo": tipo,
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "total_pedidos": calculado["total_pedidos"],
        "faturamento": calculado["faturamento"],
        "ticket_medio": calculado["ticket_medio"],
        "top_10": calculado["ranking"][:10]
    }


# ============================================================
# MODELS
# ============================================================

class PeriodoBody(BaseModel):
    data_inicio: str
    data_fim: str


# ============================================================
# ROTAS BÁSICAS
# ============================================================

@app.get("/")
def home():
    return {
        "status": "online",
        "app": "MHM Dashboard Tiny API",
        "version": "2.0.1"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "tiny_token_ok": bool(TINY_TOKEN),
        "supabase_url_ok": bool(SUPABASE_URL),
        "supabase_key_ok": bool(SUPABASE_SERVICE_ROLE_KEY)
    }


@app.get("/rotas")
def rotas():
    return {
        "sync": [
            "/sync/tiny-dia",
            "/sync/tiny-mes",
            "/sync/tiny-ano",
            "/sync/tiny-periodo",
            "/sync/descobrir-data-inicial-tiny"
        ],
        "db": [
            "/db/resumo-diario",
            "/db/resumo-mensal",
            "/db/resumo-anual",
            "/db/dashboard-resumo",
            "/db/resumo-periodo",
            "/db/ranking-periodo",
            "/db/sync-logs"
        ],
        "configuracoes": [
            "/configuracoes/data-inicio-tiny"
        ]
    }


# ============================================================
# ROTAS SYNC TINY
# ============================================================

@app.post("/sync/tiny-dia")
def sync_tiny_dia(
    data: str = Query(..., description="Data no formato YYYY-MM-DD")
):
    data_ref = parse_data(data)
    return sincronizar_periodo(data_ref, data_ref, tipo="dia")


@app.post("/sync/tiny-mes")
def sync_tiny_mes(
    ano: int = Query(...),
    mes: int = Query(...)
):
    if mes < 1 or mes > 12:
        raise HTTPException(status_code=400, detail="Mês inválido.")

    data_inicio = date(ano, mes, 1)
    ultimo_dia = monthrange(ano, mes)[1]
    data_fim = date(ano, mes, ultimo_dia)

    return sincronizar_periodo(data_inicio, data_fim, tipo="mes")


@app.post("/sync/tiny-ano")
def sync_tiny_ano(
    ano: int = Query(...)
):
    data_inicio = date(ano, 1, 1)
    data_fim = date(ano, 12, 31)

    hoje = hoje_br()
    if data_fim > hoje:
        data_fim = hoje

    return sincronizar_periodo(data_inicio, data_fim, tipo="ano")


@app.post("/sync/tiny-periodo")
def sync_tiny_periodo(body: PeriodoBody):
    data_inicio = parse_data(body.data_inicio)
    data_fim = parse_data(body.data_fim)

    if data_fim < data_inicio:
        raise HTTPException(
            status_code=400,
            detail="data_fim não pode ser menor que data_inicio."
        )

    return sincronizar_periodo(data_inicio, data_fim, tipo="periodo")


@app.post("/sync/descobrir-data-inicial-tiny")
def descobrir_data_inicial_tiny(
    meses_voltar: int = Query(36, description="Quantidade máxima de meses para voltar procurando pedidos.")
):
    """
    Descobre a primeira data com pedido.

    Estratégia:
    1. Primeiro tenta achar no Supabase a menor data_pedido já sincronizada.
    2. Se encontrar, salva em configuracoes como data_inicio_tiny.
    3. Se não encontrar no Supabase, tenta procurar no Tiny mês a mês.
    """

    if meses_voltar < 1:
        meses_voltar = 1

    if meses_voltar > 120:
        meses_voltar = 120

    erro_supabase = None

    # ========================================================
    # 1. PRIMEIRO TENTA DESCOBRIR PELO SUPABASE
    # ========================================================

    try:
        pedidos_banco = supabase_get(
            "pedidos",
            {
                "select": "data_pedido",
                "data_pedido": "not.is.null",
                "order": "data_pedido.asc",
                "limit": "1"
            }
        )

        if pedidos_banco:
            primeira_data_banco = pedidos_banco[0].get("data_pedido")

            if primeira_data_banco:
                salvar_configuracao("data_inicio_tiny", primeira_data_banco)

                return {
                    "status": "ok",
                    "origem": "supabase",
                    "data_inicio_tiny": primeira_data_banco,
                    "mensagem": "Data inicial encontrada nos pedidos já salvos no Supabase."
                }

    except Exception as e:
        erro_supabase = str(e)

    # ========================================================
    # 2. SE NÃO ACHOU NO BANCO, PROCURA NO TINY
    # ========================================================

    hoje = hoje_br()

    meses_com_pedido = []

    ano_atual = hoje.year
    mes_atual = hoje.month

    erros_tiny = []

    for i in range(meses_voltar):
        mes_calc = mes_atual - i
        ano_calc = ano_atual

        while mes_calc <= 0:
            mes_calc += 12
            ano_calc -= 1

        data_inicio_mes = date(ano_calc, mes_calc, 1)
        ultimo_dia = monthrange(ano_calc, mes_calc)[1]
        data_fim_mes = date(ano_calc, mes_calc, ultimo_dia)

        if data_fim_mes > hoje:
            data_fim_mes = hoje

        try:
            pedidos_mes = buscar_pedidos_periodo_tiny(
                data_inicio_mes,
                data_fim_mes,
                pausa_segundos=0.5
            )

            pedidos_validos = [
                p for p in pedidos_mes
                if pedido_tem_venda_valida(p)
            ]

            if pedidos_validos:
                meses_com_pedido.append({
                    "ano": ano_calc,
                    "mes": mes_calc,
                    "data_inicio": data_inicio_mes,
                    "data_fim": data_fim_mes,
                    "total": len(pedidos_validos)
                })

        except Exception as e:
            erros_tiny.append({
                "ano": ano_calc,
                "mes": mes_calc,
                "erro": str(e)
            })

        time.sleep(0.8)

    if not meses_com_pedido:
        return {
            "status": "vazio",
            "mensagem": "Nenhum pedido encontrado no Supabase nem no Tiny dentro do período pesquisado.",
            "meses_voltar": meses_voltar,
            "data_inicio_tiny": None,
            "erro_supabase": erro_supabase,
            "erros_tiny_amostra": erros_tiny[:5]
        }

    mes_mais_antigo = sorted(
        meses_com_pedido,
        key=lambda x: x["data_inicio"]
    )[0]

    primeira_data = None

    dia_inicio = mes_mais_antigo["data_inicio"]
    dia_fim = mes_mais_antigo["data_fim"]

    dia_atual = dia_inicio

    while dia_atual <= dia_fim:
        try:
            pedidos_dia = buscar_pedidos_periodo_tiny(
                dia_atual,
                dia_atual,
                pausa_segundos=0.3
            )

            pedidos_validos = [
                p for p in pedidos_dia
                if pedido_tem_venda_valida(p)
            ]

            if pedidos_validos:
                primeira_data = dia_atual
                break

        except Exception as e:
            erros_tiny.append({
                "data": dia_atual.isoformat(),
                "erro": str(e)
            })

        dia_atual += timedelta(days=1)
        time.sleep(0.4)

    if not primeira_data:
        primeira_data = mes_mais_antigo["data_inicio"]

    salvar_configuracao("data_inicio_tiny", primeira_data.isoformat())

    return {
        "status": "ok",
        "origem": "tiny",
        "data_inicio_tiny": primeira_data.isoformat(),
        "mensagem": "Data inicial do Tiny descoberta e salva no Supabase.",
        "mes_mais_antigo_com_pedido": {
            "ano": mes_mais_antigo["ano"],
            "mes": mes_mais_antigo["mes"],
            "total_pedidos": mes_mais_antigo["total"]
        },
        "erros_tiny_amostra": erros_tiny[:5]
    }


# ============================================================
# ROTAS CONFIGURAÇÕES
# ============================================================

@app.get("/configuracoes/data-inicio-tiny")
def get_data_inicio_tiny():
    valor = buscar_configuracao("data_inicio_tiny")

    if not valor:
        return {
            "status": "vazio",
            "data_inicio_tiny": None,
            "mensagem": "Data inicial ainda não descoberta. Rode POST /sync/descobrir-data-inicial-tiny."
        }

    return {
        "status": "ok",
        "data_inicio_tiny": valor
    }


# ============================================================
# FUNÇÕES DB POR PERÍODO
# ============================================================

def buscar_pedidos_banco_periodo_corrigido(
    data_inicio: date,
    data_fim: date
) -> List[Dict[str, Any]]:
    validar_env()

    url = f"{SUPABASE_URL}/rest/v1/pedidos"

    params = [
        ("select", "*"),
        ("data_pedido", f"gte.{data_inicio.isoformat()}"),
        ("data_pedido", f"lte.{data_fim.isoformat()}"),
        ("order", "data_pedido.asc")
    ]

    response = requests.get(
        url,
        headers=supabase_headers(),
        params=params,
        timeout=60
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro ao consultar pedidos por período.",
                "status_code": response.status_code,
                "resposta": response.text
            }
        )

    return response.json()


def buscar_itens_banco_periodo_corrigido(
    data_inicio: date,
    data_fim: date
) -> List[Dict[str, Any]]:
    validar_env()

    url = f"{SUPABASE_URL}/rest/v1/itens_pedido"

    params = [
        ("select", "*"),
        ("data_pedido", f"gte.{data_inicio.isoformat()}"),
        ("data_pedido", f"lte.{data_fim.isoformat()}"),
        ("order", "data_pedido.asc")
    ]

    response = requests.get(
        url,
        headers=supabase_headers(),
        params=params,
        timeout=60
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro ao consultar itens por período.",
                "status_code": response.status_code,
                "resposta": response.text
            }
        )

    return response.json()


def calcular_resumo_periodo_banco(
    data_inicio: date,
    data_fim: date
) -> Dict[str, Any]:
    pedidos = buscar_pedidos_banco_periodo_corrigido(data_inicio, data_fim)
    itens = buscar_itens_banco_periodo_corrigido(data_inicio, data_fim)

    calculado = calcular_resumo_e_ranking(
        pedidos=pedidos,
        itens=itens,
        data_inicio=data_inicio,
        data_fim=data_fim
    )

    return {
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "faturamento": calculado["faturamento"],
        "total_pedidos": calculado["total_pedidos"],
        "ticket_medio": calculado["ticket_medio"]
    }


# ============================================================
# ROTAS DB DASHBOARD
# ============================================================

@app.get("/db/resumo-diario")
def db_resumo_diario(
    data: Optional[str] = Query(None)
):
    params = {
        "select": "*",
        "order": "data.desc"
    }

    if data:
        params["data"] = f"eq.{data}"

    return {
        "status": "ok",
        "dados": supabase_get("resumo_diario", params)
    }


@app.get("/db/resumo-mensal")
def db_resumo_mensal(
    ano: Optional[int] = Query(None),
    mes: Optional[int] = Query(None)
):
    params = {
        "select": "*",
        "order": "ano.desc,mes.desc"
    }

    if ano:
        params["ano"] = f"eq.{ano}"

    if mes:
        params["mes"] = f"eq.{mes}"

    return {
        "status": "ok",
        "dados": supabase_get("resumo_mensal", params)
    }


@app.get("/db/resumo-anual")
def db_resumo_anual(
    ano: Optional[int] = Query(None)
):
    params = {
        "select": "*",
        "order": "ano.desc"
    }

    if ano:
        params["ano"] = f"eq.{ano}"

    return {
        "status": "ok",
        "dados": supabase_get("resumo_anual", params)
    }


@app.get("/db/dashboard-resumo")
def db_dashboard_resumo():
    hoje = hoje_br()
    inicio_30 = hoje - timedelta(days=30)

    resumo_hoje = supabase_get(
        "resumo_diario",
        {
            "data": f"eq.{hoje.isoformat()}",
            "select": "*",
            "limit": "1"
        }
    )

    resumo_mes = supabase_get(
        "resumo_mensal",
        {
            "ano": f"eq.{hoje.year}",
            "mes": f"eq.{hoje.month}",
            "select": "*",
            "limit": "1"
        }
    )

    ultimos_logs = supabase_get(
        "sync_logs",
        {
            "select": "*",
            "order": "created_at.desc",
            "limit": "10"
        }
    )

    resumo_30 = calcular_resumo_periodo_banco(inicio_30, hoje)

    return {
        "status": "ok",
        "hoje": resumo_hoje[0] if resumo_hoje else None,
        "mes_atual": resumo_mes[0] if resumo_mes else None,
        "ultimos_30_dias": resumo_30,
        "sync_logs": ultimos_logs
    }


@app.get("/db/sync-logs")
def db_sync_logs(
    limit: int = Query(20)
):
    return {
        "status": "ok",
        "dados": supabase_get(
            "sync_logs",
            {
                "select": "*",
                "order": "created_at.desc",
                "limit": str(limit)
            }
        )
    }


@app.get("/db/resumo-periodo")
def db_resumo_periodo(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD")
):
    inicio = parse_data(data_inicio)
    fim = parse_data(data_fim)

    if fim < inicio:
        raise HTTPException(
            status_code=400,
            detail="data_fim não pode ser menor que data_inicio."
        )

    # -------------------------------------------------------
    # CORREÇÃO: somar resumo_diario (1 linha por dia),
    # nunca pedidos/itens individuais (bate no limite 1000).
    # -------------------------------------------------------
    registros = supabase_get(
        "resumo_diario",
        {
            "select": "data_resumo,faturamento_total,total_pedidos,total_unidades_vendidas",
            "data_resumo": f"gte.{inicio.isoformat()}",
            "order": "data_resumo.asc",
            # Supabase filtra os dois parâmetros com o mesmo nome via lista,
            # mas a helper supabase_get usa dict — usamos params como lista abaixo.
        }
    )

    # A helper supabase_get não suporta dois valores para a mesma chave.
    # Precisamos refazer a query com params como lista de tuplas.
    validar_env()
    url = f"{SUPABASE_URL}/rest/v1/resumo_diario"
    params = [
        ("select", "data_resumo,faturamento_total,total_pedidos,total_unidades_vendidas"),
        ("data_resumo", f"gte.{inicio.isoformat()}"),
        ("data_resumo", f"lte.{fim.isoformat()}"),
        ("order", "data_resumo.asc"),
        ("limit", "1000"),
    ]
    resp = requests.get(url, headers=supabase_headers(), params=params, timeout=60)
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro ao consultar resumo_diario por período.",
                "status_code": resp.status_code,
                "resposta": resp.text
            }
        )
    registros = resp.json()

    # Montar lista de dias esperados
    total_dias = (fim - inicio).days + 1
    dias_esperados = [
        (inicio + timedelta(days=i)).isoformat()
        for i in range(total_dias)
    ]
    dias_com_resumo = [r["data_resumo"] for r in registros]
    dias_faltantes = [d for d in dias_esperados if d not in dias_com_resumo]

    # Somar os campos
    faturamento_total = round(sum(float(r.get("faturamento_total") or 0) for r in registros), 2)
    total_pedidos = sum(int(r.get("total_pedidos") or 0) for r in registros)
    total_unidades = sum(float(r.get("total_unidades_vendidas") or 0) for r in registros)
    ticket_medio = round(faturamento_total / total_pedidos, 2) if total_pedidos else 0.0

    return {
        "status": "ok",
        "dados": {
            "data_inicio": inicio.isoformat(),
            "data_fim": fim.isoformat(),
            "dias_esperados": total_dias,
            "dias_com_resumo": len(dias_com_resumo),
            "dias_faltantes": dias_faltantes,
            "faturamento": faturamento_total,
            "total_pedidos": total_pedidos,
            "total_unidades_vendidas": round(total_unidades, 2),
            "ticket_medio": ticket_medio,
            "resumos_por_dia": registros
        }
    }


@app.get("/db/ranking-periodo")
def db_ranking_periodo(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD"),
    limite: int = Query(10)
):
    inicio = parse_data(data_inicio)
    fim = parse_data(data_fim)

    if fim < inicio:
        raise HTTPException(
            status_code=400,
            detail="data_fim não pode ser menor que data_inicio."
        )

    pedidos = buscar_pedidos_banco_periodo_corrigido(inicio, fim)
    itens = buscar_itens_banco_periodo_corrigido(inicio, fim)

    calculado = calcular_resumo_e_ranking(
        pedidos=pedidos,
        itens=itens,
        data_inicio=inicio,
        data_fim=fim
    )

    return {
        "status": "ok",
        "data_inicio": inicio.isoformat(),
        "data_fim": fim.isoformat(),
        "limite": limite,
        "dados": calculado["ranking"][:limite]
    }

# ============================================================
# ESTOQUE — busca produtos e estoque do Tiny
# ============================================================

def buscar_produtos_tiny_pagina(pagina: int = 1) -> Dict[str, Any]:
    return tiny_get(
        "produtos.pesquisa.php",
        {
            "pagina": pagina,
            "situacao": "A"  # apenas ativos
        }
    )

def buscar_todos_produtos_tiny() -> List[Dict[str, Any]]:
    todos = []
    pagina = 1

    while True:
        resposta = buscar_produtos_tiny_pagina(pagina)
        retorno = resposta.get("retorno", {})
        produtos_raw = retorno.get("produtos", [])

        for item in produtos_raw:
            produto = item.get("produto", item)
            todos.append(produto)

        numero_paginas = int(retorno.get("numero_paginas", 1) or 1)
        if pagina >= numero_paginas:
            break

        pagina += 1
        time.sleep(0.5)

    return todos

def obter_estoque_produto_tiny(id_produto: str) -> float:
    try:
        resposta = tiny_get(
            "produto.obter.php",
            {"id": id_produto}
        )
        retorno = resposta.get("retorno", {})
        produto = retorno.get("produto", {})
        saldo = produto.get("estoque", {})
        if isinstance(saldo, dict):
            return dinheiro_para_float(saldo.get("saldo_fisico_total", 0))
        return dinheiro_para_float(saldo or 0)
    except Exception:
        return 0.0

def salvar_produtos_supabase(produtos: List[Dict[str, Any]]):
    if not produtos:
        return
    supabase_insert(
        "produtos",
        produtos,
        upsert=True,
        on_conflict="tiny_id"
    )

@app.post("/sync/estoque")
def sync_estoque(pagina_inicio: int = Query(1, description="Página inicial"), pagina_fim: int = Query(5, description="Página final (máx 5 por vez)")):
    """
    Busca produtos do Tiny em lotes de páginas e salva no Supabase.
    Chame várias vezes incrementando pagina_inicio para cobrir todos os produtos.
    Exemplo: pagina_inicio=1&pagina_fim=5, depois 6&10, depois 11&15...
    """
    validar_env()

    produtos_normalizados = []

    for pagina in range(pagina_inicio, pagina_fim + 1):
        try:
            resposta = buscar_produtos_tiny_pagina(pagina)
            retorno = resposta.get("retorno", {})
            produtos_raw = retorno.get("produtos", [])

            if not produtos_raw:
                break

            for item in produtos_raw:
                p = item.get("produto", item)
                tiny_id = safe_str(p.get("id") or p.get("codigo") or "")
                if not tiny_id:
                    continue

                estoque_atual = dinheiro_para_float(
                    p.get("saldo_fisico_total")
                    or p.get("estoque_atual")
                    or p.get("saldo")
                    or 0
                )

                produtos_normalizados.append({
                    "tiny_id": tiny_id,
                    "sku": safe_str(p.get("codigo") or p.get("id") or ""),
                    "nome": safe_str(p.get("nome") or p.get("descricao") or ""),
                    "unidade": safe_str(p.get("unidade") or "un"),
                    "preco": dinheiro_para_float(p.get("preco") or 0),
                    "estoque_atual": estoque_atual,
                    "situacao": safe_str(p.get("situacao") or "A"),
                    "updated_at": datetime.now().isoformat()
                })

            numero_paginas = int(retorno.get("numero_paginas", 1) or 1)
            if pagina >= numero_paginas:
                break

            time.sleep(0.5)

        except Exception as e:
            return {
                "status": "erro",
                "pagina_com_erro": pagina,
                "erro": str(e),
                "salvos_antes": len(produtos_normalizados)
            }

    if produtos_normalizados:
        salvar_produtos_supabase(produtos_normalizados)

    return {
        "status": "ok",
        "paginas": f"{pagina_inicio} a {pagina_fim}",
        "total_produtos": len(produtos_normalizados),
        "mensagem": f"{len(produtos_normalizados)} produtos salvos. Se houver mais, rode a próxima página."
    }


@app.get("/sync/estoque-agora")
def sync_estoque_agora_get(pagina_inicio: int = Query(1), pagina_fim: int = Query(5)):
    """Rota GET para rodar sync de estoque direto pelo navegador em lotes."""
    return sync_estoque(pagina_inicio=pagina_inicio, pagina_fim=pagina_fim)


@app.get("/estoque/alertas")
def get_estoque_alertas():
    """
    Retorna produtos do Supabase que estão abaixo do estoque mínimo configurado.
    Cruza tabela produtos com estoque_minimo_config.
    """
    validar_env()

    # Busca produtos
    produtos = supabase_get(
        "produtos",
        {
            "select": "tiny_id,sku,nome,estoque_atual,unidade,preco",
            "situacao": "eq.A",
            "order": "nome.asc",
            "limit": "1000"
        }
    )

    # Busca configurações de estoque mínimo
    try:
        configs = supabase_get(
            "estoque_minimo_config",
            {
                "select": "*",
                "limit": "1000"
            }
        )
        config_map = {c["sku"]: c for c in configs}
    except Exception:
        config_map = {}

    alertas = []
    todos = []

    for p in produtos:
        sku = p.get("sku") or p.get("tiny_id") or ""
        config = config_map.get(sku, {})

        estoque_atual = float(p.get("estoque_atual") or 0)
        estoque_minimo = float(config.get("estoque_minimo") or 0)
        quantidade_sugerida = float(config.get("quantidade_sugerida_compra") or 0)
        fornecedor = config.get("fornecedor_principal") or ""
        whatsapp = config.get("whatsapp_fornecedor") or ""

        # Calcula status
        if estoque_atual <= 0:
            status = "Urgente"
        elif estoque_minimo > 0 and estoque_atual < estoque_minimo:
            status = "Comprar"
        elif estoque_minimo > 0 and estoque_atual <= estoque_minimo * 1.2:
            status = "Atenção"
        else:
            status = "Ok"

        item = {
            "sku": sku,
            "produto": p.get("nome") or "",
            "estoque_atual": estoque_atual,
            "estoque_minimo": estoque_minimo,
            "quantidade_sugerida_compra": quantidade_sugerida,
            "fornecedor_principal": fornecedor,
            "whatsapp_fornecedor": whatsapp,
            "status": status,
            "preco": float(p.get("preco") or 0),
            "unidade": p.get("unidade") or "un"
        }

        todos.append(item)
        if status != "Ok":
            alertas.append(item)

    # Ordena: Urgente > Comprar > Atenção
    ordem = {"Urgente": 0, "Comprar": 1, "Atenção": 2, "Ok": 3}
    alertas.sort(key=lambda x: ordem.get(x["status"], 99))

    return {
        "status": "ok",
        "total_produtos": len(todos),
        "total_alertas": len(alertas),
        "alertas": alertas
    }


@app.get("/estoque/produtos")
def get_estoque_produtos(
    busca: Optional[str] = Query(None, description="Filtrar por nome ou SKU"),
    apenas_alertas: bool = Query(False)
):
    """
    Lista todos os produtos com estoque atual.
    """
    validar_env()

    params: Dict[str, str] = {
        "select": "tiny_id,sku,nome,estoque_atual,unidade,preco,situacao",
        "situacao": "eq.A",
        "order": "nome.asc",
        "limit": "1000"
    }

    produtos = supabase_get("produtos", params)

    # Filtro de busca
    if busca:
        busca_lower = busca.lower()
        produtos = [
            p for p in produtos
            if busca_lower in (p.get("nome") or "").lower()
            or busca_lower in (p.get("sku") or "").lower()
        ]

    # Busca configs de estoque mínimo
    try:
        configs = supabase_get("estoque_minimo_config", {"select": "*", "limit": "1000"})
        config_map = {c["sku"]: c for c in configs}
    except Exception:
        config_map = {}

    resultado = []
    for p in produtos:
        sku = p.get("sku") or p.get("tiny_id") or ""
        config = config_map.get(sku, {})
        estoque_atual = float(p.get("estoque_atual") or 0)
        estoque_minimo = float(config.get("estoque_minimo") or 0)

        if estoque_atual <= 0:
            status = "Urgente"
        elif estoque_minimo > 0 and estoque_atual < estoque_minimo:
            status = "Comprar"
        elif estoque_minimo > 0 and estoque_atual <= estoque_minimo * 1.2:
            status = "Atenção"
        else:
            status = "Ok"

        if apenas_alertas and status == "Ok":
            continue

        resultado.append({
            "sku": sku,
            "produto": p.get("nome") or "",
            "estoque_atual": estoque_atual,
            "estoque_minimo": estoque_minimo,
            "quantidade_sugerida_compra": float(config.get("quantidade_sugerida_compra") or 0),
            "fornecedor_principal": config.get("fornecedor_principal") or "",
            "whatsapp_fornecedor": config.get("whatsapp_fornecedor") or "",
            "status": status,
            "preco": float(p.get("preco") or 0),
            "unidade": p.get("unidade") or "un"
        })

    return {
        "status": "ok",
        "total": len(resultado),
        "produtos": resultado
    }


@app.post("/estoque/config")
def salvar_config_estoque(body: Dict[str, Any]):
    """
    Salva ou atualiza configuração de estoque mínimo para um produto.
    Body: { sku, estoque_minimo, quantidade_sugerida_compra, fornecedor_principal, whatsapp_fornecedor }
    """
    validar_env()

    sku = body.get("sku")
    if not sku:
        raise HTTPException(status_code=400, detail="SKU é obrigatório.")

    payload = {
        "sku": sku,
        "estoque_minimo": float(body.get("estoque_minimo") or 0),
        "quantidade_sugerida_compra": float(body.get("quantidade_sugerida_compra") or 0),
        "fornecedor_principal": body.get("fornecedor_principal") or "",
        "whatsapp_fornecedor": body.get("whatsapp_fornecedor") or "",
        "updated_at": datetime.now().isoformat()
    }

    supabase_insert(
        "estoque_minimo_config",
        payload,
        upsert=True,
        on_conflict="sku"
    )

    return {"status": "ok", "mensagem": "Configuração salva com sucesso.", "sku": sku}






# ============================================================
# AGENDADOR AUTOMÁTICO — sync a cada 1h no próprio Railway
# ============================================================
import threading

def job_sync_diario():
    """
    Roda o sync do dia atual automaticamente a cada 1h.
    Só executa entre 09:00 e 17:40 (horário de Brasília).
    Executa em background sem travar a API.
    """
    while True:
        try:
            agora = datetime.now()
            hora_atual = agora.hour
            minuto_atual = agora.minute
            hora_minuto = hora_atual * 60 + minuto_atual  # em minutos

            # Janela permitida: 09:00 (540min) até 17:40 (1060min)
            dentro_horario = 540 <= hora_minuto <= 1060

            if dentro_horario:
                hoje = hoje_br()
                print(f"[AUTO-SYNC] {agora.strftime('%H:%M')} - Iniciando sync do dia {hoje.isoformat()}...")
                sincronizar_periodo(hoje, hoje, tipo="dia")
                print(f"[AUTO-SYNC] Sync concluído: {hoje.isoformat()}")
            else:
                print(f"[AUTO-SYNC] {agora.strftime('%H:%M')} - Fora do horário comercial (09:00-17:40). Pulando sync.")

        except Exception as e:
            print(f"[AUTO-SYNC] Erro: {e}")

        # Aguarda 1 hora antes do próximo sync
        time.sleep(60 * 60)

def iniciar_agendador():
    thread = threading.Thread(target=job_sync_diario, daemon=True)
    thread.start()
    print("[AUTO-SYNC] Agendador iniciado — sync automático a cada 1h.")

# Inicia o agendador quando o servidor sobe
iniciar_agendador()
