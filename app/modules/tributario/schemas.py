from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ProdutoTributarioAtualizacao(BaseModel):
    codigo: Optional[str] = None
    sku: Optional[str] = None
    descricao: Optional[str] = None
    ncm: Optional[str] = None
    cest: Optional[str] = None
    origem_mercadoria: Optional[str] = None
    unidade: Optional[str] = None
    preco: Optional[float] = None
    custo: Optional[float] = None
    fornecedor: Optional[str] = None
    ativo: Optional[bool] = None
    status_ia: Optional[str] = None
    percentual_confianca: Optional[float] = Field(None, ge=0, le=100)
    justificativa_ia: Optional[str] = None
    data_analise: Optional[datetime] = None
    lote: Optional[int] = Field(None, ge=1)


class RegraTributariaBase(BaseModel):
    ncm: str = Field(..., min_length=8, max_length=8)
    descricao_ncm: Optional[str] = None
    cest: Optional[str] = None
    ex_tipi: Optional[str] = None

    regime_tributario: str = "lucro_presumido"
    uf_origem: str = Field("SP", min_length=2, max_length=2)
    uf_destino: str = Field("SP", min_length=2, max_length=2)
    tipo_operacao: str = "venda"
    finalidade: str = "revenda"
    consumidor_final: bool = False
    contribuinte_icms: bool = True

    cfop: Optional[str] = None
    cst_icms: Optional[str] = None
    csosn: Optional[str] = None
    aliquota_icms: float = 0
    reducao_base_icms: float = 0
    possui_icms_st: bool = False
    mva: float = 0
    aliquota_icms_st: float = 0
    reducao_base_icms_st: float = 0

    possui_fcp: bool = False
    aliquota_fcp: float = 0
    aliquota_fcp_st: float = 0

    possui_difal: bool = False
    aliquota_interna_destino: float = 0
    aliquota_interestadual: float = 0

    cst_ipi: Optional[str] = None
    aliquota_ipi: float = 0
    codigo_enquadramento_ipi: Optional[str] = None

    cst_pis: Optional[str] = None
    aliquota_pis: float = 0
    cst_cofins: Optional[str] = None
    aliquota_cofins: float = 0

    pis_cofins_monofasico: bool = False
    substituicao_tributaria: bool = False
    beneficio_fiscal: bool = False
    codigo_beneficio_fiscal: Optional[str] = None
    desoneracao_icms: bool = False

    status: str = "pendente"
    percentual_confianca: float = Field(0, ge=0, le=100)
    justificativa_ia: Optional[str] = None
    observacoes: Optional[str] = None

    fonte: Optional[str] = None
    link_fonte: Optional[str] = None
    data_consulta_fonte: Optional[datetime] = None
    vigencia_inicio: Optional[date] = None
    vigencia_fim: Optional[date] = None

    ativo: bool = True
    revisao_manual: bool = False


class RegraTributariaCriacao(RegraTributariaBase):
    pass


class RegraTributariaAtualizacao(BaseModel):
    ncm: Optional[str] = Field(None, min_length=8, max_length=8)
    descricao_ncm: Optional[str] = None
    cest: Optional[str] = None
    ex_tipi: Optional[str] = None
    regime_tributario: Optional[str] = None
    uf_origem: Optional[str] = Field(None, min_length=2, max_length=2)
    uf_destino: Optional[str] = Field(None, min_length=2, max_length=2)
    tipo_operacao: Optional[str] = None
    finalidade: Optional[str] = None
    consumidor_final: Optional[bool] = None
    contribuinte_icms: Optional[bool] = None
    cfop: Optional[str] = None
    cst_icms: Optional[str] = None
    csosn: Optional[str] = None
    aliquota_icms: Optional[float] = None
    reducao_base_icms: Optional[float] = None
    possui_icms_st: Optional[bool] = None
    mva: Optional[float] = None
    aliquota_icms_st: Optional[float] = None
    reducao_base_icms_st: Optional[float] = None
    possui_fcp: Optional[bool] = None
    aliquota_fcp: Optional[float] = None
    aliquota_fcp_st: Optional[float] = None
    possui_difal: Optional[bool] = None
    aliquota_interna_destino: Optional[float] = None
    aliquota_interestadual: Optional[float] = None
    cst_ipi: Optional[str] = None
    aliquota_ipi: Optional[float] = None
    codigo_enquadramento_ipi: Optional[str] = None
    cst_pis: Optional[str] = None
    aliquota_pis: Optional[float] = None
    cst_cofins: Optional[str] = None
    aliquota_cofins: Optional[float] = None
    pis_cofins_monofasico: Optional[bool] = None
    substituicao_tributaria: Optional[bool] = None
    beneficio_fiscal: Optional[bool] = None
    codigo_beneficio_fiscal: Optional[str] = None
    desoneracao_icms: Optional[bool] = None
    status: Optional[str] = None
    percentual_confianca: Optional[float] = Field(None, ge=0, le=100)
    justificativa_ia: Optional[str] = None
    observacoes: Optional[str] = None
    fonte: Optional[str] = None
    link_fonte: Optional[str] = None
    data_consulta_fonte: Optional[datetime] = None
    vigencia_inicio: Optional[date] = None
    vigencia_fim: Optional[date] = None
    ativo: Optional[bool] = None
    revisao_manual: Optional[bool] = None
