from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

from .schemas import AtualizarProdutoTributarioBody
from .service import (
    listar_lotes,
    listar_produtos,
    normalizar_filial,
    obter_resumo,
    sincronizar_produtos,
    supabase_patch,
)


router = APIRouter(prefix="/tributario", tags=["Tributário"])


@router.post("/sync-produtos")
def sync_produtos_tributarios(
    filial: str = Query("sp", description="sp ou mg"),
    tamanho_lote: int = Query(50, ge=1, le=500),
    detalhar: bool = Query(True, description="Consulta produto.obter.php para enriquecer os dados."),
    limite_detalhes: int = Query(50, ge=0, le=500),
    max_paginas: Optional[int] = Query(None, ge=1),
):
    return sincronizar_produtos(
        filial=filial,
        tamanho_lote=tamanho_lote,
        detalhar=detalhar,
        limite_detalhes=limite_detalhes,
        max_paginas=max_paginas,
    )


@router.get("/resumo")
def resumo_tributario(
    filial: str = Query("sp", description="sp, mg ou all"),
):
    return obter_resumo(filial)


@router.get("/produtos")
def produtos_tributarios(
    filial: str = Query("sp", description="sp, mg ou all"),
    status_tributario: Optional[str] = Query(None),
    status_ia: Optional[str] = Query(None),
    lote: Optional[int] = Query(None, ge=1),
    busca: Optional[str] = Query(None),
    limite: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    dados = listar_produtos(
        filial=filial,
        status_tributario=status_tributario,
        status_ia=status_ia,
        lote=lote,
        busca=busca,
        limite=limite,
        offset=offset,
    )
    return {
        "status": "ok",
        "filial": normalizar_filial(filial),
        "total_retornado": len(dados),
        "limite": limite,
        "offset": offset,
        "dados": dados,
    }


@router.get("/lotes")
def lotes_tributarios(
    filial: str = Query("sp", description="sp, mg ou all"),
):
    dados = listar_lotes(filial)
    return {
        "status": "ok",
        "filial": normalizar_filial(filial),
        "total_lotes": len(dados),
        "dados": dados,
    }


@router.get("/lote/{numero}")
def produtos_do_lote(
    numero: int,
    filial: str = Query("sp", description="sp ou mg"),
    limite: int = Query(500, ge=1, le=1000),
):
    if numero < 1:
        raise HTTPException(status_code=400, detail="Número do lote inválido.")
    dados = listar_produtos(filial, None, None, numero, None, limite, 0)
    return {
        "status": "ok",
        "filial": normalizar_filial(filial),
        "lote": numero,
        "total_produtos": len(dados),
        "dados": dados,
    }


@router.patch("/produto/{produto_id}")
def atualizar_produto_tributario(
    produto_id: str,
    body: AtualizarProdutoTributarioBody,
):
    dados: Dict[str, Any] = body.model_dump(exclude_none=True)
    if not dados:
        raise HTTPException(status_code=400, detail="Nenhum campo foi informado.")
    dados["updated_at"] = datetime.now().isoformat()
    atualizado = supabase_patch(produto_id, dados)
    if not atualizado:
        raise HTTPException(status_code=404, detail="Produto tributário não encontrado.")
    return {"status": "ok", "produto": atualizado[0]}
