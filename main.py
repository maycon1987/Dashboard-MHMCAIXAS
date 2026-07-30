import os
import time
import io
import re
import json
import html
import zipfile
import requests
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from calendar import monthrange
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Query, HTTPException, Depends, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from app.modules.tributario.router import router as tributario_router


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="MHM Dashboard Tiny API",
    version="2.6.0",
    description="API para sincronizar Tiny/Olist com Supabase e alimentar dashboard Lovable."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(tributario_router)

# ============================================================
# ENV
# ============================================================

TINY_TOKEN = os.getenv("TINY_TOKEN", "").strip()
TINY_API_KEY_MINAS = os.getenv("TINY_API_KEY_MINAS", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")

# Aceita os dois nomes, para não quebrar se o Railway tiver uma ou outra variável
SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.getenv("SUPABASE_SERVICE_KEY", "").strip()
)

TINY_BASE_URL = "https://api.tiny.com.br/api2"

# Ativação gradual da proteção JWT.
# Primeiro publique com false, ajuste o Lovable para enviar o token,
# teste e só depois altere para true no Railway.
JWT_AUTH_ENABLED = os.getenv("JWT_AUTH_ENABLED", "false").strip().lower() in [
    "1", "true", "yes", "sim", "on"
]

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


def normalizar_filial(filial: str = "sp") -> str:
    """
    Padroniza filial para uso no backend.
    sp  = Campinas
    mg  = Minas/Pouso Alegre
    all = consolidado
    """
    filial = (filial or "sp").lower().strip()

    if filial in ["all", "todas", "todos", "consolidado"]:
        return "all"

    if filial in ["mg", "minas", "pouso_alegre", "pouso-alegre"]:
        return "mg"

    return "sp"


def adicionar_filtro_filial_params(params: List[Any], filial: str = "sp") -> List[Any]:
    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params.append(("filial", f"eq.{filial_normalizada}"))
    return params



# ============================================================
# AUTENTICAÇÃO E AUTORIZAÇÃO — SUPABASE JWT
# ============================================================

bearer_scheme = HTTPBearer(auto_error=False)


def validar_token_supabase(access_token: str) -> Dict[str, Any]:
    """
    Valida o access_token diretamente no Supabase Auth.

    Essa abordagem funciona tanto para projetos com assinatura JWT legada
    quanto para projetos com chaves assimétricas, sem expor o JWT Secret.
    """
    validar_env()

    url = f"{SUPABASE_URL}/auth/v1/user"

    response = requests.get(
        url,
        headers={
            "apikey": SUPABASE_SERVICE_ROLE_KEY,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30
    )

    if response.status_code in [401, 403]:
        raise HTTPException(
            status_code=401,
            detail="Sessão inválida ou expirada. Faça login novamente."
        )

    if response.status_code >= 400:
        raise HTTPException(
            status_code=503,
            detail={
                "erro": "Não foi possível validar a sessão no Supabase Auth.",
                "status_code": response.status_code,
                "resposta": response.text
            }
        )

    usuario_auth = response.json()

    if not usuario_auth.get("id") or not usuario_auth.get("email"):
        raise HTTPException(
            status_code=401,
            detail="Token válido, mas sem identificação de usuário."
        )

    return usuario_auth


def buscar_perfil_dashboard(email: str) -> Dict[str, Any]:
    """
    Consulta a autorização interna do usuário na tabela usuarios_dashboard.
    """
    resultado = supabase_get(
        "usuarios_dashboard",
        {
            "select": "email,nome,perfil,filial,ativo",
            "email": f"eq.{email}",
            "limit": "1"
        }
    )

    if not resultado:
        raise HTTPException(
            status_code=403,
            detail="Usuário autenticado, mas sem acesso ao Dashboard MHM."
        )

    perfil = resultado[0]

    if perfil.get("ativo") is False:
        raise HTTPException(
            status_code=403,
            detail="Usuário desativado no Dashboard MHM."
        )

    perfil["perfil"] = safe_str(perfil.get("perfil")).lower().strip()
    perfil["filial"] = normalizar_filial(perfil.get("filial") or "sp")

    return perfil


def obter_usuario_atual(
    credenciais: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> Dict[str, Any]:
    """
    Dependência usada pelas rotas protegidas.

    Durante a implantação, JWT_AUTH_ENABLED=false mantém compatibilidade
    temporária. Quando true, o Bearer token passa a ser obrigatório.
    """
    if not JWT_AUTH_ENABLED:
        return {
            "id": "modo-implantacao",
            "email": "sistema@interno",
            "nome": "Modo de implantação",
            "perfil": "admin",
            "filial": "all",
            "ativo": True,
            "auth_desativada_temporariamente": True
        }

    if not credenciais or not credenciais.credentials:
        raise HTTPException(
            status_code=401,
            detail="Token de acesso não informado.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    usuario_auth = validar_token_supabase(credenciais.credentials)
    perfil = buscar_perfil_dashboard(usuario_auth["email"])

    return {
        "id": usuario_auth["id"],
        "email": usuario_auth["email"],
        "nome": perfil.get("nome") or usuario_auth.get("email"),
        "perfil": perfil.get("perfil"),
        "filial": perfil.get("filial"),
        "ativo": perfil.get("ativo", True)
    }


def resolver_filial_autorizada(
    filial_solicitada: Optional[str],
    usuario: Dict[str, Any],
    permitir_all: bool = True
) -> str:
    """
    Decide a filial no backend.

    - Usuário com filial=all pode escolher sp, mg ou all.
    - Usuário restrito a sp/mg só pode consultar sua própria filial.
    - Quando a filial não é enviada, usa a filial do próprio usuário.
    """
    filial_usuario = normalizar_filial(usuario.get("filial") or "sp")

    if filial_solicitada is None or not safe_str(filial_solicitada).strip():
        if filial_usuario == "all":
            return "all" if permitir_all else "sp"
        return filial_usuario

    filial_pedida = normalizar_filial(filial_solicitada)

    if not permitir_all and filial_pedida == "all":
        raise HTTPException(
            status_code=400,
            detail="Esta operação não aceita filial=all."
        )

    if filial_usuario == "all":
        return filial_pedida

    if filial_pedida != filial_usuario:
        raise HTTPException(
            status_code=403,
            detail=f"Usuário sem permissão para acessar a filial '{filial_pedida}'."
        )

    return filial_usuario


@app.get("/auth/me")
def auth_me(
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    """
    Retorna o usuário reconhecido pelo backend.
    Útil para testar a integração JWT com o Lovable.
    """
    return {
        "status": "ok",
        "auth_ativa": JWT_AUTH_ENABLED,
        "usuario": usuario
    }


# ============================================================
# TINY
# ============================================================

def obter_token_tiny(filial: str = "sp") -> str:
    """
    Retorna o token Tiny conforme a filial.
    """

    filial = (filial or "sp").lower().strip()

    if filial in ["mg", "minas", "pouso_alegre"]:
        if not TINY_API_KEY_MINAS:
            raise HTTPException(
                status_code=500,
                detail="TINY_API_KEY_MINAS não configurado."
            )
        return TINY_API_KEY_MINAS

    return TINY_TOKEN


def tiny_get(endpoint: str, params: Dict[str, Any], filial: str = "sp") -> Dict[str, Any]:
    validar_env()

    url = f"{TINY_BASE_URL}/{endpoint}"

    params_base = {
        "token": obter_token_tiny(filial),
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
        codigo_erro = safe_str(retorno.get("codigo_erro")).strip()
        erros = retorno.get("erros") or []

        mensagens_erro = []
        for item in erros:
            if isinstance(item, dict):
                mensagens_erro.append(
                    safe_str(item.get("erro") or item.get("mensagem") or "")
                )
            else:
                mensagens_erro.append(safe_str(item))

        texto_erros = " ".join(mensagens_erro).lower()

        # O Tiny usa código 20 quando a pesquisa não encontra registros.
        # Isso não é falha da sincronização: significa apenas dia/período sem vendas.
        if codigo_erro == "20" or "não retornou registros" in texto_erros or "nao retornou registros" in texto_erros:
            retorno_vazio = {
                "status": "OK",
                "status_processamento": retorno.get("status_processamento", "3"),
                "numero_paginas": 1
            }

            if "pedidos.pesquisa.php" in endpoint:
                retorno_vazio["pedidos"] = []
            elif "produtos.pesquisa.php" in endpoint:
                retorno_vazio["produtos"] = []

            return {"retorno": retorno_vazio}

        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Tiny retornou erro.",
                "retorno": retorno
            }
        )

    return dados



# ============================================================
# TINY FISCAL — NOTAS E XML
# ============================================================

def tiny_post_xml(
    endpoint: str,
    params: Dict[str, Any],
    filial: str = "sp",
    tentativas: int = 4
) -> str:
    """
    Executa um POST na API 2.0 do Tiny para endpoints que retornam XML.

    O endpoint nota.fiscal.obter.xml.php não retorna JSON: ele devolve um
    XML de resposta contendo xml_nfe e, quando existir, xml_cancelamento.
    """
    url = f"{TINY_BASE_URL}/{endpoint}"
    payload = {
        "token": obter_token_tiny(filial),
        **params,
    }

    ultimo_erro = None

    for tentativa in range(1, max(1, tentativas) + 1):
        try:
            response = requests.post(url, data=payload, timeout=120)
        except requests.RequestException as exc:
            ultimo_erro = str(exc)
            if tentativa < tentativas:
                time.sleep(min(2 ** tentativa, 10))
                continue
            raise HTTPException(
                status_code=502,
                detail={
                    "erro": "Falha de comunicação com o Tiny.",
                    "endpoint": endpoint,
                    "detalhe": ultimo_erro,
                }
            )

        if response.status_code >= 400:
            ultimo_erro = response.text
            if tentativa < tentativas and response.status_code in [429, 500, 502, 503, 504]:
                time.sleep(min(2 ** tentativa, 10))
                continue
            raise HTTPException(
                status_code=502,
                detail={
                    "erro": "Erro HTTP ao consultar o XML no Tiny.",
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "resposta": response.text[:2000],
                }
            )

        conteudo = response.text or ""

        # Códigos 6 e 11 indicam bloqueio temporário por excesso/concor­rência.
        bloqueio_temporario = (
            "<codigo_erro>6</codigo_erro>" in conteudo
            or "<codigo_erro>11</codigo_erro>" in conteudo
        )
        if bloqueio_temporario and tentativa < tentativas:
            time.sleep(min(2 ** tentativa, 12))
            continue

        return conteudo

    raise HTTPException(
        status_code=502,
        detail={
            "erro": "Não foi possível consultar o Tiny após novas tentativas.",
            "endpoint": endpoint,
            "detalhe": ultimo_erro,
        }
    )


def pesquisar_notas_fiscais_tiny(
    data_inicial: date,
    data_final: date,
    pagina: int = 1,
    filial: str = "sp"
) -> Dict[str, Any]:
    return tiny_get(
        "notas.fiscais.pesquisa.php",
        {
            "tipoNota": "S",
            "dataInicial": data_inicial.strftime("%d/%m/%Y"),
            "dataFinal": data_final.strftime("%d/%m/%Y"),
            "pagina": pagina,
        },
        filial=filial,
    )


def extrair_lista_notas_fiscais(resposta_tiny: Dict[str, Any]) -> List[Dict[str, Any]]:
    retorno = resposta_tiny.get("retorno", {}) or {}
    notas_raw = retorno.get("notas_fiscais", []) or []
    notas: List[Dict[str, Any]] = []

    for item in notas_raw:
        if isinstance(item, dict):
            nota = item.get("nota_fiscal", item)
            if isinstance(nota, dict):
                notas.append(nota)

    return notas


def buscar_notas_fiscais_periodo_tiny(
    data_inicial: date,
    data_final: date,
    filial: str = "sp",
    limite: int = 0,
    pausa_segundos: float = 0.35
) -> List[Dict[str, Any]]:
    """
    Pesquisa notas de saída, com paginação de até 100 registros por página.

    limite=0 significa sem limite artificial. O limite é aplicado depois da
    paginação e existe apenas para permitir downloads menores no frontend.
    """
    todas: List[Dict[str, Any]] = []
    pagina = 1

    while True:
        resposta = pesquisar_notas_fiscais_tiny(
            data_inicial,
            data_final,
            pagina=pagina,
            filial=filial,
        )
        retorno = resposta.get("retorno", {}) or {}
        notas = extrair_lista_notas_fiscais(resposta)
        todas.extend(notas)

        if limite > 0 and len(todas) >= limite:
            return todas[:limite]

        numero_paginas = int(retorno.get("numero_paginas", 1) or 1)
        if pagina >= numero_paginas:
            break

        pagina += 1
        time.sleep(pausa_segundos)

    return todas


def modelo_nota_pela_chave(chave_acesso: Any) -> str:
    """
    Na chave de acesso da NF-e/NFC-e, o modelo ocupa as posições 21 e 22.
    Modelo 55 = NF-e; modelo 65 = NFC-e.
    """
    chave = re.sub(r"\D", "", safe_str(chave_acesso))
    if len(chave) == 44:
        modelo = chave[20:22]
        if modelo == "55":
            return "nfe"
        if modelo == "65":
            return "nfce"
    return "outros"


def nota_fiscal_valida_para_total(nota: Dict[str, Any]) -> bool:
    descricao = normalizar_texto_tag(
        safe_str(nota.get("descricao_situacao") or nota.get("situacao") or "")
    )
    termos_invalidos = [
        "CANCEL", "REJEIT", "DENEG", "INUTIL", "NAO AUTORIZ",
    ]
    return not any(termo in descricao for termo in termos_invalidos)


def resumir_notas_fiscais(notas: List[Dict[str, Any]]) -> Dict[str, Any]:
    grupos = {
        "nfe": {"quantidade": 0, "valor_total": 0.0},
        "nfce": {"quantidade": 0, "valor_total": 0.0},
        "outros": {"quantidade": 0, "valor_total": 0.0},
    }
    situacoes: Dict[str, Dict[str, Any]] = {}
    documentos_invalidos = 0
    valor_invalidos = 0.0

    for nota in notas:
        modelo = modelo_nota_pela_chave(nota.get("chave_acesso"))
        valor = dinheiro_para_float(nota.get("valor"))
        situacao = safe_str(
            nota.get("descricao_situacao") or nota.get("situacao") or "Sem situação"
        ).strip() or "Sem situação"

        if situacao not in situacoes:
            situacoes[situacao] = {"quantidade": 0, "valor_total": 0.0}
        situacoes[situacao]["quantidade"] += 1
        situacoes[situacao]["valor_total"] += valor

        if nota_fiscal_valida_para_total(nota):
            grupos[modelo]["quantidade"] += 1
            grupos[modelo]["valor_total"] += valor
        else:
            documentos_invalidos += 1
            valor_invalidos += valor

    for grupo in grupos.values():
        grupo["valor_total"] = round(grupo["valor_total"], 2)

    situacoes_lista = []
    for nome, dados in situacoes.items():
        situacoes_lista.append({
            "situacao": nome,
            "quantidade": dados["quantidade"],
            "valor_total": round(dados["valor_total"], 2),
        })
    situacoes_lista.sort(key=lambda item: item["valor_total"], reverse=True)

    quantidade_total = sum(grupo["quantidade"] for grupo in grupos.values())
    valor_total = round(sum(grupo["valor_total"] for grupo in grupos.values()), 2)

    return {
        **grupos,
        "total_geral": {
            "quantidade": quantidade_total,
            "valor_total": valor_total,
        },
        "documentos_desconsiderados": {
            "quantidade": documentos_invalidos,
            "valor_total": round(valor_invalidos, 2),
            "motivo": "Canceladas, rejeitadas, denegadas, inutilizadas ou não autorizadas.",
        },
        "situacoes": situacoes_lista,
    }


def _tag_local(elemento: ET.Element) -> str:
    return elemento.tag.split("}")[-1] if "}" in elemento.tag else elemento.tag


def extrair_xml_nfe_resposta_tiny(conteudo_resposta: str) -> Dict[str, Optional[str]]:
    """
    Extrai o XML fiscal e o XML de cancelamento do envelope retornado pelo Tiny.
    Funciona tanto quando o XML vem como filho real quanto quando vem escapado.
    """
    if not conteudo_resposta.strip():
        raise HTTPException(status_code=502, detail="Tiny retornou resposta vazia ao obter XML.")

    try:
        raiz = ET.fromstring(conteudo_resposta)
    except ET.ParseError:
        raise HTTPException(
            status_code=502,
            detail={
                "erro": "Tiny retornou XML de resposta inválido.",
                "resposta": conteudo_resposta[:2000],
            }
        )

    status = ""
    codigo_erro = ""
    mensagens: List[str] = []
    elemento_xml_nfe = None
    elemento_xml_cancelamento = None

    for elemento in raiz.iter():
        nome = _tag_local(elemento)
        if nome == "status":
            status = safe_str(elemento.text).strip()
        elif nome == "codigo_erro":
            codigo_erro = safe_str(elemento.text).strip()
        elif nome == "erro":
            mensagem = safe_str(elemento.text).strip()
            if mensagem:
                mensagens.append(mensagem)
        elif nome == "xml_nfe":
            elemento_xml_nfe = elemento
        elif nome == "xml_cancelamento":
            elemento_xml_cancelamento = elemento

    if status.upper() == "ERRO":
        status_http = 404 if codigo_erro == "32" else 502
        raise HTTPException(
            status_code=status_http,
            detail={
                "erro": "Tiny não disponibilizou o XML solicitado.",
                "codigo_erro": codigo_erro,
                "mensagens": mensagens,
            }
        )

    def conteudo_elemento(elemento: Optional[ET.Element]) -> Optional[str]:
        if elemento is None:
            return None

        filhos = list(elemento)
        if filhos:
            partes = [
                ET.tostring(filho, encoding="unicode")
                for filho in filhos
            ]
            return "".join(partes).strip() or None

        texto = html.unescape(safe_str(elemento.text)).strip()
        return texto or None

    xml_nfe = conteudo_elemento(elemento_xml_nfe)
    xml_cancelamento = conteudo_elemento(elemento_xml_cancelamento)

    if not xml_nfe:
        raise HTTPException(
            status_code=404,
            detail={
                "erro": "XML da nota não encontrado no retorno do Tiny.",
                "codigo_erro": codigo_erro,
                "mensagens": mensagens,
            }
        )

    if not xml_nfe.lstrip().startswith("<?xml"):
        xml_nfe = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_nfe

    if xml_cancelamento and not xml_cancelamento.lstrip().startswith("<?xml"):
        xml_cancelamento = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_cancelamento

    return {
        "xml_nfe": xml_nfe,
        "xml_cancelamento": xml_cancelamento,
    }


def obter_xml_nota_fiscal_tiny(id_nota: str, filial: str = "sp") -> Dict[str, Optional[str]]:
    resposta = tiny_post_xml(
        "nota.fiscal.obter.xml.php",
        {"id": id_nota},
        filial=filial,
    )
    return extrair_xml_nfe_resposta_tiny(resposta)


def nome_seguro_arquivo(valor: Any, padrao: str = "documento") -> str:
    texto = re.sub(r"[^A-Za-z0-9._-]+", "_", safe_str(valor).strip())
    texto = texto.strip("._-")
    return texto or padrao

def pesquisar_pedidos_tiny(
    data_inicial: date,
    data_final: date,
    pagina: int = 1,
    filial: str = "sp"
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
    },
    filial=filial
)


def obter_pedido_tiny(
    id_pedido: str,
    filial: str = "sp"
) -> Dict[str, Any]:
    return tiny_get(
    "pedido.obter.php",
    {
        "id": id_pedido
    },
    filial=filial
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
    pausa_segundos: float = 0.8,
    filial: str = "sp"
) -> List[Dict[str, Any]]:
    """
    Busca pedidos paginando.
    """
    todos = []
    pagina = 1

    while True:
        resposta = pesquisar_pedidos_tiny(
    data_inicial,
    data_final,
    pagina,
    filial=filial
)
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
    Conta APENAS pedidos com NF emitida (faturado e posteriores).
    Status válidos: faturado, pronto para envio, enviado, entregue.
    Status inválidos: em aberto, aprovado, cancelado, orcamento, etc.
    """
    situacao = str(pedido.get("situacao", "")).lower().strip()

    # Só conta se estiver em um desses status (NF emitida)
    status_validos = [
        "faturado",
        "pronto para envio",
        "enviado",
        "entregue",
        "faturada",
    ]

    for valido in status_validos:
        if valido in situacao:
            return True

    return False



def extrair_marcadores_pedido(pedido: Dict[str, Any]) -> List[str]:
    """
    Extrai os marcadores/tags do pedido retornado pelo Tiny.
    Exemplo real do Tiny:
    "marcadores": [{"marcador": {"descricao": "PDV"}}]
    """
    marcadores_raw = pedido.get("marcadores", []) or []
    marcadores = []

    for item in marcadores_raw:
        marcador = item.get("marcador", item) if isinstance(item, dict) else {}
        descricao = safe_str(marcador.get("descricao", "")).strip()
        if descricao:
            marcadores.append(descricao)

    return marcadores


def definir_canal_venda(pedido: Dict[str, Any]) -> str:
    """
    Regra MHM:
    - Pedido com marcador/tag "PDV" = PDV
    - Pedido sem marcador/tag "PDV" = COMERCIAL
    """
    marcadores = extrair_marcadores_pedido(pedido)

    for marcador in marcadores:
        if marcador.strip().upper() == "PDV":
            return "PDV"

    return "COMERCIAL"


def normalizar_texto_tag(valor: str) -> str:
    texto = safe_str(valor).strip().upper()
    substituicoes = {
        "Á": "A", "À": "A", "Ã": "A", "Â": "A",
        "É": "E", "Ê": "E", "Í": "I",
        "Ó": "O", "Ô": "O", "Õ": "O",
        "Ú": "U", "Ç": "C",
    }
    for origem, destino in substituicoes.items():
        texto = texto.replace(origem, destino)
    return " ".join(texto.split())


TAGS_ORIGEM_CLIENTE = {
    "INSTAGRAM": "Instagram",
    "INSTA": "Instagram",
    "TIKTOK": "TikTok",
    "TIK TOK": "TikTok",
    "GOOGLE": "Google",
    "RUA": "Passando na Rua",
    "PASSANDO NA RUA": "Passando na Rua",
    "PASSOU NA RUA": "Passando na Rua",
    "PROSPECCAO": "Prospecção",
    "PROSPECTADO": "Prospecção",
    "INDICACAO": "Indicação",
    "INDICADO": "Indicação",
}


def definir_origem_cliente(pedido: Dict[str, Any]) -> str:
    marcadores = extrair_marcadores_pedido(pedido)
    for marcador in marcadores:
        marcador_normalizado = normalizar_texto_tag(marcador)
        if marcador_normalizado in TAGS_ORIGEM_CLIENTE:
            return TAGS_ORIGEM_CLIENTE[marcador_normalizado]
    return "Sem origem"


def extrair_nome_cliente(pedido: Dict[str, Any]) -> str:
    cliente = pedido.get("cliente")

    if isinstance(cliente, dict):
        return safe_str(cliente.get("nome") or cliente.get("nome_fantasia") or "")

    return safe_str(pedido.get("nome") or pedido.get("cliente") or "")


def normalizar_pedido_resumo(
    pedido: Dict[str, Any],
    filial: str = "sp"
) -> Dict[str, Any]:
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

    marcadores = extrair_marcadores_pedido(pedido)
    canal_venda = definir_canal_venda(pedido)
    origem_cliente = definir_origem_cliente(pedido)

    return {
    "tiny_id": id_pedido,
    "numero": safe_str(pedido.get("numero", "")),
    "numero_ecommerce": safe_str(pedido.get("numero_ecommerce", "")),
    "data_pedido": data_pedido_iso,
    "cliente": extrair_nome_cliente(pedido),
    "situacao": safe_str(pedido.get("situacao", "")),
    "valor_total": valor,

    "marcadores": marcadores,
    "canal_venda": canal_venda,
    "origem_cliente": origem_cliente,

    "filial": filial,

    "id_vendedor": safe_str(pedido.get("id_vendedor", "")),
    "nome_vendedor": safe_str(pedido.get("nome_vendedor", "")),
    "forma_pagamento": safe_str(pedido.get("forma_pagamento", "")),
    "meio_pagamento": safe_str(pedido.get("meio_pagamento", "")),

    "raw": pedido,
    "updated_at": datetime.now().isoformat()
}

def extrair_itens_do_pedido_completo(
    pedido_completo: Dict[str, Any],
    pedido_id: str,
    data_pedido: Optional[str],
    filial: str = "sp"
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
    "filial": filial,

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
    faturamento: float = 0.0,
    filial: str = "sp"
):
    payload = {
        "tipo": tipo,
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "status": status,
        "mensagem": mensagem,
        "total_pedidos": total_pedidos,
        "faturamento": faturamento,
        "filial": normalizar_filial(filial),
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


def limpar_ranking_periodo(data_inicio: date, data_fim: date, tipo: str, filial: str = "sp"):
    try:
        supabase_delete(
            "ranking_periodo",
            {
                "data_inicio": f"eq.{data_inicio.isoformat()}",
                "data_fim": f"eq.{data_fim.isoformat()}",
                "tipo": f"eq.{tipo}",
                "filial": f"eq.{normalizar_filial(filial)}"
            }
        )
    except Exception:
        pass
def sincronizar_periodo(
    data_inicio: date,
    data_fim: date,
    tipo: str = "periodo",
    buscar_itens: bool = True,
    filial: str = "sp"
) -> Dict[str, Any]:
    """
    Busca pedidos no Tiny, salva pedidos, itens e resumos no Supabase.
    """

    pedidos_tiny = buscar_pedidos_periodo_tiny(
    data_inicio,
    data_fim,
    filial=filial
)

    pedidos_normalizados = []
    itens_normalizados = []

    for pedido_raw in pedidos_tiny:
        if not pedido_tem_venda_valida(pedido_raw):
            continue

        # Primeiro pega o detalhe completo do pedido.
        # É no pedido.obter.php que o Tiny retorna "marcadores", incluindo a TAG "PDV".
        pedido_completo = None
        pedido_detalhado = pedido_raw

        id_para_obter = safe_str(
            pedido_raw.get("id")
            or pedido_raw.get("numero")
            or pedido_raw.get("numero_ecommerce")
            or ""
        )

        if id_para_obter:
            try:
                pedido_completo = obter_pedido_tiny(
    id_para_obter,
    filial=filial
)
                pedido_detalhado = pedido_completo.get("retorno", {}).get("pedido", pedido_raw)
                time.sleep(0.7)
            except Exception:
                pedido_detalhado = pedido_raw

        pedido_norm = normalizar_pedido_resumo(
    pedido_detalhado,
    filial=filial
)
        if not pedido_norm.get("tiny_id"):
            continue

        if not pedido_norm.get("data_pedido"):
            pedido_norm["data_pedido"] = data_inicio.isoformat()

        pedidos_normalizados.append(pedido_norm)

        if buscar_itens:
            try:
                if not pedido_completo:
                    pedido_completo = obter_pedido_tiny(
    pedido_norm["tiny_id"],
    filial=filial
)
                    time.sleep(0.7)

                itens = extrair_itens_do_pedido_completo(
                    pedido_completo,
                    pedido_norm["tiny_id"],
                    pedido_norm["data_pedido"],
                    filial=filial
                )
                itens_normalizados.extend(itens)
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
            "filial": normalizar_filial(filial),
            "updated_at": datetime.now().isoformat()
        }

        try:
            supabase_insert(
                "resumo_diario",
                resumo_diario_payload,
                upsert=True,
                on_conflict="data_resumo,filial"
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
            "filial": normalizar_filial(filial),
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
            "filial": normalizar_filial(filial),
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
            "filial": normalizar_filial(filial),
            "updated_at": datetime.now().isoformat()
        })

    if ranking_periodo_payload:
        limpar_ranking_periodo(data_inicio, data_fim, tipo, filial=filial)

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
        faturamento=calculado["faturamento"],
        filial=filial
    )

    return {
        "status": "ok",
        "tipo": tipo,
        "data_inicio": data_inicio.isoformat(),
        "data_fim": data_fim.isoformat(),
        "total_pedidos": calculado["total_pedidos"],
        "faturamento": calculado["faturamento"],
        "ticket_medio": calculado["ticket_medio"],
        "filial": normalizar_filial(filial),
        "top_10": calculado["ranking"][:10]
    }


# ============================================================
# MODELS
# ============================================================

class PeriodoBody(BaseModel):
    data_inicio: str
    data_fim: str

# ============================================================
# MODELOS — REGRAS TRIBUTÁRIAS
# ============================================================



class FiscalMotorSimularBody(BaseModel):
    produto_id: Optional[str] = None
    ncm: Optional[str] = None
    uf_origem: str
    uf_destino: str
    operacao: str = "venda"
    regime: str
    filial: Optional[str] = None
    consumidor_final: Optional[bool] = None
    contribuinte_icms: Optional[bool] = None
    pessoa_fisica: Optional[bool] = None
    marketplace: Optional[bool] = None
    data_operacao: Optional[str] = None

class FiscalRegraBase(BaseModel):
    empresa_id: Optional[str] = None
    filial: str = "sp"
    nome: str
    descricao: Optional[str] = None
    ativa: bool = True
    prioridade: int = 100
    regime_tributario: str
    tipo_operacao: str = "venda"
    uf_origem: Optional[str] = None
    uf_destino: Optional[str] = None
    categoria_id: Optional[str] = None
    ncm: Optional[str] = None
    cfop: Optional[str] = None
    cst_icms: Optional[str] = None
    csosn: Optional[str] = None
    aliquota_icms: float = 0
    reducao_bc: float = 0
    tem_st: bool = False
    mva: float = 0
    aliquota_fcp: float = 0
    cst_pis: Optional[str] = None
    aliquota_pis: float = 0
    cst_cofins: Optional[str] = None
    aliquota_cofins: float = 0
    cst_ipi: Optional[str] = None
    aliquota_ipi: float = 0
    consumidor_final: Optional[bool] = None
    contribuinte_icms: Optional[bool] = None
    pessoa_fisica: Optional[bool] = None
    marketplace: Optional[bool] = None
    vigencia_inicio: Optional[str] = None
    vigencia_fim: Optional[str] = None
    observacoes: Optional[str] = None
    produtos: List[str] = []


class FiscalRegraCriar(FiscalRegraBase):
    pass


class FiscalRegraAtualizar(BaseModel):
    empresa_id: Optional[str] = None
    filial: Optional[str] = None
    nome: Optional[str] = None
    descricao: Optional[str] = None
    ativa: Optional[bool] = None
    prioridade: Optional[int] = None
    regime_tributario: Optional[str] = None
    tipo_operacao: Optional[str] = None
    uf_origem: Optional[str] = None
    uf_destino: Optional[str] = None
    categoria_id: Optional[str] = None
    ncm: Optional[str] = None
    cfop: Optional[str] = None
    cst_icms: Optional[str] = None
    csosn: Optional[str] = None
    aliquota_icms: Optional[float] = None
    reducao_bc: Optional[float] = None
    tem_st: Optional[bool] = None
    mva: Optional[float] = None
    aliquota_fcp: Optional[float] = None
    cst_pis: Optional[str] = None
    aliquota_pis: Optional[float] = None
    cst_cofins: Optional[str] = None
    aliquota_cofins: Optional[float] = None
    cst_ipi: Optional[str] = None
    aliquota_ipi: Optional[float] = None
    consumidor_final: Optional[bool] = None
    contribuinte_icms: Optional[bool] = None
    pessoa_fisica: Optional[bool] = None
    marketplace: Optional[bool] = None
    vigencia_inicio: Optional[str] = None
    vigencia_fim: Optional[str] = None
    observacoes: Optional[str] = None
    produtos: Optional[List[str]] = None


def modelo_para_dict(modelo: BaseModel, exclude_unset: bool = False) -> Dict[str, Any]:
    if hasattr(modelo, "model_dump"):
        return modelo.model_dump(exclude_unset=exclude_unset)
    return modelo.dict(exclude_unset=exclude_unset)


REGIMES_TRIBUTARIOS_VALIDOS = {
    "simples_nacional", "lucro_presumido", "lucro_real"
}

TIPOS_OPERACAO_VALIDOS = {
    "venda", "compra", "transferencia", "devolucao",
    "bonificacao", "industrializacao", "remessa"
}

UFS_VALIDAS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO",
    "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI",
    "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"
}

CAMPOS_PERCENTUAIS_FISCAL = {
    "aliquota_icms", "reducao_bc", "aliquota_fcp", "aliquota_pis",
    "aliquota_cofins", "aliquota_ipi"
}


def normalizar_codigo_numerico(valor: Optional[str], tamanho_maximo: int) -> Optional[str]:
    if valor is None:
        return None
    texto = re.sub(r"\D", "", safe_str(valor))
    if not texto:
        return None
    if len(texto) > tamanho_maximo:
        raise HTTPException(status_code=400, detail=f"Código inválido: máximo de {tamanho_maximo} dígitos.")
    return texto


def validar_payload_regra_fiscal(payload: Dict[str, Any], parcial: bool = False) -> Dict[str, Any]:
    dados = dict(payload)

    if "nome" in dados:
        dados["nome"] = safe_str(dados.get("nome")).strip()
        if not dados["nome"]:
            raise HTTPException(status_code=400, detail="O nome da regra é obrigatório.")
    elif not parcial:
        raise HTTPException(status_code=400, detail="O nome da regra é obrigatório.")

    if "filial" in dados and dados.get("filial") is not None:
        filial_raw = safe_str(dados.get("filial")).strip().lower()
        if filial_raw not in {"sp", "mg", "all"}:
            raise HTTPException(status_code=400, detail="Filial inválida. Use sp, mg ou all.")
        dados["filial"] = filial_raw

    if "regime_tributario" in dados and dados.get("regime_tributario") is not None:
        regime = safe_str(dados.get("regime_tributario")).strip().lower()
        if regime not in REGIMES_TRIBUTARIOS_VALIDOS:
            raise HTTPException(status_code=400, detail="Regime tributário inválido.")
        dados["regime_tributario"] = regime
    elif not parcial:
        raise HTTPException(status_code=400, detail="O regime tributário é obrigatório.")

    if "tipo_operacao" in dados and dados.get("tipo_operacao") is not None:
        operacao = safe_str(dados.get("tipo_operacao")).strip().lower()
        if operacao not in TIPOS_OPERACAO_VALIDOS:
            raise HTTPException(status_code=400, detail="Tipo de operação inválido.")
        dados["tipo_operacao"] = operacao

    for campo_uf in ["uf_origem", "uf_destino"]:
        if campo_uf in dados and dados.get(campo_uf) is not None:
            uf = safe_str(dados.get(campo_uf)).strip().upper()
            if uf and uf not in UFS_VALIDAS:
                raise HTTPException(status_code=400, detail=f"{campo_uf} inválida.")
            dados[campo_uf] = uf or None

    if "ncm" in dados:
        dados["ncm"] = normalizar_codigo_numerico(dados.get("ncm"), 8)
    if "cfop" in dados:
        dados["cfop"] = normalizar_codigo_numerico(dados.get("cfop"), 4)

    for campo in ["cst_icms", "csosn", "cst_pis", "cst_cofins", "cst_ipi"]:
        if campo in dados:
            dados[campo] = normalizar_codigo_numerico(dados.get(campo), 3)

    if "prioridade" in dados and dados.get("prioridade") is not None:
        if int(dados["prioridade"]) < 0:
            raise HTTPException(status_code=400, detail="A prioridade não pode ser negativa.")
        dados["prioridade"] = int(dados["prioridade"])

    for campo in CAMPOS_PERCENTUAIS_FISCAL:
        if campo in dados and dados.get(campo) is not None:
            valor = float(dados[campo])
            if valor < 0 or valor > 100:
                raise HTTPException(status_code=400, detail=f"{campo} deve estar entre 0 e 100.")
            dados[campo] = valor

    if "mva" in dados and dados.get("mva") is not None:
        dados["mva"] = float(dados["mva"])
        if dados["mva"] < 0:
            raise HTTPException(status_code=400, detail="MVA não pode ser negativa.")

    for campo_data in ["vigencia_inicio", "vigencia_fim"]:
        if campo_data in dados and dados.get(campo_data):
            dados[campo_data] = parse_data(safe_str(dados[campo_data])).isoformat()

    inicio = dados.get("vigencia_inicio")
    fim = dados.get("vigencia_fim")
    if inicio and fim and fim < inicio:
        raise HTTPException(status_code=400, detail="vigencia_fim não pode ser menor que vigencia_inicio.")

    return dados


def usuario_id_uuid_ou_none(usuario: Dict[str, Any]) -> Optional[str]:
    valor = safe_str(usuario.get("id")).strip()
    return valor if re.fullmatch(r"[0-9a-fA-F-]{36}", valor) else None


def registrar_historico_fiscal(
    regra_id: str,
    usuario: Dict[str, Any],
    acao: str,
    antes: Optional[Dict[str, Any]] = None,
    depois: Optional[Dict[str, Any]] = None,
    observacoes: Optional[str] = None
):
    payload = {
        "regra_id": regra_id,
        "usuario_id": usuario_id_uuid_ou_none(usuario),
        "usuario_nome": usuario.get("nome") or usuario.get("email") or "Sistema",
        "acao": acao,
        "antes": antes,
        "depois": depois,
        "observacoes": observacoes,
        "created_at": datetime.now().isoformat()
    }
    supabase_insert("fiscal_historico", payload)


def buscar_regra_fiscal_por_id(regra_id: str) -> Dict[str, Any]:
    registros = supabase_get(
        "fiscal_regras",
        {"id": f"eq.{regra_id}", "select": "*", "limit": "1"}
    )
    if not registros:
        raise HTTPException(status_code=404, detail="Regra tributária não encontrada.")
    return registros[0]


def buscar_produtos_da_regra(regra_id: str) -> List[str]:
    vinculos = supabase_get(
        "fiscal_regras_produtos",
        {
            "regra_id": f"eq.{regra_id}",
            "select": "produto_id",
            "order": "created_at.asc"
        }
    )
    return [safe_str(item.get("produto_id")) for item in vinculos if item.get("produto_id")]


def substituir_produtos_da_regra(regra_id: str, produtos: Optional[List[str]]):
    if produtos is None:
        return

    supabase_delete("fiscal_regras_produtos", {"regra_id": f"eq.{regra_id}"})
    produtos_limpos = list(dict.fromkeys(
        safe_str(produto).strip() for produto in produtos if safe_str(produto).strip()
    ))
    if produtos_limpos:
        supabase_insert(
            "fiscal_regras_produtos",
            [{"regra_id": regra_id, "produto_id": produto} for produto in produtos_limpos]
        )


def validar_acesso_regra_fiscal(regra: Dict[str, Any], usuario: Dict[str, Any]):
    filial_usuario = normalizar_filial(usuario.get("filial") or "sp")
    filial_regra = normalizar_filial(regra.get("filial") or "sp")
    if filial_usuario != "all" and filial_regra not in {filial_usuario, "all"}:
        raise HTTPException(status_code=403, detail="Usuário sem permissão para acessar esta regra.")


# ============================================================
# ROTAS — CRUD DE REGRAS TRIBUTÁRIAS
# ============================================================

@app.get("/fiscal/regras")
def listar_regras_fiscais(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    regime_tributario: Optional[str] = Query(None),
    tipo_operacao: Optional[str] = Query(None),
    ncm: Optional[str] = Query(None),
    ativa: Optional[bool] = Query(None),
    busca: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial_resolvida = resolver_filial_autorizada(filial, usuario, permitir_all=True)
    params: Dict[str, str] = {
        "select": "*",
        "order": "prioridade.asc,nome.asc",
        "limit": str(limit),
        "offset": str(offset)
    }

    if filial_resolvida != "all":
        # Regras globais também valem para a filial selecionada.
        params["filial"] = f"in.({filial_resolvida},all)"
    if regime_tributario:
        params["regime_tributario"] = f"eq.{safe_str(regime_tributario).lower().strip()}"
    if tipo_operacao:
        params["tipo_operacao"] = f"eq.{safe_str(tipo_operacao).lower().strip()}"
    if ncm:
        params["ncm"] = f"eq.{normalizar_codigo_numerico(ncm, 8)}"
    if ativa is not None:
        params["ativa"] = f"eq.{str(ativa).lower()}"
    if busca and safe_str(busca).strip():
        termo = safe_str(busca).strip().replace(",", " ")
        params["or"] = f"(nome.ilike.*{termo}*,descricao.ilike.*{termo}*,ncm.ilike.*{termo}*)"

    regras = supabase_get("fiscal_regras", params)
    return {
        "status": "ok",
        "filial": filial_resolvida,
        "quantidade": len(regras),
        "dados": regras
    }


@app.get("/fiscal/regras/{regra_id}")
def obter_regra_fiscal(
    regra_id: str,
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    regra = buscar_regra_fiscal_por_id(regra_id)
    validar_acesso_regra_fiscal(regra, usuario)
    regra["produtos"] = buscar_produtos_da_regra(regra_id)
    return {"status": "ok", "dados": regra}


@app.post("/fiscal/regras", status_code=201)
def criar_regra_fiscal(
    body: FiscalRegraCriar,
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    payload = modelo_para_dict(body)
    produtos = payload.pop("produtos", [])
    payload = validar_payload_regra_fiscal(payload, parcial=False)

    filial_regra = resolver_filial_autorizada(payload.get("filial"), usuario, permitir_all=True)
    payload["filial"] = filial_regra
    payload["created_by"] = usuario_id_uuid_ou_none(usuario)
    payload["updated_by"] = usuario_id_uuid_ou_none(usuario)
    payload["created_at"] = datetime.now().isoformat()
    payload["updated_at"] = datetime.now().isoformat()

    criado = supabase_insert("fiscal_regras", payload)
    if not criado:
        raise HTTPException(status_code=500, detail="Supabase não retornou a regra criada.")

    regra = criado[0] if isinstance(criado, list) else criado
    regra_id = safe_str(regra.get("id"))
    substituir_produtos_da_regra(regra_id, produtos)
    regra["produtos"] = buscar_produtos_da_regra(regra_id)
    registrar_historico_fiscal(regra_id, usuario, "criou", depois=regra)

    return {"status": "ok", "mensagem": "Regra tributária criada.", "dados": regra}


@app.put("/fiscal/regras/{regra_id}")
def atualizar_regra_fiscal(
    regra_id: str,
    body: FiscalRegraAtualizar,
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    anterior = buscar_regra_fiscal_por_id(regra_id)
    validar_acesso_regra_fiscal(anterior, usuario)
    anterior_completo = dict(anterior)
    anterior_completo["produtos"] = buscar_produtos_da_regra(regra_id)

    payload = modelo_para_dict(body, exclude_unset=True)
    produtos_informados = "produtos" in payload
    produtos = payload.pop("produtos", None)
    payload = validar_payload_regra_fiscal(payload, parcial=True)

    if "filial" in payload:
        payload["filial"] = resolver_filial_autorizada(payload["filial"], usuario, permitir_all=True)

    if not payload and not produtos_informados:
        raise HTTPException(status_code=400, detail="Nenhum campo foi informado para atualização.")

    if payload:
        payload["updated_by"] = usuario_id_uuid_ou_none(usuario)
        payload["updated_at"] = datetime.now().isoformat()
        atualizado = supabase_patch("fiscal_regras", {"id": f"eq.{regra_id}"}, payload)
        regra = atualizado[0] if isinstance(atualizado, list) and atualizado else buscar_regra_fiscal_por_id(regra_id)
    else:
        regra = buscar_regra_fiscal_por_id(regra_id)

    if produtos_informados:
        substituir_produtos_da_regra(regra_id, produtos)

    regra = buscar_regra_fiscal_por_id(regra_id)
    regra["produtos"] = buscar_produtos_da_regra(regra_id)

    acao = "editou"
    if anterior.get("ativa") is False and regra.get("ativa") is True:
        acao = "ativou"
    elif anterior.get("ativa") is True and regra.get("ativa") is False:
        acao = "desativou"

    registrar_historico_fiscal(regra_id, usuario, acao, antes=anterior_completo, depois=regra)
    return {"status": "ok", "mensagem": "Regra tributária atualizada.", "dados": regra}


@app.delete("/fiscal/regras/{regra_id}")
def excluir_regra_fiscal(
    regra_id: str,
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    anterior = buscar_regra_fiscal_por_id(regra_id)
    validar_acesso_regra_fiscal(anterior, usuario)
    anterior["produtos"] = buscar_produtos_da_regra(regra_id)

    # Exclusão lógica: preserva histórico e evita quebrar auditorias antigas.
    atualizado = supabase_patch(
        "fiscal_regras",
        {"id": f"eq.{regra_id}"},
        {
            "ativa": False,
            "updated_by": usuario_id_uuid_ou_none(usuario),
            "updated_at": datetime.now().isoformat()
        }
    )
    regra = atualizado[0] if isinstance(atualizado, list) and atualizado else buscar_regra_fiscal_por_id(regra_id)
    regra["produtos"] = buscar_produtos_da_regra(regra_id)
    registrar_historico_fiscal(
        regra_id, usuario, "desativou", antes=anterior, depois=regra,
        observacoes="Exclusão lógica realizada pelo endpoint DELETE."
    )
    return {
        "status": "ok",
        "mensagem": "Regra tributária desativada com sucesso.",
        "dados": regra
    }


@app.get("/fiscal/regras/{regra_id}/historico")
def listar_historico_regra_fiscal(
    regra_id: str,
    limit: int = Query(100, ge=1, le=500),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    regra = buscar_regra_fiscal_por_id(regra_id)
    validar_acesso_regra_fiscal(regra, usuario)
    dados = supabase_get(
        "fiscal_historico",
        {
            "regra_id": f"eq.{regra_id}",
            "select": "*",
            "order": "created_at.desc",
            "limit": str(limit)
        }
    )
    return {"status": "ok", "quantidade": len(dados), "dados": dados}




# ============================================================
# MOTOR TRIBUTÁRIO
# ============================================================

def _valor_booleano_compativel(valor_regra: Any, valor_entrada: Optional[bool]) -> bool:
    """Campo nulo na regra funciona como curinga; valor definido exige igualdade."""
    if valor_regra is None:
        return True
    if valor_entrada is None:
        return False
    return bool(valor_regra) == bool(valor_entrada)


def _data_vigencia_compativel(regra: Dict[str, Any], data_operacao: date) -> bool:
    inicio = regra.get("vigencia_inicio")
    fim = regra.get("vigencia_fim")
    if inicio and data_operacao < parse_data(safe_str(inicio)[:10]):
        return False
    if fim and data_operacao > parse_data(safe_str(fim)[:10]):
        return False
    return True


def _campo_texto_compativel(valor_regra: Any, valor_entrada: Optional[str]) -> bool:
    """Campo vazio/nulo na regra funciona como curinga."""
    regra_txt = safe_str(valor_regra).strip().upper()
    entrada_txt = safe_str(valor_entrada).strip().upper()
    return not regra_txt or regra_txt == entrada_txt


def _calcular_especificidade_regra(
    regra: Dict[str, Any],
    produto_especifico: bool,
    ncm_entrada: Optional[str],
    uf_origem: str,
    uf_destino: str,
    contexto: Dict[str, Optional[bool]]
) -> int:
    """Quanto maior, mais específica é a regra."""
    pontos = 0
    if produto_especifico:
        pontos += 1000
    if regra.get("ncm") and safe_str(regra.get("ncm")) == safe_str(ncm_entrada):
        pontos += 300
    if regra.get("uf_origem") and safe_str(regra.get("uf_origem")).upper() == uf_origem:
        pontos += 100
    if regra.get("uf_destino") and safe_str(regra.get("uf_destino")).upper() == uf_destino:
        pontos += 100
    if regra.get("categoria_id"):
        pontos += 20
    for campo, valor in contexto.items():
        if regra.get(campo) is not None and valor is not None:
            pontos += 10
    if safe_str(regra.get("filial")).lower() != "all":
        pontos += 5
    return pontos


def resolver_regra_tributaria(
    *,
    produto_id: Optional[str],
    ncm: Optional[str],
    uf_origem: str,
    uf_destino: str,
    operacao: str,
    regime: str,
    filial: str,
    consumidor_final: Optional[bool],
    contribuinte_icms: Optional[bool],
    pessoa_fisica: Optional[bool],
    marketplace: Optional[bool],
    data_operacao: date
) -> Dict[str, Any]:
    """Resolve a melhor regra ativa usando especificidade e prioridade."""
    params: Dict[str, str] = {
        "select": "*",
        "ativa": "eq.true",
        "regime_tributario": f"eq.{regime}",
        "tipo_operacao": f"eq.{operacao}",
        "order": "prioridade.asc,created_at.desc",
        "limit": "1000",
    }
    if filial != "all":
        params["filial"] = f"in.({filial},all)"

    regras = supabase_get("fiscal_regras", params)

    ids_regras_produto = set()
    if produto_id:
        vinculos = supabase_get(
            "fiscal_regras_produtos",
            {
                "select": "regra_id",
                "produto_id": f"eq.{produto_id}",
                "limit": "1000",
            },
        )
        ids_regras_produto = {safe_str(v.get("regra_id")) for v in vinculos}

    contexto = {
        "consumidor_final": consumidor_final,
        "contribuinte_icms": contribuinte_icms,
        "pessoa_fisica": pessoa_fisica,
        "marketplace": marketplace,
    }

    candidatas = []
    descartes = {
        "vigencia": 0, "ncm": 0, "uf": 0, "contexto": 0, "produto": 0
    }

    for regra in regras:
        regra_id = safe_str(regra.get("id"))
        tem_vinculos = bool(supabase_get(
            "fiscal_regras_produtos",
            {"select": "id", "regra_id": f"eq.{regra_id}", "limit": "1"}
        ))
        produto_especifico = regra_id in ids_regras_produto

        # Regra ligada a produto não pode ser aplicada a outro produto.
        if tem_vinculos and not produto_especifico:
            descartes["produto"] += 1
            continue
        if not _data_vigencia_compativel(regra, data_operacao):
            descartes["vigencia"] += 1
            continue
        if not _campo_texto_compativel(regra.get("ncm"), ncm):
            descartes["ncm"] += 1
            continue
        if not _campo_texto_compativel(regra.get("uf_origem"), uf_origem):
            descartes["uf"] += 1
            continue
        if not _campo_texto_compativel(regra.get("uf_destino"), uf_destino):
            descartes["uf"] += 1
            continue
        if not all(_valor_booleano_compativel(regra.get(c), v) for c, v in contexto.items()):
            descartes["contexto"] += 1
            continue

        especificidade = _calcular_especificidade_regra(
            regra, produto_especifico, ncm, uf_origem, uf_destino, contexto
        )
        prioridade = int(regra.get("prioridade") or 100)
        candidatas.append({
            "regra": regra,
            "produto_especifico": produto_especifico,
            "especificidade": especificidade,
            "prioridade": prioridade,
        })

    if not candidatas:
        return {
            "encontrou": False,
            "regra": None,
            "criterio": None,
            "total_regras_avaliadas": len(regras),
            "descartes": descartes,
        }

    candidatas.sort(key=lambda item: (-item["especificidade"], item["prioridade"], safe_str(item["regra"].get("created_at"))))
    vencedora = candidatas[0]
    regra = vencedora["regra"]

    if vencedora["produto_especifico"]:
        criterio = "produto"
    elif regra.get("ncm") and regra.get("uf_origem") and regra.get("uf_destino"):
        criterio = "ncm_uf"
    elif regra.get("ncm"):
        criterio = "ncm"
    else:
        criterio = "geral"

    return {
        "encontrou": True,
        "regra": regra,
        "criterio": criterio,
        "especificidade": vencedora["especificidade"],
        "prioridade": vencedora["prioridade"],
        "total_regras_avaliadas": len(regras),
        "total_candidatas": len(candidatas),
        "descartes": descartes,
    }


@app.post("/fiscal/motor/simular")
def simular_motor_tributario(
    body: FiscalMotorSimularBody,
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    payload = modelo_para_dict(body)
    regime = safe_str(payload.get("regime")).strip().lower()
    operacao = safe_str(payload.get("operacao") or "venda").strip().lower()
    if regime not in REGIMES_TRIBUTARIOS_VALIDOS:
        raise HTTPException(status_code=400, detail="Regime tributário inválido.")
    if operacao not in TIPOS_OPERACAO_VALIDOS:
        raise HTTPException(status_code=400, detail="Tipo de operação inválido.")

    uf_origem = safe_str(payload.get("uf_origem")).strip().upper()
    uf_destino = safe_str(payload.get("uf_destino")).strip().upper()
    if uf_origem not in UFS_VALIDAS or uf_destino not in UFS_VALIDAS:
        raise HTTPException(status_code=400, detail="UF de origem ou destino inválida.")

    ncm = normalizar_codigo_numerico(payload.get("ncm"), 8)
    if ncm and len(ncm) != 8:
        raise HTTPException(status_code=400, detail="NCM deve possuir exatamente 8 dígitos.")

    filial = resolver_filial_autorizada(payload.get("filial"), usuario, permitir_all=True)
    data_operacao = parse_data(payload["data_operacao"]) if payload.get("data_operacao") else hoje_br()

    resultado = resolver_regra_tributaria(
        produto_id=safe_str(payload.get("produto_id")).strip() or None,
        ncm=ncm,
        uf_origem=uf_origem,
        uf_destino=uf_destino,
        operacao=operacao,
        regime=regime,
        filial=filial,
        consumidor_final=payload.get("consumidor_final"),
        contribuinte_icms=payload.get("contribuinte_icms"),
        pessoa_fisica=payload.get("pessoa_fisica"),
        marketplace=payload.get("marketplace"),
        data_operacao=data_operacao,
    )

    if not resultado["encontrou"]:
        return {
            "status": "sem_regra",
            "mensagem": "Nenhuma regra tributária compatível foi encontrada.",
            "entrada": {**payload, "ncm": ncm, "filial": filial, "data_operacao": data_operacao.isoformat()},
            "diagnostico": resultado,
        }

    regra = resultado["regra"]
    return {
        "status": "ok",
        "encontrou": True,
        "criterio": resultado["criterio"],
        "regra_id": regra.get("id"),
        "regra_nome": regra.get("nome"),
        "filial_regra": regra.get("filial"),
        "cfop": regra.get("cfop"),
        "cst_icms": regra.get("cst_icms"),
        "csosn": regra.get("csosn"),
        "aliquota_icms": regra.get("aliquota_icms") or 0,
        "reducao_bc": regra.get("reducao_bc") or 0,
        "tem_st": bool(regra.get("tem_st")),
        "mva": regra.get("mva") or 0,
        "aliquota_fcp": regra.get("aliquota_fcp") or 0,
        "cst_pis": regra.get("cst_pis"),
        "aliquota_pis": regra.get("aliquota_pis") or 0,
        "cst_cofins": regra.get("cst_cofins"),
        "aliquota_cofins": regra.get("aliquota_cofins") or 0,
        "cst_ipi": regra.get("cst_ipi"),
        "aliquota_ipi": regra.get("aliquota_ipi") or 0,
        "vigencia_inicio": regra.get("vigencia_inicio"),
        "vigencia_fim": regra.get("vigencia_fim"),
        "motor": {
            "especificidade": resultado["especificidade"],
            "prioridade": resultado["prioridade"],
            "total_regras_avaliadas": resultado["total_regras_avaliadas"],
            "total_candidatas": resultado["total_candidatas"],
        },
    }


# ============================================================
# ROTAS BÁSICAS
# ============================================================

@app.get("/")
def home():
    return {
        "status": "online",
        "app": "MHM Dashboard Tiny API",
        "version": "2.5.0"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "tiny_token_ok": bool(TINY_TOKEN),
        "supabase_url_ok": bool(SUPABASE_URL),
        "supabase_key_ok": bool(SUPABASE_SERVICE_ROLE_KEY),
        "jwt_auth_enabled": JWT_AUTH_ENABLED
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
            "/db/faturamento-canais",
            "/db/faturamento-origens",
            "/db/sync-logs"
        ],
        "configuracoes": [
            "/configuracoes/data-inicio-tiny"
        ],
        "auth": [
            "/auth/me"
        ],
        "fiscal": [
            "/fiscal/xml/resumo",
            "/fiscal/xml/nota",
            "/fiscal/xml/download",
            "/fiscal/regras",
            "/fiscal/regras/{regra_id}",
            "/fiscal/regras/{regra_id}/historico",
            "/fiscal/motor/simular"
        ]
    }


# ============================================================
# ROTAS SYNC TINY
# ============================================================

@app.post("/sync/tiny-dia")
def sync_tiny_dia(
    data: str = Query(..., description="Data no formato YYYY-MM-DD"),
    filial: str = Query("sp", description="sp ou mg")
):
    data_ref = parse_data(data)
    return sincronizar_periodo(data_ref, data_ref, tipo="dia", filial=filial)

@app.post("/sync/tiny-mes")
def sync_tiny_mes(
    ano: int = Query(...),
    mes: int = Query(...),
    filial: str = Query("sp", description="sp ou mg")
):
    if mes < 1 or mes > 12:
        raise HTTPException(status_code=400, detail="Mês inválido.")

    data_inicio = date(ano, mes, 1)
    ultimo_dia = monthrange(ano, mes)[1]
    data_fim = date(ano, mes, ultimo_dia)

    return sincronizar_periodo(data_inicio, data_fim, tipo="mes", filial=filial)


@app.post("/sync/tiny-ano")
def sync_tiny_ano(
    ano: int = Query(...),
    filial: str = Query("sp", description="sp ou mg")
):
    data_inicio = date(ano, 1, 1)
    data_fim = date(ano, 12, 31)

    hoje = hoje_br()
    if data_fim > hoje:
        data_fim = hoje

    return sincronizar_periodo(data_inicio, data_fim, tipo="ano", filial=filial)


@app.post("/sync/tiny-periodo")
def sync_tiny_periodo(
    body: PeriodoBody,
    filial: str = Query("sp", description="sp ou mg")
):
    data_inicio = parse_data(body.data_inicio)
    data_fim = parse_data(body.data_fim)

    if data_fim < data_inicio:
        raise HTTPException(
            status_code=400,
            detail="data_fim não pode ser menor que data_inicio."
        )

    return sincronizar_periodo(data_inicio, data_fim, tipo="periodo", filial=filial)


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
    data_fim: date,
    filial: str = "sp"
) -> List[Dict[str, Any]]:
    validar_env()

    url = f"{SUPABASE_URL}/rest/v1/pedidos"

    params = [
        ("select", "*"),
        ("data_pedido", f"gte.{data_inicio.isoformat()}"),
        ("data_pedido", f"lte.{data_fim.isoformat()}"),
        ("order", "data_pedido.asc")
    ]
    params = adicionar_filtro_filial_params(params, filial)

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
    data_fim: date,
    filial: str = "sp"
) -> List[Dict[str, Any]]:
    validar_env()

    filial_normalizada = normalizar_filial(filial)

    url = f"{SUPABASE_URL}/rest/v1/itens_pedido"

    params = [
        ("select", "*"),
        ("data_pedido", f"gte.{data_inicio.isoformat()}"),
        ("data_pedido", f"lte.{data_fim.isoformat()}"),
        ("order", "data_pedido.asc")
    ]

    if filial_normalizada != "all":
        params.append(("filial", f"eq.{filial_normalizada}"))

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
                "resposta": response.text,
                "filial": filial_normalizada
            }
        )

    return response.json()


def calcular_resumo_periodo_banco(
    data_inicio: date,
    data_fim: date,
    filial: str = "sp"
) -> Dict[str, Any]:
    pedidos = buscar_pedidos_banco_periodo_corrigido(data_inicio, data_fim, filial=filial)
    itens = buscar_itens_banco_periodo_corrigido(data_inicio, data_fim, filial=filial)

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
    data: Optional[str] = Query(None),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    params = {
        "select": "*",
        "order": "data.desc"
    }

    if data:
        params["data"] = f"eq.{data}"

    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params["filial"] = f"eq.{filial_normalizada}"

    return {
        "status": "ok",
        "dados": supabase_get("resumo_diario", params)
    }


@app.get("/db/resumo-mensal")
def db_resumo_mensal(
    ano: Optional[int] = Query(None),
    mes: Optional[int] = Query(None),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    params = {
        "select": "*",
        "order": "ano.desc,mes.desc"
    }

    if ano:
        params["ano"] = f"eq.{ano}"

    if mes:
        params["mes"] = f"eq.{mes}"

    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params["filial"] = f"eq.{filial_normalizada}"

    return {
        "status": "ok",
        "dados": supabase_get("resumo_mensal", params)
    }


@app.get("/db/resumo-anual")
def db_resumo_anual(
    ano: Optional[int] = Query(None),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    params = {
        "select": "*",
        "order": "ano.desc"
    }

    if ano:
        params["ano"] = f"eq.{ano}"

    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params["filial"] = f"eq.{filial_normalizada}"

    return {
        "status": "ok",
        "dados": supabase_get("resumo_anual", params)
    }


@app.get("/db/dashboard-resumo")
def db_dashboard_resumo(
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    hoje = hoje_br()
    inicio_30 = hoje - timedelta(days=30)

    params_hoje = {
        "data": f"eq.{hoje.isoformat()}",
        "select": "*",
        "limit": "1"
    }
    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params_hoje["filial"] = f"eq.{filial_normalizada}"

    resumo_hoje = supabase_get(
        "resumo_diario",
        params_hoje
    )

    # Calcula o mês atual diretamente pela soma do resumo_diario.
    # A tabela resumo_mensal só é atualizada quando /sync/tiny-mes é executado,
    # então ela pode ficar vazia ou desatualizada durante o mês.
    inicio_mes = date(hoje.year, hoje.month, 1)

    validar_env()
    url_mes = f"{SUPABASE_URL}/rest/v1/resumo_diario"
    params_mes = [
        ("select", "data_resumo,faturamento_total,total_pedidos,total_unidades_vendidas,total_produtos_diferentes"),
        ("data_resumo", f"gte.{inicio_mes.isoformat()}"),
        ("data_resumo", f"lte.{hoje.isoformat()}"),
        ("order", "data_resumo.asc"),
        ("limit", "1000"),
    ]
    params_mes = adicionar_filtro_filial_params(params_mes, filial)

    resp_mes = requests.get(
        url_mes,
        headers=supabase_headers(),
        params=params_mes,
        timeout=60
    )

    if resp_mes.status_code >= 400:
        raise HTTPException(
            status_code=500,
            detail={
                "erro": "Erro ao calcular o resumo do mês atual.",
                "status_code": resp_mes.status_code,
                "resposta": resp_mes.text,
                "filial": filial_normalizada
            }
        )

    registros_mes = resp_mes.json()

    faturamento_mes = round(
        sum(float(r.get("faturamento_total") or 0) for r in registros_mes),
        2
    )
    total_pedidos_mes = sum(
        int(r.get("total_pedidos") or 0) for r in registros_mes
    )
    total_unidades_mes = round(
        sum(float(r.get("total_unidades_vendidas") or 0) for r in registros_mes),
        2
    )
    total_produtos_diferentes_mes = sum(
        int(r.get("total_produtos_diferentes") or 0) for r in registros_mes
    )
    ticket_medio_mes = round(
        faturamento_mes / total_pedidos_mes,
        2
    ) if total_pedidos_mes else 0.0

    resumo_mes = {
        "ano": hoje.year,
        "mes": hoje.month,
        "data_inicio": inicio_mes.isoformat(),
        "data_fim": hoje.isoformat(),
        "faturamento": faturamento_mes,
        "faturamento_total": faturamento_mes,
        "total_pedidos": total_pedidos_mes,
        "ticket_medio": ticket_medio_mes,
        "total_unidades_vendidas": total_unidades_mes,
        "total_produtos_diferentes": total_produtos_diferentes_mes,
        "filial": filial_normalizada
    }

    params_logs = {
        "select": "*",
        "order": "created_at.desc",
        "limit": "10"
    }
    if filial_normalizada != "all":
        params_logs["filial"] = f"eq.{filial_normalizada}"

    ultimos_logs = supabase_get(
        "sync_logs",
        params_logs
    )

    resumo_30 = calcular_resumo_periodo_banco(inicio_30, hoje, filial=filial)

    return {
        "status": "ok",
        "hoje": resumo_hoje[0] if resumo_hoje else None,
        "mes_atual": resumo_mes,
        "ultimos_30_dias": resumo_30,
        "sync_logs": ultimos_logs
    }


@app.get("/db/sync-logs")
def db_sync_logs(
    limit: int = Query(20),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    params = {
        "select": "*",
        "order": "created_at.desc",
        "limit": str(limit)
    }

    filial_normalizada = normalizar_filial(filial)
    if filial_normalizada != "all":
        params["filial"] = f"eq.{filial_normalizada}"

    return {
        "status": "ok",
        "filial": filial_normalizada,
        "dados": supabase_get("sync_logs", params)
    }


@app.get("/db/resumo-periodo")
def db_resumo_periodo(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD"),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
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
    params = adicionar_filtro_filial_params(params, filial)
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
    limite: int = Query(10),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    inicio = parse_data(data_inicio)
    fim = parse_data(data_fim)

    if fim < inicio:
        raise HTTPException(
            status_code=400,
            detail="data_fim não pode ser menor que data_inicio."
        )

    pedidos = buscar_pedidos_banco_periodo_corrigido(inicio, fim, filial=filial)
    itens = buscar_itens_banco_periodo_corrigido(inicio, fim, filial=filial)

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


@app.get("/db/faturamento-canais")
def db_faturamento_canais(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD"),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    inicio = parse_data(data_inicio)
    fim = parse_data(data_fim)

    if fim < inicio:
        raise HTTPException(
            status_code=400,
            detail="data_fim não pode ser menor que data_inicio."
        )

    pedidos = buscar_pedidos_banco_periodo_corrigido(inicio, fim, filial=filial)

    resultado = {
        "PDV": {
            "pedidos": 0,
            "faturamento": 0.0,
            "ticket_medio": 0.0,
            "percentual": 0.0
        },
        "COMERCIAL": {
            "pedidos": 0,
            "faturamento": 0.0,
            "ticket_medio": 0.0,
            "percentual": 0.0
        }
    }

    for pedido in pedidos:
        canal = safe_str(pedido.get("canal_venda") or "COMERCIAL").upper()

        if canal not in resultado:
            canal = "COMERCIAL"

        resultado[canal]["pedidos"] += 1
        resultado[canal]["faturamento"] += float(pedido.get("valor_total") or 0)

    total = round(
        resultado["PDV"]["faturamento"] + resultado["COMERCIAL"]["faturamento"],
        2
    )

    for canal in resultado:
        qtd = resultado[canal]["pedidos"]
        faturamento = resultado[canal]["faturamento"]

        resultado[canal]["faturamento"] = round(faturamento, 2)
        resultado[canal]["ticket_medio"] = round(faturamento / qtd, 2) if qtd else 0.0
        resultado[canal]["percentual"] = round((faturamento / total * 100), 2) if total else 0.0

    return {
        "status": "ok",
        "data_inicio": inicio.isoformat(),
        "data_fim": fim.isoformat(),
        "filial": normalizar_filial(filial),
        "pdv": resultado["PDV"],
        "comercial": resultado["COMERCIAL"],
        "total": total
    }



@app.get("/db/faturamento-origens")
def db_faturamento_origens(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD"),
    filial: Optional[str] = Query(None, description="sp, mg ou all"),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual)
):
    filial = resolver_filial_autorizada(filial, usuario)
    inicio = parse_data(data_inicio)
    fim = parse_data(data_fim)

    if fim < inicio:
        raise HTTPException(status_code=400, detail="data_fim não pode ser menor que data_inicio.")

    pedidos = buscar_pedidos_banco_periodo_corrigido(inicio, fim, filial=filial)
    agrupado: Dict[str, Dict[str, Any]] = {}

    for pedido in pedidos:
        origem = safe_str(pedido.get("origem_cliente") or "Sem origem").strip() or "Sem origem"
        canal = safe_str(pedido.get("canal_venda") or "COMERCIAL").upper().strip()
        if canal not in ["PDV", "COMERCIAL"]:
            canal = "COMERCIAL"

        valor = float(pedido.get("valor_total") or 0)

        if origem not in agrupado:
            agrupado[origem] = {
                "origem": origem,
                "pedidos": 0,
                "faturamento": 0.0,
                "ticket_medio": 0.0,
                "percentual_pedidos": 0.0,
                "percentual_faturamento": 0.0,
                "pdv": {"pedidos": 0, "faturamento": 0.0, "ticket_medio": 0.0},
                "comercial": {"pedidos": 0, "faturamento": 0.0, "ticket_medio": 0.0}
            }

        item = agrupado[origem]
        item["pedidos"] += 1
        item["faturamento"] += valor

        chave_canal = "pdv" if canal == "PDV" else "comercial"
        item[chave_canal]["pedidos"] += 1
        item[chave_canal]["faturamento"] += valor

    total_pedidos = sum(item["pedidos"] for item in agrupado.values())
    total_faturamento = round(sum(item["faturamento"] for item in agrupado.values()), 2)

    origens = []
    for item in agrupado.values():
        pedidos_origem = item["pedidos"]
        faturamento_origem = item["faturamento"]

        item["faturamento"] = round(faturamento_origem, 2)
        item["ticket_medio"] = round(faturamento_origem / pedidos_origem, 2) if pedidos_origem else 0.0
        item["percentual_pedidos"] = round(pedidos_origem / total_pedidos * 100, 2) if total_pedidos else 0.0
        item["percentual_faturamento"] = round(faturamento_origem / total_faturamento * 100, 2) if total_faturamento else 0.0

        for chave in ["pdv", "comercial"]:
            qtd = item[chave]["pedidos"]
            fat = item[chave]["faturamento"]
            item[chave]["faturamento"] = round(fat, 2)
            item[chave]["ticket_medio"] = round(fat / qtd, 2) if qtd else 0.0

        origens.append(item)

    origens.sort(key=lambda item: item["faturamento"], reverse=True)

    sem_origem = next(
        (item for item in origens if item["origem"] == "Sem origem"),
        {"pedidos": 0, "faturamento": 0.0}
    )

    maior_ticket = max(origens, key=lambda item: item["ticket_medio"])["origem"] if origens else None

    return {
        "status": "ok",
        "data_inicio": inicio.isoformat(),
        "data_fim": fim.isoformat(),
        "filial": normalizar_filial(filial),
        "total": {
            "pedidos": total_pedidos,
            "faturamento": total_faturamento,
            "ticket_medio": round(total_faturamento / total_pedidos, 2) if total_pedidos else 0.0
        },
        "destaques": {
            "principal_origem": origens[0]["origem"] if origens else None,
            "maior_ticket_medio": maior_ticket,
            "pedidos_sem_origem": sem_origem["pedidos"],
            "faturamento_sem_origem": sem_origem["faturamento"]
        },
        "origens": origens
    }



# ============================================================
# ROTAS FISCAIS — NF-e, NFC-e E XML
# ============================================================

@app.get("/fiscal/xml/resumo")
def fiscal_xml_resumo(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD"),
    filial: Optional[str] = Query(None, description="sp ou mg"),
    incluir_notas: bool = Query(False, description="Inclui a lista de notas no retorno."),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual),
):
    filial_resolvida = resolver_filial_autorizada(filial, usuario, permitir_all=False)
    inicio = parse_data(data_inicio)
    fim = parse_data(data_fim)

    if fim < inicio:
        raise HTTPException(status_code=400, detail="data_fim não pode ser menor que data_inicio.")

    if (fim - inicio).days > 366:
        raise HTTPException(status_code=400, detail="O período fiscal máximo é de 367 dias.")

    notas = buscar_notas_fiscais_periodo_tiny(
        inicio,
        fim,
        filial=filial_resolvida,
    )
    resumo = resumir_notas_fiscais(notas)

    resposta: Dict[str, Any] = {
        "status": "ok",
        "filial": filial_resolvida,
        "data_inicio": inicio.isoformat(),
        "data_fim": fim.isoformat(),
        **resumo,
    }

    if incluir_notas:
        resposta["notas"] = notas

    return resposta


@app.get("/fiscal/xml/nota")
def fiscal_xml_nota(
    id_nota: str = Query(..., min_length=1, description="ID interno da nota fiscal no Tiny/Olist."),
    filial: Optional[str] = Query(None, description="sp ou mg"),
    incluir_cancelamento: bool = Query(False),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual),
):
    filial_resolvida = resolver_filial_autorizada(filial, usuario, permitir_all=False)
    resultado = obter_xml_nota_fiscal_tiny(id_nota, filial=filial_resolvida)

    if incluir_cancelamento and resultado.get("xml_cancelamento"):
        conteudo = resultado["xml_cancelamento"] or ""
        sufixo = "cancelamento"
    else:
        conteudo = resultado["xml_nfe"] or ""
        sufixo = "nfe"

    nome = f"xml_{nome_seguro_arquivo(id_nota)}_{sufixo}.xml"
    return Response(
        content=conteudo.encode("utf-8"),
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


@app.get("/fiscal/xml/download")
def fiscal_xml_download(
    data_inicio: str = Query(..., description="YYYY-MM-DD"),
    data_fim: str = Query(..., description="YYYY-MM-DD"),
    filial: Optional[str] = Query(None, description="sp ou mg"),
    tipo: str = Query("ambos", description="nfe, nfce ou ambos"),
    limite: int = Query(500, ge=1, le=2000, description="Máximo de XMLs neste ZIP."),
    incluir_cancelamentos: bool = Query(True),
    usuario: Dict[str, Any] = Depends(obter_usuario_atual),
):
    filial_resolvida = resolver_filial_autorizada(filial, usuario, permitir_all=False)
    inicio = parse_data(data_inicio)
    fim = parse_data(data_fim)
    tipo_normalizado = safe_str(tipo).lower().strip()

    if fim < inicio:
        raise HTTPException(status_code=400, detail="data_fim não pode ser menor que data_inicio.")
    if tipo_normalizado not in ["nfe", "nfce", "ambos"]:
        raise HTTPException(status_code=400, detail="tipo deve ser nfe, nfce ou ambos.")
    if (fim - inicio).days > 92:
        raise HTTPException(
            status_code=400,
            detail="Para download em ZIP, selecione no máximo 93 dias por operação.",
        )

    notas = buscar_notas_fiscais_periodo_tiny(
        inicio,
        fim,
        filial=filial_resolvida,
    )

    notas_filtradas = []
    for nota in notas:
        modelo = modelo_nota_pela_chave(nota.get("chave_acesso"))
        if tipo_normalizado == "ambos" and modelo in ["nfe", "nfce"]:
            notas_filtradas.append(nota)
        elif modelo == tipo_normalizado:
            notas_filtradas.append(nota)

    total_encontrado = len(notas_filtradas)
    notas_processadas = notas_filtradas[:limite]

    buffer = io.BytesIO()
    erros: List[Dict[str, Any]] = []
    baixados = 0

    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as arquivo_zip:
        for indice, nota in enumerate(notas_processadas, start=1):
            id_nota = safe_str(nota.get("id")).strip()
            modelo = modelo_nota_pela_chave(nota.get("chave_acesso"))
            pasta = "NFE" if modelo == "nfe" else "NFCE"

            if not id_nota:
                erros.append({"id": None, "numero": nota.get("numero"), "erro": "Nota sem ID interno."})
                continue

            try:
                xmls = obter_xml_nota_fiscal_tiny(id_nota, filial=filial_resolvida)
                numero = nome_seguro_arquivo(nota.get("numero"), id_nota)
                serie = nome_seguro_arquivo(nota.get("serie"), "sem_serie")
                chave = nome_seguro_arquivo(nota.get("chave_acesso"), id_nota)
                nome_base = f"{numero}_serie_{serie}_{chave}"

                arquivo_zip.writestr(
                    f"{pasta}/{nome_base}.xml",
                    (xmls.get("xml_nfe") or "").encode("utf-8"),
                )
                baixados += 1

                if incluir_cancelamentos and xmls.get("xml_cancelamento"):
                    arquivo_zip.writestr(
                        f"{pasta}/CANCELAMENTOS/{nome_base}_cancelamento.xml",
                        (xmls.get("xml_cancelamento") or "").encode("utf-8"),
                    )

            except HTTPException as exc:
                erros.append({
                    "id": id_nota,
                    "numero": nota.get("numero"),
                    "erro": exc.detail,
                })
            except Exception as exc:
                erros.append({
                    "id": id_nota,
                    "numero": nota.get("numero"),
                    "erro": str(exc),
                })

            # Pequena pausa reduz bloqueios da API 2.0 em downloads grandes.
            if indice < len(notas_processadas):
                time.sleep(0.25)

        manifesto = {
            "filial": filial_resolvida,
            "data_inicio": inicio.isoformat(),
            "data_fim": fim.isoformat(),
            "tipo": tipo_normalizado,
            "total_encontrado": total_encontrado,
            "limite_solicitado": limite,
            "total_processado": len(notas_processadas),
            "xmls_baixados": baixados,
            "erros": len(erros),
            "download_parcial": total_encontrado > limite,
            "gerado_em": datetime.now().isoformat(),
        }
        arquivo_zip.writestr(
            "manifesto.json",
            json.dumps(manifesto, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        if erros:
            arquivo_zip.writestr(
                "erros.json",
                json.dumps(erros, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            )

    buffer.seek(0)
    nome_zip = (
        f"xml_{filial_resolvida}_{tipo_normalizado}_"
        f"{inicio.isoformat()}_{fim.isoformat()}.zip"
    )

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{nome_zip}"',
            "X-XML-Encontrados": str(total_encontrado),
            "X-XML-Baixados": str(baixados),
            "X-XML-Erros": str(len(erros)),
            "X-Download-Parcial": "true" if total_encontrado > limite else "false",
        },
    )


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
