"""
Ingestão dos dados abertos do ONS (Operador Nacional do Sistema Elétrico) -> camada Bronze.

1. Descobre no bucket público do ONS (ons-aws-prod-opendata, licença CC-BY 4.0) a pasta de cada conjunto.
2. Baixa os arquivos dos últimos ONS_MESES meses para data_lake/raw/<conjunto>/ (prefere Parquet).
3. Normaliza os nomes de coluna para um esquema canônico e grava data_lake/bronze/<conjunto>/*.parquet,
   tudo como texto + colunas de auditoria. A conversão de tipos e as regras ficam na Silver (dbt).
"""
import datetime as dt
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import duckdb
import requests

ONS = "https://ons-aws-prod-opendata.s3.amazonaws.com"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
RAIZ = Path(__file__).resolve().parent.parent
RAW = RAIZ / "data_lake" / "raw"
BRONZE = RAIZ / "data_lake" / "bronze"
MESES = int(os.getenv("ONS_MESES", "3"))

# Cada conjunto: palavras para achar a pasta no bucket e o mapa coluna canônica -> nomes possíveis no ONS.
CONJUNTOS = {
    "geracao_usina": {
        "palavras": ["geracao_usina"], "preferir": "_ho", "evitar": None,
        "obrigatorias": ["instante", "usina", "tipo_usina", "geracao"],
        "campos": {
            "instante": ["din_instante", "dat_referencia"],
            "usina": ["nom_usina"],
            "ceg": ["ceg", "cod_ceg"],
            "id_ons": ["id_ons"],
            "tipo_usina": ["nom_tipousina", "nom_tipo_usina"],
            "estado": ["id_estado", "nom_estado"],
            "subsistema": ["id_subsistema", "nom_subsistema"],
            "geracao": ["val_geracao", "val_geracaomwmed"],
        },
    },
    "restricao_eolica": {
        "palavras": ["restricao_coff_eolica"], "preferir": "usi", "evitar": "detail",
        "obrigatorias": ["instante", "usina", "geracao", "referencia"],
        "campos": {
            "instante": ["din_instante"],
            "usina": ["nom_usina", "nom_conjuntousina"],
            "ceg": ["ceg"],
            "id_ons": ["id_ons"],
            "estado": ["id_estado", "nom_estado"],
            "geracao": ["val_geracao", "val_geracaoverificada"],
            "referencia": ["val_geracaoreferenciafinal", "val_geracaoreferencia", "val_geracaoestimada", "val_disponibilidade"],
            "limitada": ["val_geracaolimitada"],
            "razao": ["cod_razaorestricao", "dsc_restricao"],
        },
    },
}


def listar(prefixo, delimitador=None):
    chaves, prefixos, token = [], [], None
    while True:
        params = {"list-type": "2", "prefix": prefixo, "max-keys": "1000"}
        if delimitador:
            params["delimiter"] = delimitador
        if token:
            params["continuation-token"] = token
        r = requests.get(ONS + "/", params=params, timeout=60)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        chaves += [c.find("s3:Key", NS).text for c in root.findall("s3:Contents", NS)]
        prefixos += [p.find("s3:Prefix", NS).text for p in root.findall("s3:CommonPrefixes", NS)]
        if root.findtext("s3:IsTruncated", default="false", namespaces=NS) != "true":
            return chaves, prefixos
        token = root.findtext("s3:NextContinuationToken", namespaces=NS)


def descobrir_pasta(cfg, pastas):
    cand = [p for p in pastas if all(w in p.lower() for w in cfg["palavras"])]
    if cfg["evitar"]:
        cand = [p for p in cand if cfg["evitar"] not in p.lower()] or cand
    cand.sort(key=lambda p: (cfg["preferir"] not in p.lower(), p))
    return cand[0] if cand else None


def meses_alvo():
    hoje = dt.date.today()
    a, m, out = hoje.year, hoje.month, []
    for _ in range(MESES):
        out.append((a, m))
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    return set(out)


def escolher(chaves, alvo):
    """Um arquivo por período, Parquet antes de CSV. Aceita mensal (_2025_01) e anual (_2025)."""
    pref = ["parquet", "csv"]
    escolha = {}
    for k in chaves:
        nome = k.rsplit("/", 1)[-1]
        ext = nome.rsplit(".", 1)[-1].lower()
        if ext not in pref:
            continue
        mm = re.search(r"(20\d{2})[_-](\d{2})(?!\d)", nome)
        aa = re.search(r"(20\d{2})(?!\d)", nome)
        per = (int(mm.group(1)), int(mm.group(2))) if mm else ((int(aa.group(1)), None) if aa else None)
        if per is None:
            continue
        ok = per in alvo if per[1] else per[0] in {a for a, _ in alvo}
        if not ok:
            continue
        atual = escolha.get(per)
        if atual is None or pref.index(ext) < pref.index(atual.rsplit(".", 1)[-1].lower()):
            escolha[per] = k
    return [escolha[p] for p in sorted(escolha, key=lambda x: (x[0], x[1] or 0))]


def recente(nome):
    """Arquivos do mês atual e do anterior ainda são atualizados pelo ONS: baixa de novo a cada execução."""
    hoje = dt.date.today()
    ant = (hoje.replace(day=1) - dt.timedelta(days=1))
    return any(f"{d.year}_{d.month:02d}" in nome or f"{d.year}-{d.month:02d}" in nome for d in (hoje, ant))


def baixar(chave, destino):
    if destino.exists() and destino.stat().st_size > 0 and not recente(destino.name):
        print(f"   em cache: {destino.name}")
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(destino.suffix + ".part")
    with requests.get(f"{ONS}/{chave}", stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for bloco in r.iter_content(chunk_size=8 * 1024 * 1024):
                fh.write(bloco)
    tmp.rename(destino)
    print(f"   baixado: {destino.name} ({destino.stat().st_size / 1e6:.1f} MB)")


def normalizar(conjunto, cfg, arquivo, saida):
    con = duckdb.connect()
    if arquivo.suffix.lower() == ".parquet":
        rel = f"read_parquet('{arquivo.as_posix()}')"
    else:
        rel = f"read_csv('{arquivo.as_posix()}', delim=';', header=true, all_varchar=true)"
    cols = {c.lower(): c for c in con.sql(f"select * from {rel} limit 0").columns}
    sel, faltando = [], []
    for canon, nomes in cfg["campos"].items():
        achou = next((cols[n] for n in nomes if n in cols), None)
        if achou is None and canon in cfg["obrigatorias"]:
            faltando.append(canon)
        sel.append(f'cast("{achou}" as varchar) as {canon}' if achou else f"cast(null as varchar) as {canon}")
    if faltando:
        sys.exit(f"[{conjunto}] colunas obrigatórias ausentes em {arquivo.name}: {faltando}. Colunas do arquivo: {list(cols)}")
    saida.parent.mkdir(parents=True, exist_ok=True)
    con.sql(f"""copy (select {', '.join(sel)}, '{arquivo.name}' as source_file, now()::varchar as ingested_at from {rel})
               to '{saida.as_posix()}' (format parquet)""")
    n = con.sql(f"select count(*) from read_parquet('{saida.as_posix()}')").fetchone()[0]
    print(f"   bronze: {saida.name} ({n:,} linhas)")


def bronze_vazia(cfg, saida):
    """Sem arquivos no período: grava uma Bronze vazia com o esquema canônico para o dbt seguir."""
    saida.parent.mkdir(parents=True, exist_ok=True)
    cols = ", ".join(f"cast(null as varchar) as {c}" for c in list(cfg["campos"]) + ["source_file", "ingested_at"])
    duckdb.sql(f"copy (select {cols} where false) to '{saida.as_posix()}' (format parquet)")


def main():
    alvo = meses_alvo()
    print(f"Meses alvo: {sorted(alvo)}")
    _, pastas = listar("dataset/", "/")
    for conjunto, cfg in CONJUNTOS.items():
        pasta = descobrir_pasta(cfg, pastas)
        print(f"\n[{conjunto}] pasta no ONS: {pasta}")
        saida_dir = BRONZE / conjunto
        for velho in saida_dir.glob("*.parquet"):
            velho.unlink()
        arquivos = escolher(listar(pasta)[0], alvo) if pasta else []
        if not arquivos:
            print(f"   nenhum arquivo no período; Bronze vazia. Pastas parecidas: {[p for p in pastas if cfg['palavras'][0].split('_')[0] in p][:10]}")
            bronze_vazia(cfg, saida_dir / "_vazio.parquet")
            continue
        for k in arquivos:
            destino = RAW / conjunto / k.rsplit("/", 1)[-1]
            baixar(k, destino)
            normalizar(conjunto, cfg, destino, saida_dir / (destino.stem + ".parquet"))


if __name__ == "__main__":
    main()
