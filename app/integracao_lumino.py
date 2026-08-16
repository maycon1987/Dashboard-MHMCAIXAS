"""
Integração do Radar Tributário na API FastAPI do LÚMINO (MHM Dashboard Tiny API).

Arquivo adaptado para o projeto atual:
- usa o scraper.py do Radar Tributário;
- cruza alertas com a tabela real public.produtos_tributarios do Supabase;
- suporta múltiplas empresas por empresa_id e filtro opcional de filial;
- não inicia APScheduler automaticamente;
- mantém cache local noticias.json nesta primeira etapa.

Registro no main.py:
    from integracao_lumino import router as noticias_router
    app.include_router(noticias_router)
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Set

import requests
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

import scraper

router = APIRouter(tags=["Tributário"])
log = logging.getLogger("lumino-noticias")

DATA_DIR = Path(__file__).parent
DEFAULT_CACHE = {
    "gerado_em": "",
    "periodo_dias": 0,
    "total": 0,
    "por_relevancia": {},
    "noticias": [],
}

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
)
TABELA_PRODUTOS = "produtos_tributarios"
TABELA_CONFIG_TRIBUTARIA = "empresas_config_tributaria"



def _cache_file(empresa_id: str) -> Path:
    """
    Cache separado por empresa para evitar que um cliente sobrescreva
    as notícias de outro cliente no ambiente SaaS.
    """
    seguro = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(empresa_id or "default").strip())
    return DATA_DIR / f"noticias_{seguro}.json"


def _load_cache(empresa_id: str = "mhm_sp") -> dict:
    cache_file = _cache_file(empresa_id)

    if not cache_file.exists():
        base = DEFAULT_CACHE.copy()
        base["empresa_id"] = empresa_id
        return base

    try:
        with open(cache_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data

        base = DEFAULT_CACHE.copy()
        base["empresa_id"] = empresa_id
        return base
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Não foi possível ler o cache de notícias da empresa %s: %s", empresa_id, exc)
        base = DEFAULT_CACHE.copy()
        base["empresa_id"] = empresa_id
        return base


def _save_cache(data: dict, empresa_id: str) -> None:
    cache_file = _cache_file(empresa_id)
    tmp_file = cache_file.with_suffix(".json.tmp")

    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    tmp_file.replace(cache_file)


def _coletar_e_salvar(dias: int = 7, empresa_id: str = "mhm_sp") -> None:
    """Atualiza o Radar usando a configuração tributária da empresa solicitada."""
    try:
        config = _buscar_config_empresa(empresa_id)

        mapa_ncms: Dict[str, Dict[str, Any]] = {}
        if bool(config.get("usa_ncm")):
            mapa_ncms = _buscar_ncms_usados(empresa_id=empresa_id)

        ncm_watchlist = sorted(mapa_ncms.keys())

        log.info(
            "Radar Tributário: empresa=%s | regime=%s | UF=%s | tipo=%s | %d NCMs monitorados.",
            empresa_id,
            config.get("regime_tributario"),
            config.get("uf_origem"),
            config.get("tipo_negocio"),
            len(ncm_watchlist),
        )

        res = scraper.run(
            days=dias,
            ncm_watchlist=ncm_watchlist,
            ncm_days=30,
        )

        res["empresa_id"] = empresa_id
        res["nome_empresa"] = config.get("nome_empresa")
        res["regime_tributario"] = config.get("regime_tributario")
        res["uf_origem"] = config.get("uf_origem")
        res["tipo_negocio"] = config.get("tipo_negocio")
        res["ncms_monitorados"] = len(ncm_watchlist)

        _save_cache(res, empresa_id)

        log.info(
            "Cache do Radar atualizado: empresa=%s | %d notícias | %d NCMs monitorados",
            empresa_id,
            res.get("total", 0),
            len(ncm_watchlist),
        )
    except Exception:
        log.exception("Falha na coleta do Radar Tributário para empresa=%s", empresa_id)


def _buscar_config_empresa(empresa_id: str) -> Dict[str, Any]:
    """Carrega a configuração tributária ativa de uma empresa do LÚMINO."""
    empresa_id = str(empresa_id or "").strip()
    if not empresa_id:
        raise HTTPException(status_code=422, detail="empresa_id é obrigatório")

    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/{TABELA_CONFIG_TRIBUTARIA}",
        headers=_supabase_headers(),
        params={
            "select": "*",
            "empresa_id": f"eq.{empresa_id}",
            "ativo": "eq.true",
            "limit": 1,
        },
        timeout=30,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro ao consultar configuração tributária da empresa.",
                "status_code": response.status_code,
                "resposta": response.text[:1000],
            },
        )

    rows = response.json() if response.text.strip() else []
    if not isinstance(rows, list) or not rows:
        raise HTTPException(
            status_code=404,
            detail=f"Configuração tributária ativa não encontrada para empresa_id={empresa_id}",
        )

    return rows[0]


def _filial_por_empresa(config: Dict[str, Any]) -> str:
    """
    Compatibilidade legada com telas antigas que ainda trabalham com filial.
    O Radar SaaS não depende mais desta função para localizar produtos.
    """
    empresa_id = str(config.get("empresa_id") or "").strip().lower()
    if empresa_id == "mhm_sp":
        return "sp"
    if empresa_id == "mhm_mg":
        return "mg"

    filial = str(config.get("filial") or "").strip().lower()
    if filial in {"sp", "mg", "all"}:
        return filial

    return "all"


def _normalizar_filial(filial: str) -> str:
    valor = (filial or "sp").strip().lower()

    aliases = {
        "campinas": "sp",
        "minas": "mg",
        "minas_gerais": "mg",
        "pouso_alegre": "mg",
        "todos": "all",
    }
    valor = aliases.get(valor, valor)

    if valor not in {"sp", "mg", "all"}:
        raise HTTPException(status_code=422, detail="filial deve ser sp, mg ou all")

    return valor


def _normalizar_ncm(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _supabase_headers() -> Dict[str, str]:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY não configurados no Railway.",
        )

    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Accept": "application/json",
    }




def _buscar_ncms_usados(
    empresa_id: str,
    filial: str | None = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Busca os NCMs reais de uma empresa em public.produtos_tributarios.

    empresa_id é a identidade principal do cliente no SaaS.
    filial é opcional e serve apenas para recortar uma unidade interna.
    """
    empresa_id = str(empresa_id or "").strip()
    if not empresa_id:
        raise HTTPException(status_code=422, detail="empresa_id é obrigatório")

    agrupado: Dict[str, Dict[str, Any]] = {}
    offset = 0
    page_size = 1000

    while True:
        params: Dict[str, Any] = {
            "select": "empresa_id,filial,ncm,descricao,sku,codigo,ativo",
            "empresa_id": f"eq.{empresa_id}",
            "ncm": "not.is.null",
            "limit": page_size,
            "offset": offset,
        }

        if filial:
            filial_normalizada = _normalizar_filial(filial)
            if filial_normalizada != "all":
                params["filial"] = f"eq.{filial_normalizada}"

        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/{TABELA_PRODUTOS}",
            headers=_supabase_headers(),
            params=params,
            timeout=90,
        )

        if response.status_code >= 400:
            raise HTTPException(
                status_code=500,
                detail={
                    "erro": "Erro ao consultar NCMs dos produtos tributários da empresa.",
                    "empresa_id": empresa_id,
                    "status_code": response.status_code,
                    "resposta": response.text[:1000],
                },
            )

        rows: List[Dict[str, Any]] = response.json()

        for row in rows:
            ncm = _normalizar_ncm(row.get("ncm"))
            if len(ncm) != 8:
                continue

            item = agrupado.setdefault(
                ncm,
                {
                    "ncm": ncm,
                    "quantidade_produtos": 0,
                    "produtos_ativos": 0,
                    "filiais_encontradas": set(),
                    "exemplos_produtos": [],
                },
            )

            item["quantidade_produtos"] += 1

            if row.get("ativo") is not False:
                item["produtos_ativos"] += 1

            if row.get("filial"):
                item["filiais_encontradas"].add(str(row.get("filial")))

            if len(item["exemplos_produtos"]) < 5:
                item["exemplos_produtos"].append(
                    {
                        "descricao": row.get("descricao"),
                        "sku": row.get("sku"),
                        "codigo": row.get("codigo"),
                        "filial": row.get("filial"),
                    }
                )

        if len(rows) < page_size:
            break

        offset += len(rows)

    # set não é serializável em JSON.
    for item in agrupado.values():
        item["filiais_encontradas"] = sorted(item["filiais_encontradas"])

    return agrupado


def _buscar_regras_por_ncms(ncms: Set[str]) -> Dict[str, int]:
    """
    Conta quantas regras tributárias ativas existem para cada NCM.
    """
    if not ncms:
        return {}

    response = requests.get(
        f"{SUPABASE_URL}/rest/v1/tributacao_ncm",
        headers=_supabase_headers(),
        params={
            "select": "ncm,id",
            "ativo": "eq.true",
            "ncm": f"in.({','.join(sorted(ncms))})",
            "limit": 10000,
        },
        timeout=90,
    )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro ao consultar regras tributárias por NCM.",
                "status_code": response.status_code,
                "resposta": response.text[:1000],
            },
        )

    resultado: Dict[str, int] = {}
    rows = response.json() if response.text.strip() else []
    for row in rows if isinstance(rows, list) else []:
        ncm = _normalizar_ncm(row.get("ncm"))
        if len(ncm) == 8:
            resultado[ncm] = resultado.get(ncm, 0) + 1

    return resultado


def _acao_recomendada_alerta(relevancia: str, total_produtos_afetados: int, impacto_ncms: List[Dict[str, Any]]) -> Dict[str, str]:
    """Gera orientação operacional conservadora; nunca altera regra fiscal automaticamente."""
    sem_regra = sum(1 for item in impacto_ncms if not bool(item.get("regra_cadastrada")))
    relevancia = str(relevancia or "").strip().lower()

    if sem_regra > 0:
        return {
            "prioridade": "alta",
            "status": "requer_analise",
            "acao": f"Revisar {sem_regra} NCM(s) afetado(s) sem regra tributária ativa cadastrada antes de qualquer alteração fiscal.",
        }
    if relevancia == "alta" and total_produtos_afetados > 0:
        return {
            "prioridade": "alta",
            "status": "requer_analise",
            "acao": "Revisar a publicação e validar as regras tributárias dos produtos afetados antes da vigência ou de alterar cadastro/emissão fiscal.",
        }
    return {
        "prioridade": "media",
        "status": "monitorar",
        "acao": "Monitorar a publicação e conferir se as regras tributárias cadastradas continuam válidas para os produtos afetados.",
    }


@router.get("/tributario/config")
def obter_config_tributaria(
    empresa_id: str = Query("mhm_sp", description="ID da empresa no LÚMINO"),
):
    """Retorna a configuração tributária ativa usada pelo Radar."""
    return _buscar_config_empresa(empresa_id)


@router.get("/tributario/noticias")
def listar_noticias(
    empresa_id: str = Query("mhm_sp", description="ID da empresa no LÚMINO"),
    relevancia: str = Query(None, description="filtro: alta|media|neutra"),
    limit: int = Query(20, ge=1, le=100),
):
    _buscar_config_empresa(empresa_id)
    data = _load_cache(empresa_id)
    noticias = data.get("noticias", [])

    if relevancia:
        relevancia = relevancia.strip().lower()
        if relevancia not in {"alta", "media", "neutra"}:
            raise HTTPException(
                status_code=422,
                detail="relevancia deve ser alta, media ou neutra",
            )
        noticias = [
            n for n in noticias
            if str(n.get("relevancia", "")).lower() == relevancia
        ]

    return {
        "atualizado_em": data.get("gerado_em"),
        "total": len(noticias),
        "noticias": noticias[:limit],
    }


@router.get("/tributario/noticias/recentes")
def noticias_recentes(
    empresa_id: str = Query("mhm_sp", description="ID da empresa no LÚMINO"),
    n: int = Query(10, ge=1, le=50),
):
    _buscar_config_empresa(empresa_id)
    data = _load_cache(empresa_id)
    return {
        "atualizado_em": data.get("gerado_em"),
        "noticias": data.get("noticias", [])[:n],
    }


@router.post("/tributario/noticias/refresh")
def atualizar_noticias(
    background_tasks: BackgroundTasks,
    dias: int = Query(7, ge=1, le=30),
    empresa_id: str = Query("mhm_sp", description="ID da empresa no LÚMINO"),
):
    """
    Dispara a coleta em background e devolve imediatamente o cache atual.
    """
    config = _buscar_config_empresa(empresa_id)
    background_tasks.add_task(_coletar_e_salvar, dias, empresa_id)
    cache = _load_cache(empresa_id)

    return {
        "status": "coleta_iniciada",
        "dias": dias,
        "empresa_id": empresa_id,
        "nome_empresa": config.get("nome_empresa"),
        "regime_tributario": config.get("regime_tributario"),
        "uf_origem": config.get("uf_origem"),
        "tipo_negocio": config.get("tipo_negocio"),
        "ncms_monitorados_cache": cache.get("ncms_monitorados", 0),
        "cache_atualizado_em": cache.get("gerado_em"),
        "cache_atual": cache.get("noticias", [])[:5],
    }


@router.get("/tributario/ncms-alertas")
def alertas_ncm(
    empresa_id: str = Query("mhm_sp", description="ID da empresa no LÚMINO"),
    filial: str | None = Query(None, description="Opcional: filtrar unidade sp, mg ou all"),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Cruza NCMs citados nas notícias com produtos da empresa.
    empresa_id é a identidade principal; filial é apenas um filtro opcional.
    """
    config = _buscar_config_empresa(empresa_id)

    mapa_ncms = _buscar_ncms_usados(
        empresa_id=empresa_id,
        filial=filial,
    )
    ncms_usados = set(mapa_ncms.keys())
    regras_por_ncm = _buscar_regras_por_ncms(ncms_usados)

    data = _load_cache(empresa_id)
    alertas = []

    for noticia in data.get("noticias", []):
        ncms_noticia = {
            _normalizar_ncm(ncm)
            for ncm in noticia.get("ncms_afetados", [])
            if len(_normalizar_ncm(ncm)) == 8
        }

        correspondencias = sorted(ncms_noticia & ncms_usados)

        if not correspondencias:
            continue

        impacto_ncms = []
        total_produtos_afetados = 0

        for ncm in correspondencias:
            dados_ncm = mapa_ncms[ncm]
            qtd_produtos = int(dados_ncm.get("produtos_ativos") or 0)
            total_produtos_afetados += qtd_produtos

            regra_cadastrada = regras_por_ncm.get(ncm, 0) > 0

            impacto_ncms.append(
                {
                    "ncm": ncm,
                    "produtos_afetados": qtd_produtos,
                    "regra_cadastrada": regra_cadastrada,
                    "quantidade_regras_ativas": regras_por_ncm.get(ncm, 0),
                    "status_regra": "cadastrada" if regra_cadastrada else "sem_regra",
                    "filiais_encontradas": dados_ncm.get("filiais_encontradas", []),
                    "exemplos_produtos": dados_ncm.get("exemplos_produtos", []),
                }
            )

        item = dict(noticia)
        item["empresa_id"] = empresa_id
        item["nome_empresa"] = config.get("nome_empresa")
        item["filial_filtro"] = filial
        item["ncms_em_uso_afetados"] = correspondencias
        item["total_produtos_afetados"] = total_produtos_afetados
        item["impacto_ncms"] = impacto_ncms

        recomendacao = _acao_recomendada_alerta(
            item.get("relevancia", ""),
            total_produtos_afetados,
            impacto_ncms,
        )

        item["prioridade"] = recomendacao["prioridade"]
        item["status_acao"] = recomendacao["status"]
        item["acao_recomendada"] = recomendacao["acao"]

        alertas.append(item)

    peso_relevancia = {"alta": 3, "media": 2, "neutra": 1}
    alertas.sort(
        key=lambda item: (
            peso_relevancia.get(str(item.get("relevancia", "")).lower(), 0),
            int(item.get("total_produtos_afetados") or 0),
        ),
        reverse=True,
    )

    return {
        "empresa_id": empresa_id,
        "nome_empresa": config.get("nome_empresa"),
        "filial_filtro": filial,
        "atualizado_em": data.get("gerado_em"),
        "ncms_monitorados": len(ncms_usados),
        "total_alertas": len(alertas),
        "alertas": alertas[:limit],
    }

