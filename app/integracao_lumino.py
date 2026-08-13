"""
Integração do Radar Tributário na API FastAPI do LÚMINO (MHM Dashboard Tiny API).

Arquivo adaptado para o projeto atual:
- usa o scraper.py do Radar Tributário;
- cruza alertas com a tabela real public.produtos_tributarios do Supabase;
- suporta filial sp, mg e all;
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
CACHE_FILE = DATA_DIR / "noticias.json"
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


def _load_cache() -> dict:
    if not CACHE_FILE.exists():
        return DEFAULT_CACHE.copy()

    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else DEFAULT_CACHE.copy()
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Não foi possível ler o cache de notícias: %s", exc)
        return DEFAULT_CACHE.copy()


def _save_cache(data: dict) -> None:
    tmp_file = CACHE_FILE.with_suffix(".json.tmp")
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_file.replace(CACHE_FILE)


def _coletar_e_salvar(dias: int = 7) -> None:
    try:
        res = scraper.run(days=dias)
        _save_cache(res)
        log.info("Cache do Radar Tributário atualizado: %d notícias", res.get("total", 0))
    except Exception:
        log.exception("Falha na coleta do Radar Tributário")


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


def _buscar_ncms_usados(filial: str) -> Set[str]:
    """
    Busca os NCMs reais da tabela public.produtos_tributarios.
    Faz paginação para não depender do limite padrão do Supabase.
    """
    filial = _normalizar_filial(filial)
    ncms: Set[str] = set()
    offset = 0
    page_size = 1000

    while True:
        params: Dict[str, Any] = {
            "select": "ncm",
            "ncm": "not.is.null",
            "limit": page_size,
            "offset": offset,
        }

        if filial != "all":
            params["filial"] = f"eq.{filial}"

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
                    "erro": "Erro ao consultar NCMs dos produtos tributários.",
                    "status_code": response.status_code,
                    "resposta": response.text[:1000],
                },
            )

        rows: List[Dict[str, Any]] = response.json()

        for row in rows:
            ncm = _normalizar_ncm(row.get("ncm"))
            if len(ncm) == 8:
                ncms.add(ncm)

        if len(rows) < page_size:
            break

        offset += len(rows)

    return ncms


@router.get("/tributario/noticias")
def listar_noticias(
    relevancia: str = Query(None, description="filtro: alta|media|neutra"),
    limit: int = Query(20, ge=1, le=100),
):
    data = _load_cache()
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
def noticias_recentes(n: int = Query(10, ge=1, le=50)):
    data = _load_cache()
    return {
        "atualizado_em": data.get("gerado_em"),
        "noticias": data.get("noticias", [])[:n],
    }


@router.post("/tributario/noticias/refresh")
def atualizar_noticias(
    background_tasks: BackgroundTasks,
    dias: int = Query(7, ge=1, le=30),
):
    """
    Dispara a coleta em background e devolve imediatamente o cache atual.
    """
    background_tasks.add_task(_coletar_e_salvar, dias)
    cache = _load_cache()

    return {
        "status": "coleta_iniciada",
        "dias": dias,
        "cache_atualizado_em": cache.get("gerado_em"),
        "cache_atual": cache.get("noticias", [])[:5],
    }


@router.get("/tributario/ncms-alertas")
def alertas_ncm(
    filial: str = Query("sp", description="sp, mg ou all"),
    limit: int = Query(100, ge=1, le=500),
):
    """
    Cruza os NCMs citados nas notícias com os NCMs realmente usados
    pelos produtos tributários da filial escolhida.
    """
    filial_normalizada = _normalizar_filial(filial)
    ncms_usados = _buscar_ncms_usados(filial_normalizada)

    data = _load_cache()
    alertas = []

    for noticia in data.get("noticias", []):
        ncms_noticia = {
            _normalizar_ncm(ncm)
            for ncm in noticia.get("ncms_afetados", [])
            if len(_normalizar_ncm(ncm)) == 8
        }

        correspondencias = sorted(ncms_noticia & ncms_usados)

        if correspondencias:
            item = dict(noticia)
            item["ncms_em_uso_afetados"] = correspondencias
            alertas.append(item)

    return {
        "filial": filial_normalizada,
        "atualizado_em": data.get("gerado_em"),
        "ncms_monitorados": len(ncms_usados),
        "total_alertas": len(alertas),
        "alertas": alertas[:limit],
    }
