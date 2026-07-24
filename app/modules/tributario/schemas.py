
from typing import Optional

from pydantic import BaseModel, Field


class AtualizarProdutoTributarioBody(BaseModel):
    ncm: Optional[str] = None
    cest: Optional[str] = None
    origem_mercadoria: Optional[str] = None
    fornecedor: Optional[str] = None
    estado_fornecedor: Optional[str] = None
    regime_fornecedor: Optional[str] = None
    status_tributario: Optional[str] = None
    status_ia: Optional[str] = None
    percentual_confianca: Optional[float] = Field(default=None, ge=0, le=100)
    justificativa_ia: Optional[str] = None


class AprovarProdutoTributarioBody(BaseModel):
    ncm_aprovado: Optional[str] = None
    cest_aprovado: Optional[str] = None
    observacao: Optional[str] = None
