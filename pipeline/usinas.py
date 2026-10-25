"""
Escolha das usinas que o site mostra (curvas do topo, SCADA e previsão solar).

Usinas fixas, para o portfólio contar sempre a mesma história e o visitante poder conferir no ONS:
  - Solar:  Complexo Solar São Gonçalo (Enel · São Gonçalo do Gurguéia, PI · 864 MW)
  - Eólica: Complexo Eólico Lagoa dos Ventos (Enel · Piauí)

O ONS publica cada complexo em várias linhas (uma por usina ou conjunto). Somamos todas as séries cujo nome
contém o padrão. Se o padrão não achar nada (o ONS mudou o nome, por exemplo), cai na escolha automática:
a maior usina do período, fora agregados de MMGD e séries solares que geram à noite.

Para trocar de usina sem mexer no código: variáveis USINA_SOLAR / USINA_EOLICA com um trecho do nome no ONS
(sem acento, ex.: "PIRAPORA") e, se quiser, USINA_SOLAR_LAT / USINA_SOLAR_LON etc.
"""
import os

USINAS = {
    "SOLAR": {
        "padrao": "SAO GONCALO", "nome": "Complexo Solar São Gonçalo", "dono": "Enel Green Power",
        "estado": "PI", "lat": -10.03, "lon": -45.30, "capacidade_mw": 864.0,
    },
    "EOLICA": {
        "padrao": "LAGOA DOS VENTOS", "nome": "Complexo Eólico Lagoa dos Ventos", "dono": "Enel Green Power",
        "estado": "PI", "lat": -8.48, "lon": -41.55, "capacidade_mw": None,
    },
}
ENV = {"SOLAR": "USINA_SOLAR", "EOLICA": "USINA_EOLICA"}

NOME_SQL = "strip_accents(upper(coalesce(usina, '')))"

# Séries que não representam uma usina física:
#  - agregados de MMGD (micro e minigeração distribuída), estimativas que o ONS publica por estado
#    (ex.: "PQU MGMG MMGD"), com patamar noturno de ~1 GW;
#  - séries solares com média entre 0h e 4h acima de 2% do pico (dado agregado ou errado).
FILTRO_FISICO = f"""
    {NOME_SQL} not like '%MMGD%'
    and chave_usina not in (
        select chave_usina from silver_geracao_usina
        where tipo_fonte = 'SOLAR'
        group by chave_usina
        having avg(geracao_mw) filter (where hour(instante) between 0 and 4) > 0.02 * max(geracao_mw))
"""


def config(tipo):
    c = dict(USINAS[tipo])
    p = os.getenv(ENV[tipo])
    if p:  # usina trocada por variável de ambiente: nome, estado e coordenadas vêm dos dados
        c.update(padrao=p.upper(), nome=None, dono=None, estado=None, lat=None, lon=None, capacidade_mw=None)
    for k, campo in (("lat", "_LAT"), ("lon", "_LON"), ("capacidade_mw", "_CAPACIDADE_MW")):
        v = os.getenv(ENV[tipo] + campo)
        if v:
            c[k] = float(v)
    return c


def selecionar(con, tipo):
    """Devolve {chaves, usina, estado, fixada, dono, padrao} da usina do tipo pedido."""
    c = config(tipo)
    linhas = con.execute(f"""
        select chave_usina, any_value(usina), any_value(estado), sum(geracao_mw) e
        from silver_geracao_usina
        where tipo_fonte = ? and {NOME_SQL} like ? and {NOME_SQL} not like '%MMGD%'
        group by chave_usina order by e desc""", [tipo, f"%{c['padrao']}%"]).fetchall()
    if linhas:
        return {"chaves": [r[0] for r in linhas], "usina": c["nome"] or linhas[0][1],
                "estado": c["estado"] or (linhas[0][2] or ""), "fixada": True,
                "dono": c["dono"], "padrao": c["padrao"]}

    print(f"AVISO: nenhuma usina {tipo} com '{c['padrao']}' no nome; usando a maior dos últimos 30 dias.")
    filtro = ("and " + FILTRO_FISICO) if tipo == "SOLAR" else f"and {NOME_SQL} not like '%MMGD%'"
    r = con.execute(f"""
        with ult as (select max(instante) m from silver_geracao_usina where tipo_fonte = ?)
        select chave_usina, any_value(usina), any_value(estado), sum(geracao_mw) e
        from silver_geracao_usina, ult
        where tipo_fonte = ? and instante > ult.m - interval 30 day {filtro}
        group by chave_usina order by e desc limit 1""", [tipo, tipo]).fetchone()
    if not r:
        return None
    return {"chaves": [r[0]], "usina": r[1], "estado": r[2] or "", "fixada": False, "dono": None, "padrao": None}


def serie(con, chaves):
    """Geração horária somada das chaves: lista de (instante, MW)."""
    return con.execute("""
        select instante, sum(geracao_mw) from silver_geracao_usina
        where chave_usina in (select unnest(?)) group by instante order by instante""", [chaves]).fetchall()


def tem_tabela(con, nome):
    return con.execute("select count(*) from information_schema.tables where table_name = ?", [nome]).fetchone()[0] > 0


def capacidade_e_local(con, tipo, sel):
    """Capacidade instalada (MW) e coordenadas: do conjunto 'fator de capacidade' do ONS quando houver,
    senão da configuração, senão do pico observado."""
    c = config(tipo)
    cap, lat, lon, origem = c["capacidade_mw"], c["lat"], c["lon"], "cadastro do portfólio"
    if sel["padrao"] and tem_tabela(con, "silver_fator_capacidade"):
        r = con.execute("""
            with ult as (
                select chave_conjunto, arg_max(capacidade_mw, instante) cap,
                       arg_max(latitude, instante) lat, arg_max(longitude, instante) lon
                from silver_fator_capacidade
                where tipo_fonte = ? and strip_accents(upper(coalesce(conjunto, ''))) like ?
                group by chave_conjunto)
            select sum(cap), avg(lat), avg(lon) from ult""", [tipo, f"%{sel['padrao']}%"]).fetchone()
        if r and r[0]:
            cap, origem = float(r[0]), "ONS · fator de capacidade"
            if r[1] is not None and r[2] is not None and -35 < r[1] < 6 and -75 < r[2] < -30:
                lat, lon = float(r[1]), float(r[2])
    if not cap:
        pico = con.execute("""select quantile_cont(geracao_mw, 0.995) from (
                                select instante, sum(geracao_mw) geracao_mw from silver_geracao_usina
                                where chave_usina in (select unnest(?)) group by instante)""", [sel["chaves"]]).fetchone()[0]
        cap, origem = (round(float(pico) * 1.05, 1) if pico else None), "pico observado + 5%"
    return {"capacidade_mw": cap, "capacidade_origem": origem, "lat": lat, "lon": lon}
