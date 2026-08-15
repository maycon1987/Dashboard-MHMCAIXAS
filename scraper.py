"""
LÚMINO — Radar Tributário
Scraper de notícias tributárias oficiais (RSS Receita Federal + DOU + Querido Diário)
com classificação e resumo via LLM. Saída em JSON pronta para integração na API FastAPI.

Execução:
    python3 scraper.py run --dias 7          # modo produção (agrega tudo)
    python3 scraper.py fontes                # testa conectividade das fontes
    python3 scraper.py run --dias 3 --debug  # modo debug (imprime passos)
"""
import argparse
import json
import logging
import re
import sys
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("lumino-radar")

# ---------------------------------------------------------------------------
# Configuração global
# ---------------------------------------------------------------------------
DEFAULT_KEYWORDS = [
    "simples nacional", "NCM", "alíquota", "reforma tributária", "CBS", "IBS",
    "ISS", "ICMS", "IRPJ", "CSLL", "PIS", "COFINS", "portaria", "instrução normativa",
    "eSocial", "DCTF", "SPED", "nota fiscal eletrônica", "NF-e", "fator R",
    "anexo", "alíquota efetiva", "débito", "crédito tributário",
]
# Keywords extra aplicadas só ao DOU (legislação formal)
DOU_EXTRA_KEYWORDS = ["NCM", "alíquota", "simples", "portaria", "instrução normativa",
                      "regulamento", "tributo", "isenção", "benefício fiscal"]

RSS_RFB = "https://www.gov.br/receitafederal/RSS"
QD_API = "https://api.queridodiario.ok.org.br/gazettes"
DOU_API = "https://www.in.gov.br/consulta/-/buscar/dou"
DOU_WEB = "https://www.in.gov.br/web/dou/-/"

HEADERS = {"User-Agent": "LUMINO Radar Tributario (+https://seusite.com; monitor de noticias publicas)",
           "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"}

LLM_MODEL = "gpt-5-mini"  # barata e suficiente para resumo/classificação; trocar se quiser


# ---------------------------------------------------------------------------
# Fonte 1 — RSS da Receita Federal
# ---------------------------------------------------------------------------
def collect_rss(days: int = 7, debug: bool = False) -> list[dict]:
    """Coleta itens do RSS oficial da Receita Federal publicados nos últimos `days` dias."""
    items: list[dict] = []
    try:
        resp = requests.get(RSS_RFB, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        for item in root.iter("{http://purl.org/rss/1.0/}item"):
            title = item.findtext("{http://purl.org/dc/elements/1.1/}title", "").strip()
            link = item.findtext("{http://purl.org/rss/1.0/}link", "").strip()
            # Alguns itens RDF vêm sem título — derivar do link
            if not title and link:
                slug = link.rstrip("/view").rsplit("/", 1)[-1]
                title = slug.replace("-", " ").replace("_", " ").title()
            date_el = item.find("{http://purl.org/dc/elements/1.1/}date")
            pub_date = date_el.text.strip()[:10] if date_el is not None and date_el.text else ""
            if pub_date and pub_date < cutoff:
                continue
            items.append({
                "fonte": "receita_federal_rss",
                "titulo": title,
                "resumo_original": "",
                "link": link,
                "data": pub_date,
                "texto_completo": title,
            })
        if debug:
            log.info("RSS RFB: %d itens coletados (corte %s)", len(items), cutoff)
    except Exception as e:
        log.warning("RSS RFB indisponível: %s", e)
    return items


# ---------------------------------------------------------------------------
# Fonte 2 — Querido Diário (diários oficiais municipais/estaduais)
# ---------------------------------------------------------------------------
def collect_querido_diario(days: int = 7, limit: int = 30, debug: bool = False) -> list[dict]:
    """Busca menções tributárias nos diários oficiais municipais via API pública do Querido Diário."""
    items: list[dict] = []
    since = (date.today() - timedelta(days=days)).isoformat()
    session = requests.Session()
    session.headers.update(HEADERS)
    seen: set[str] = set()
    for kw in DEFAULT_KEYWORDS[:10]:  # limitar keywords para não exceder rate
        try:
            resp = session.get(QD_API, params={
                "querystring": kw, "published_since": since,
                "size": 10, "excerpt_size": 600, "number_of_excerpts": 2,
            }, timeout=30)
            if resp.status_code != 200:
                continue
            data = resp.json()
            for g in data.get("gazettes", []):
                key = f"{g['territory_id']}:{g['date']}:{kw}"
                if key in seen:
                    continue
                seen.add(key)
                text = "\n".join(g.get("excerpts", []))
                items.append({
                    "fonte": "querido_diario",
                    "titulo": f"[{g.get('territory_name','')}/{g.get('state_code','')}] Menção a '{kw}'",
                    "resumo_original": text[:1500],
                    "link": g.get("txt_url") or g.get("url"),
                    "data": g["date"],
                    "texto_completo": text,
                })
        except Exception as e:
            log.warning("Querido Diário falhou para '%s': %s", kw, e)
        if len(items) >= limit:
            break
    if debug:
        log.info("Querido Diário: %d itens coletados", len(items))
    return items[:limit]


# ---------------------------------------------------------------------------
# Fonte 3 — DOU (Imprensa Nacional) com fallback HTML
# ---------------------------------------------------------------------------
def _dou_fetch_html(query: str, frm: str, to: str) -> Optional[str]:  # noqa: C901
    """Tenta a API do DOU; se falhar, tenta página HTML de busca. Retorna HTML ou None."""
    params = {
        "q": query, "exactDate": "personalizado",
        "publishFrom": frm, "publishTo": to, "sortType": "0", "s": ["do1"],
    }
    # Tentativa: API JSON embutida (Liferay portlet), com timeout curto e 2 tentativas
    for attempt in range(2):
        try:
            r = requests.get(DOU_API, params=params, headers=HEADERS, timeout=10)
            if r.status_code == 200 and "BuscaDouPortlet_params" in r.text:
                return r.text
            if r.status_code >= 500:
                continue
            # resposta ok sem portlet = página de erro/sem resultados
            return None
        except Exception:
            pass
    return None


def collect_dou(days: int = 7, limit: int = 40, debug: bool = False) -> list[dict]:
    """Busca legislação formal no DOU (Seção 1) usando keywords."""
    items: list[dict] = []
    frm = (date.today() - timedelta(days=days)).strftime("%d-%m-%Y")
    to = date.today().strftime("%d-%m-%Y")
    keywords = DEFAULT_KEYWORDS[:6] + DOU_EXTRA_KEYWORDS
    for kw in keywords:
        if len(items) >= limit:
            break
        try:
            html = _dou_fetch_html(f'"{kw}"', frm, to)
        except Exception as e:
            log.warning("DOU timeout para '%s': %s", kw, e)
            continue
        if not html:
            continue
        try:
            m = re.search(r'BuscaDouPortlet_params[^>]*>(.*?)</script>', html, re.S)
            if not m:
                continue
            data = json.loads(m.group(1))
            for c in data.get("jsonArray", []):
                items.append({
                    "fonte": "dou_secao1",
                    "titulo": c.get("title", ""),
                    "resumo_original": re.sub(r"<[^>]+>", "", c.get("content", ""))[:1200],
                    "link": DOU_WEB + c.get("urlTitle", ""),
                    "data": c.get("pubDate", ""),
                    "texto_completo": re.sub(r"<[^>]+>", "", c.get("content", "")),
                })
        except Exception as e:
            log.warning("Parse DOU falhou para '%s': %s", kw, e)
    if debug:
        log.info("DOU: %d itens coletados", len(items))
    return items[:limit]


# ---------------------------------------------------------------------------
# Camada de Inteligência — classificação e resumo via LLM
# ---------------------------------------------------------------------------
def _batch(items: list[dict]) -> list[dict]:
    """Passa os itens brutos por um LLM para classificar relevância e resumir."""
    if not items:
        return []
    try:
        from openai import OpenAI
        client = OpenAI()  # lê OPENAI_API_KEY do ambiente
    except Exception as e:
        log.error("LLM indisponível: %s", e)
        for it in items:
            it.update({"relevancia": "neutra", "impacto": "informativo",
                       "resumo": it["titulo"], "tags": [], "ncms_afetados": []})
        return items

    SYSTEM = (
        "Você é o analista tributário do LÚMINO. Analise notícias oficiais pensando em uma "
        "empresa brasileira de COMÉRCIO DE MERCADORIAS e E-COMMERCE, atualmente optante pelo "
        "Simples Nacional. O objetivo não é montar um feed tributário genérico, mas detectar "
        "mudanças com potencial de afetar vendas, emissão fiscal, cadastro de produtos, preço, "
        "margem, obrigações acessórias ou tributação de mercadorias. "

        "REGRAS DE RELEVÂNCIA: "
        "Marque 'alta' somente quando houver mudança concreta, obrigação, prazo ou regra com "
        "efeito direto ou muito provável sobre comércio/e-commerce/Simples Nacional, especialmente "
        "ICMS, ICMS-ST, DIFAL, FCP, IPI, PIS/COFINS, CBS, IBS, NCM, CEST, CFOP, NF-e, SPED, "
        "documentos fiscais ou Reforma Tributária aplicável a empresas/comércio. "
        "Marque 'media' quando a mudança puder afetar comércio/e-commerce, mas depender de setor, "
        "produto, UF, vigência ou regulamentação complementar. "
        "Marque 'neutra' quando o conteúdo não tiver efeito prático para comércio de mercadorias/e-commerce. "

        "LICITAÇÕES E COMPRAS PÚBLICAS: editais, licitações, pregões, habilitação, contratos "
        "administrativos, PNCP e compras públicas devem ser SEMPRE classificados como 'neutra' "
        "neste Radar geral, mesmo quando citarem Simples Nacional, retenção ou documentação fiscal. "

        "Também devem ser neutras, salvo relação direta e explícita com a operação comercial: "
        "ITBI, IPTU, previdência de servidor ou ente público, contribuição atuarial, ISS de clínicas, "
        "profissões ou serviços alheios, obras públicas, tributos imobiliários locais e alterações "
        "municipais sem relação com venda de mercadorias. "

        "CONTEÚDO INSUFICIENTE: se o texto disponível for apenas um título, nome de página, nome de PDF "
        "ou trecho curto demais para comprovar a mudança, NÃO invente detalhes de ICMS, CFOP, NF-e, ST, "
        "prazo ou obrigação. Quando não houver base suficiente para um impacto empresarial objetivo, "
        "classifique como 'neutra'. EXCEÇÃO: títulos oficiais da Receita Federal que indiquem claramente "
        "mudança tributária objetiva, como Simples Nacional, regime de caixa, NFS-e, NF-e, reforma tributária "
        "ou tributos empresariais, podem ser classificados por relevância com impacto conservador, sem inventar detalhes. "

        "NCMs: preencha 'ncms_afetados' SOMENTE quando a própria notícia ou norma mencionar um NCM "
        "específico de 8 dígitos ou quando o texto trouxer uma classificação fiscal inequívoca que permita "
        "identificar o NCM sem adivinhação. Normalize cada NCM para exatamente 8 dígitos, sem pontos. "
        "NUNCA invente, estime ou deduza NCM apenas pelo setor, nome genérico de produto ou contexto. "
        "Se não houver base explícita suficiente, retorne ncms_afetados como lista vazia. "

        "No campo impacto, explique objetivamente o efeito empresarial. No resumo, descreva a mudança "
        "sem extrapolar o texto oficial. Use tags curtas e úteis."
    )
    schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "analise_noticias",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "resultados": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "indice": {"type": "integer"},
                                "relevancia": {"type": "string", "enum": ["alta", "media", "neutra"]},
                                "impacto": {"type": "string"},
                                "resumo": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                                "ncms_afetados": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["indice", "relevancia", "impacto", "resumo", "tags", "ncms_afetados"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["resultados"],
                "additionalProperties": False,
            },
        },
    }
    # processar em lotes de 20 para caber no contexto
    out: list[dict] = []
    batch_size = 20
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        user_parts = []
        for idx, it in enumerate(batch):
            user_parts.append(f"[{idx}] Título: {it['titulo']}\nData: {it['data']}\nFonte: {it['fonte']}\nTexto: {it['texto_completo'][:800]}")
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Analise estas notícias:\n" + "\n\n".join(user_parts)},
            ],
            response_format=schema,
        )
        analysis = json.loads(resp.choices[0].message.content)
        for a in analysis["resultados"]:
            idx = a.get("indice", 0)
            if 0 <= idx < len(batch):
                b = batch[idx]
                b.update({
                    "relevancia": a["relevancia"],
                    "impacto": a["impacto"],
                    "resumo": a["resumo"],
                    "tags": a["tags"],
                    "ncms_afetados": a["ncms_afetados"],
                })
                out.append(b)
    return out


# ---------------------------------------------------------------------------
# Agregação e dedupe
# ---------------------------------------------------------------------------

def _normalizar_texto_filtro(valor):
    return re.sub(r"\s+", " ", str(valor or "")).strip().lower()


def _deve_descartar_antes_da_ia(item):
    """
    Remove ruído óbvio antes de consumir tokens da OpenAI.
    Só descarta itens claramente administrativos, vazios ou sem
    conteúdo tributário útil.
    """
    titulo = _normalizar_texto_filtro(item.get("titulo"))
    resumo = _normalizar_texto_filtro(item.get("resumo_original"))
    texto = _normalizar_texto_filtro(item.get("texto_completo"))
    link = _normalizar_texto_filtro(item.get("link"))
    combinado = f"{titulo} {resumo} {texto} {link}"

    if any(x in combinado for x in [
        "pagina em construcao",
        "página em construção",
        "em construcao",
        "em construção",
    ]):
        return True

    if any(x in combinado for x in [
        "aviso de contratacao direta",
        "aviso de contratação direta",
        "licitacao",
        "licitação",
        "pregao",
        "pregão",
        "pncp",
        "portal nacional de contratacoes publicas",
        "portal nacional de contratações públicas",
        "dispensa de licitacao",
        "dispensa de licitação",
        "concorrencia eletronica",
        "concorrência eletrônica",
        "empreitada global",
    ]):
        return True

    apenas_nome_pdf = (
        titulo.endswith(".pdf")
        and not resumo
        and texto == titulo
    )

    if apenas_nome_pdf:
        palavras_tributarias = [
            "icms", "ipi", "pis", "cofins", "simples nacional", "ncm",
            "cest", "cfop", "reforma tributaria", "reforma tributária",
            "cbs", "ibs", "tribut", "fiscal", "aliquota", "alíquota",
            "substituicao tributaria", "substituição tributária",
        ]
        if not any(p in combinado for p in palavras_tributarias):
            return True

    return False


def filtrar_ruido_pre_ia(items):
    mantidos = []
    descartados = 0

    for item in items:
        if _deve_descartar_antes_da_ia(item):
            descartados += 1
            continue
        mantidos.append(item)

    log.info(
        "Pré-filtro IA: %d mantidos, %d descartados",
        len(mantidos),
        descartados,
    )
    return mantidos


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = re.sub(r"\W+", "", it["titulo"].lower())[:60]
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    out.sort(key=lambda x: x.get("data", ""), reverse=True)
    return out



def filtrar_relevancia_pos_ia(items):
    """
    Remove notícias neutras e ruídos tributários sem relação prática
    com comércio de mercadorias/e-commerce.
    """
    mantidos = []
    descartados = 0

    termos_descartar_sempre = [
        "licitação", "licitacao", "edital", "pregão", "pregao",
        "contratação pública", "contratacao publica",
        "contrato administrativo", "habilitação", "habilitacao",
        "pncp", "compras públicas", "compras publicas",
    ]

    termos_ruido_forte = [
        "itbi", "iptu", "previdência", "previdencia", "atuarial",
        "terapia multidisciplinar", "clínica", "clinica", "servidores",
        "fundo em repartição", "fundo em reparticao",
    ]

    termos_comercio = [
        "icms", "icms-st", "substituição tributária", "substituicao tributaria",
        "difal", "fcp", "ipi", "pis", "cofins", "cbs", "ibs", "ncm", "cest",
        "cfop", "nf-e", "nfs-e", "nota fiscal", "sped", "simples nacional",
        "reforma tributária", "reforma tributaria", "mercadoria", "produto",
        "comércio", "comercio", "e-commerce", "venda", "das",
        "regime de caixa", "apuração", "apuracao",
    ]

    termos_fortes_titulo_oficial = [
        "simples nacional", "regime de caixa", "nfs-e", "nf-e",
        "reforma tributária", "reforma tributaria", "cbs", "ibs",
        "icms", "ipi", "pis", "cofins", "ncm", "cest", "cfop", "sped",
    ]

    for item in items:
        relevancia = str(item.get("relevancia") or "").strip().lower()
        if relevancia == "neutra":
            descartados += 1
            continue

        combinado = _normalizar_texto_filtro(
            " ".join([
                str(item.get("titulo") or ""),
                str(item.get("resumo_original") or ""),
                str(item.get("texto_completo") or ""),
                str(item.get("resumo") or ""),
                str(item.get("impacto") or ""),
                " ".join(str(x) for x in item.get("tags", [])),
            ])
        )

        if any(t in combinado for t in termos_descartar_sempre):
            descartados += 1
            continue

        tem_ruido_forte = any(t in combinado for t in termos_ruido_forte)
        tem_contexto_comercio = any(t in combinado for t in termos_comercio)
        if tem_ruido_forte and not tem_contexto_comercio:
            descartados += 1
            continue

        texto_original = _normalizar_texto_filtro(item.get("texto_completo"))
        titulo = _normalizar_texto_filtro(item.get("titulo"))
        resumo_original = _normalizar_texto_filtro(item.get("resumo_original"))
        fonte = _normalizar_texto_filtro(item.get("fonte"))

        conteudo_curto = (
            not resumo_original
            and (texto_original == titulo or len(texto_original) < 120)
        )

        if conteudo_curto:
            eh_receita_oficial = fonte == "receita_federal_rss"
            titulo_tem_tema_forte = any(t in titulo for t in termos_fortes_titulo_oficial)
            if not (eh_receita_oficial and titulo_tem_tema_forte):
                descartados += 1
                continue

        mantidos.append(item)

    log.info(
        "Pós-filtro IA: %d mantidos, %d descartados",
        len(mantidos),
        descartados,
    )
    return mantidos


def run(days: int = 7, debug: bool = False, disable_llm: bool = False) -> dict[str, Any]:
    now = datetime.now().isoformat()
    raw = []
    raw += collect_rss(days=days, debug=debug)
    raw += collect_querido_diario(days=days, debug=debug)
    raw += collect_dou(days=days, debug=debug)
    log.info("Total bruto coletado: %d itens", len(raw))
    items = dedupe(raw)
    items = filtrar_ruido_pre_ia(items)
    if not disable_llm:
        items = _batch(items)
        items = filtrar_relevancia_pos_ia(items)
    return {
        "gerado_em": now,
        "periodo_dias": days,
        "total": len(items),
        "por_relevancia": {
            "alta": sum(1 for i in items if i.get("relevancia") == "alta"),
            "media": sum(1 for i in items if i.get("relevancia") == "media"),
            "neutra": sum(1 for i in items if i.get("relevancia") == "neutra"),
        },
        "noticias": items,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="Executa coleta completa e grava noticias.json")
    r.add_argument("--dias", type=int, default=7)
    r.add_argument("--debug", action="store_true")
    r.add_argument("--sem-llm", action="store_true", help="Pula a camada de IA (modo offline)")
    sub.add_parser("fontes", help="Testa conectividade das fontes")
    args = ap.parse_args()
    if args.cmd == "fontes":
        for nome, url in [("RSS RFB", RSS_RFB), ("Querido Diário", QD_API), ("DOU API", DOU_API)]:
            try:
                r_ = requests.get(url, headers=HEADERS, timeout=20)
                print(f"{nome}: HTTP {r_.status_code} ({len(r_.content)} bytes)")
            except Exception as e:
                print(f"{nome}: FALHOU ({e.__class__.__name__})")
    else:
        dias = getattr(args, "dias", 7)
        res = run(days=dias, debug=getattr(args, "debug", False), disable_llm=getattr(args, "sem_llm", False))
        with open("noticias.json", "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
        print(f"Pronto: {res['total']} notícias -> noticias.json")
        print(f"Alta: {res['por_relevancia']['alta']} | Média: {res['por_relevancia']['media']} | Neutra: {res['por_relevancia']['neutra']}")


if __name__ == "__main__":
    main()
