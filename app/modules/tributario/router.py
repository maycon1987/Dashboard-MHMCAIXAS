from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from . import service
from .schemas import (
    ProdutoTributarioAtualizacao,
    RegraTributariaAtualizacao,
    RegraTributariaCriacao,
)

router = APIRouter(prefix="/tributario", tags=["Tributário"])


def model_to_dict(model: Any, exclude_none: bool = True) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_none=exclude_none, mode="json")
    return model.dict(exclude_none=exclude_none)


# ============================================================
# PRODUTOS TRIBUTÁRIOS / TINY
# ============================================================

@router.post("/sync-produtos")
def sync_produtos(
    filial: Optional[str] = Query(None, description="sp ou mg"),
    tamanho_lote: int = Query(50, ge=1, le=500),
    detalhar: bool = Query(True),
    limite_detalhes: int = Query(50, ge=0),
    max_paginas: Optional[int] = Query(None, ge=1),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(
        filial, usuario, permitir_all=False
    )
    return service.sincronizar_produtos(
        filial=filial_resolvida,
        tamanho_lote=tamanho_lote,
        detalhar=detalhar,
        limite_detalhes=limite_detalhes,
        max_paginas=max_paginas,
    )


@router.get("/resumo")
def resumo(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(filial, usuario)
    return service.obter_resumo(filial_resolvida)


@router.get("/produtos")
def produtos(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    status_tributario: Optional[str] = Query(None),
    status_ia: Optional[str] = Query(None),
    lote: Optional[int] = Query(None, ge=1),
    busca: Optional[str] = Query(None),
    limite: int = Query(100, ge=1, le=10000),
    offset: int = Query(0, ge=0),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(filial, usuario)
    return service.listar_produtos(
        filial=filial_resolvida,
        status_tributario=status_tributario,
        status_ia=status_ia,
        lote=lote,
        busca=busca,
        limite=limite,
        offset=offset,
    )


@router.get("/lotes")
def lotes(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(filial, usuario)
    return service.listar_lotes(filial_resolvida)


@router.get("/lote/{numero_lote}")
def lote(
    numero_lote: int,
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(filial, usuario)
    return service.obter_lote(filial_resolvida, numero_lote)


@router.patch("/produto/{produto_id}")
def atualizar_produto(
    produto_id: str,
    body: ProdutoTributarioAtualizacao,
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    _ = usuario
    return service.atualizar_produto(produto_id, model_to_dict(body))


# ============================================================
# REGRAS TRIBUTÁRIAS POR NCM
# ============================================================

@router.get("/regras")
def regras(
    ncm: Optional[str] = Query(None),
    regime_tributario: Optional[str] = Query(None),
    uf_origem: Optional[str] = Query(None),
    uf_destino: Optional[str] = Query(None),
    tipo_operacao: Optional[str] = Query(None),
    finalidade: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    ativo: Optional[bool] = Query(True),
    limite: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    _ = usuario
    return service.listar_regras(
        ncm=ncm,
        regime_tributario=regime_tributario,
        uf_origem=uf_origem,
        uf_destino=uf_destino,
        tipo_operacao=tipo_operacao,
        finalidade=finalidade,
        status=status,
        ativo=ativo,
        limite=limite,
        offset=offset,
    )


@router.get("/regra-aplicavel")
def regra_aplicavel(
    ncm: str = Query(..., min_length=8, max_length=8),
    regime_tributario: str = Query("lucro_presumido"),
    uf_origem: str = Query("SP", min_length=2, max_length=2),
    uf_destino: str = Query("SP", min_length=2, max_length=2),
    tipo_operacao: str = Query("venda"),
    finalidade: str = Query("revenda"),
    consumidor_final: bool = Query(False),
    contribuinte_icms: bool = Query(True),
    data_referencia: Optional[str] = Query(None, description="YYYY-MM-DD"),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    _ = usuario
    return service.buscar_regra_aplicavel(
        ncm=ncm,
        regime_tributario=regime_tributario,
        uf_origem=uf_origem,
        uf_destino=uf_destino,
        tipo_operacao=tipo_operacao,
        finalidade=finalidade,
        consumidor_final=consumidor_final,
        contribuinte_icms=contribuinte_icms,
        data_referencia=data_referencia,
    )


@router.get("/regra/{regra_id}")
def regra(
    regra_id: str,
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    _ = usuario
    return service.obter_regra(regra_id)


@router.post("/regra", status_code=201)
def criar_regra(
    body: RegraTributariaCriacao,
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    _ = usuario
    return service.criar_regra(model_to_dict(body, exclude_none=False))


@router.patch("/regra/{regra_id}")
def atualizar_regra(
    regra_id: str,
    body: RegraTributariaAtualizacao,
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    _ = usuario
    return service.atualizar_regra(regra_id, model_to_dict(body))


@router.delete("/regra/{regra_id}")
def excluir_regra(
    regra_id: str,
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    _ = usuario
    return service.excluir_regra(regra_id)



@router.get("/ncms-utilizados")
def ncms_utilizados(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    incluir_sem_ncm: bool = Query(False),
    limite: int = Query(1000, ge=1, le=10000),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    # Em modo de implantação, o usuário técnico possui filial=all.
    # Quando o Swagger não envia a filial, usamos SP como padrão; 
    # filial=all continua disponível quando informada explicitamente.
    filial_entrada = filial
    if not service.safe_str(filial_entrada) and service.normalizar_filial(
        usuario.get("filial") or "sp"
    ) == "all":
        filial_entrada = "sp"

    filial_resolvida = service.resolver_filial_autorizada(
        filial_entrada, usuario
    )
    return service.listar_ncms_utilizados(
        filial=filial_resolvida,
        incluir_sem_ncm=incluir_sem_ncm,
        limite=limite,
    )

# ============================================================
# AUDITORIA TRIBUTÁRIA
# ============================================================

@router.post("/auditar")
def auditar_tributacao(
    filial: Optional[str] = Query(None, description="sp ou mg"),
    regime_tributario: str = Query("lucro_presumido"),
    uf_origem: Optional[str] = Query(None, min_length=2, max_length=2),
    uf_destino: Optional[str] = Query(None, min_length=2, max_length=2),
    tipo_operacao: str = Query("venda"),
    finalidade: str = Query("revenda"),
    consumidor_final: bool = Query(False),
    contribuinte_icms: bool = Query(True),
    data_referencia: Optional[str] = Query(None, description="YYYY-MM-DD"),
    limite: int = Query(10000, ge=1, le=10000),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(
        filial, usuario, permitir_all=False
    )
    return service.auditar_tributacao(
        filial=filial_resolvida,
        regime_tributario=regime_tributario,
        uf_origem=uf_origem,
        uf_destino=uf_destino,
        tipo_operacao=tipo_operacao,
        finalidade=finalidade,
        consumidor_final=consumidor_final,
        contribuinte_icms=contribuinte_icms,
        data_referencia=data_referencia,
        limite=limite,
    )


@router.get("/auditoria/resumo")
def resumo_auditoria(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(filial, usuario)
    return service.obter_resumo_auditoria(filial_resolvida)


@router.get("/auditoria/produtos")
def produtos_auditoria(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    status: Optional[str] = Query(None, description="ok, revisar, sem_regra ou erro"),
    busca: Optional[str] = Query(None),
    limite: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    apenas_ultima: bool = Query(True),
    usuario: Dict[str, Any] = Depends(service.obter_usuario_atual),
):
    filial_resolvida = service.resolver_filial_autorizada(filial, usuario)
    return service.listar_produtos_auditoria(
        filial=filial_resolvida,
        status=status,
        busca=busca,
        limite=limite,
        offset=offset,
        apenas_ultima=apenas_ultima,
    )
