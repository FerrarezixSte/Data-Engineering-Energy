"""
Gera data/ons.json para os gráficos do portfólio, a partir das tabelas Silver/Gold do DuckDB.

- solar / eolica: as usinas fixas do portfólio (pipeline/usinas.py) no dia mais recente em que as duas têm
  as 24 horas completas. A solar mostra o dia (0h-24h); a eólica mostra a janela noturna (12h-12h) em volta
  desse dia, porque é à noite que o vento do Nordeste gera mais.
- scada: as mesmas usinas, com capacidade, localização e o tempo da região naquele dia (Open-Meteo),
  para o painel SCADA reproduzir o dia real hora a hora.
- mmgd: a micro e minigeração distribuída estimada pelo ONS, mostrada à parte porque não é uma usina física.
- restricao: no mês mais recente, o parque eólico com mais energia restringida e o seu pior dia.
- matriz: participação das fontes nos meses processados.
"""
import datetime as dt
import json
import os
from pathlib import Path

import duckdb
import requests

from usinas import NOME_SQL, capacidade_e_local, selecionar, serie

RAIZ = Path(__file__).resolve().parent.parent
FUSO = "America/Sao_Paulo"
con = duckdb.connect(str(RAIZ / "ons.duckdb"), read_only=True)


def um(sql, params=None):
    return con.execute(sql, params or []).fetchone()


def dias_completos(pontos):
    """Datas com as 24 horas presentes."""
    por_dia = {}
    for t, _ in pontos:
        por_dia.setdefault(t.date(), set()).add(t.hour)
    return {d for d, hs in por_dia.items() if len(hs) == 24}


def do_dia(pontos, dia):
    horas = {}
    for t, mw in pontos:
        if t.date() == dia:
            horas[t.hour] = horas.get(t.hour, 0.0) + float(mw)
    return [[h, round(max(0.0, horas[h]), 2)] for h in sorted(horas)]


def clima_do_dia(lat, lon, dia):
    """Tempo da região, hora a hora, no dia escolhido. Se o Open-Meteo falhar, o site estima pela curva."""
    if lat is None or lon is None or os.getenv("SEM_CLIMA"):
        return None
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=60, params={
            "latitude": lat, "longitude": lon, "timezone": FUSO,
            "hourly": "shortwave_radiation,temperature_2m,wind_speed_100m,wind_direction_100m",
            "wind_speed_unit": "ms", "start_date": str(dia), "end_date": str(dia)})
        r.raise_for_status()
        h = r.json()["hourly"]
        return [[int(t[11:13]), g, temp, v, d] for t, g, temp, v, d in zip(
            h["time"], h["shortwave_radiation"], h["temperature_2m"], h["wind_speed_100m"], h["wind_direction_100m"])]
    except Exception as e:
        print("AVISO: clima do Open-Meteo indisponível:", e)
        return None


def janela_noturna(pontos, dia, inicio_h=12):
    """24 horas de meio-dia a meio-dia, com a noite no centro (a eólica do Nordeste gera mais à noite).
    Prefere a noite que começa no dia escolhido; se a manhã seguinte ainda não foi publicada, usa a noite anterior.
    Devolve (início, [[hora relativa 0-23, MW]]) ou (None, None)."""
    por_hora = {}
    for t, mw in pontos:
        por_hora[t] = por_hora.get(t, 0.0) + float(mw)
    for ini_dia in (dia, dia - dt.timedelta(days=1)):
        ini = dt.datetime.combine(ini_dia, dt.time(inicio_h))
        horas = [ini + dt.timedelta(hours=k) for k in range(24)]
        if all(h in por_hora for h in horas):
            return ini, [[k, round(max(0.0, por_hora[h]), 2)] for k, h in enumerate(horas)]
    return None, None


RAZOES = {  # códigos de razão da restrição no ONS
    "ENE": "razão energética",          # sobra de energia no sistema
    "REL": "razão elétrica",            # limite da rede de transmissão
    "CNF": "confiabilidade elétrica",
    "PAR": "indisponibilidade externa",
}


def restricao_da_usina(sel, ini):
    """Restrição de operação do ONS na janela de 24 h a partir de `ini`, somando as séries do complexo.
    Devolve [[hora relativa, referência MW, verificada MW, restringida MW, com_restricao]] e as razões,
    ou (None, None) se o ONS ainda não publicou ao menos 20 das 24 horas."""
    padrao = sel["padrao"] or sel["usina"].upper()
    rows = con.execute("""
        select date_trunc('hour', instante) h,
               sum(coalesce(referencia_mw, geracao_mw) * horas_intervalo) ref_mw,
               sum(geracao_mw * horas_intervalo) ger_mw,
               sum(restringido_mw * horas_intervalo) restr_mw,
               bool_or(com_restricao) com_lim,
               string_agg(distinct razao_restricao, ',') razoes
        from silver_restricao_eolica
        where strip_accents(upper(coalesce(usina, ''))) like ? and instante >= ? and instante < ?
        group by 1 order by 1""", [f"%{padrao}%", ini, ini + dt.timedelta(days=1)]).fetchall()
    if len(rows) < 20:
        return None, None
    razoes = sorted({r for row in rows if row[5] for r in row[5].split(",")})
    pts = [[int((h - ini).total_seconds() // 3600), round(float(ref or 0), 2), round(float(ger or 0), 2),
            round(float(restr or 0), 2), bool(lim)] for h, ref, ger, restr, lim, _ in rows]
    return pts, [{"codigo": r, "descricao": RAZOES.get(r.upper(), r)} for r in razoes]


def noite_com_restricao(sel, pontos, dia):
    """Escolhe a janela noturna da eólica: a do dia do gráfico solar se o ONS já publicou a restrição dela;
    senão a noite mais recente (até 21 dias) com geração e restrição completas; senão a do dia, sem restrição."""
    ini, noite = janela_noturna(pontos, dia)
    if not noite:
        return None, None, None, None
    restr, razoes = restricao_da_usina(sel, ini)
    if restr:
        return ini, noite, restr, razoes
    for k in range(1, 22):
        ini2, noite2 = janela_noturna(pontos, dia - dt.timedelta(days=k))
        if not noite2 or ini2 >= ini:
            continue
        restr2, razoes2 = restricao_da_usina(sel, ini2)
        if restr2:
            return ini2, noite2, restr2, razoes2
    return ini, noite, None, None


def resumo(sel, curva):
    energia = sum(p[1] for p in curva)
    pico = max((p[1] for p in curva), default=0)
    return {"usina": sel["usina"], "estado": sel["estado"], "fixada": sel["fixada"],
            "energia_mwh": round(energia, 2), "pico_mw": round(pico, 2), "horario": curva}


def usinas_e_dia():
    sol, eol = selecionar(con, "SOLAR"), selecionar(con, "EOLICA")
    ps = serie(con, sol["chaves"]) if sol else []
    pe = serie(con, eol["chaves"]) if eol else []
    comuns = dias_completos(ps) & dias_completos(pe)
    if comuns:
        dia = max(comuns)
    else:  # sem dia comum completo: o mais recente de qualquer uma
        dia = max([t.date() for t, _ in ps + pe], default=None)
    return sol, ps, eol, pe, dia


def scada(tipo, sel, curva, dia):
    if not sel or not curva:
        return None
    local = capacidade_e_local(con, tipo, sel)
    energia = sum(p[1] for p in curva)
    cap = local["capacidade_mw"]
    return {
        "usina": sel["usina"], "estado": sel["estado"], "dono": sel["dono"], "fixada": sel["fixada"],
        "series_ons": len(sel["chaves"]), "data": str(dia),
        "capacidade_mw": cap, "capacidade_origem": local["capacidade_origem"],
        "lat": local["lat"], "lon": local["lon"],
        "energia_mwh": round(energia, 1),
        "fator_capacidade_dia_pct": round(100 * energia / (24 * cap), 1) if cap else None,
        "horario": curva,
        "clima": clima_do_dia(local["lat"], local["lon"], dia),  # [hora, GHI W/m², °C, vento 100 m m/s, direção °]
    }


def mmgd(dia):
    if dia is None:
        return None
    horas = con.execute(f"""select hour(instante), round(sum(geracao_mw), 1) from silver_geracao_usina
                            where {NOME_SQL} like '%MMGD%' and cast(instante as date) = ?
                            group by 1 order by 1""", [dia]).fetchall()
    if not horas:
        return None
    estados = con.execute(f"""select coalesce(estado, 'N/D'), round(sum(geracao_mw) / 1000, 1) gwh
                              from silver_geracao_usina
                              where {NOME_SQL} like '%MMGD%'
                                and instante > ?::date - interval 30 day and instante < ?::date + interval 1 day
                              group by 1 order by gwh desc""", [dia, dia]).fetchall()
    return {"data": str(dia), "horario": [[int(h), float(v)] for h, v in horas],
            "estados_30d_gwh": [[e, float(g)] for e, g in estados[:8]],
            "total_30d_gwh": round(sum(float(g) for _, g in estados), 1),
            "series": um(f"select count(distinct chave_usina) from silver_geracao_usina where {NOME_SQL} like '%MMGD%'")[0]}


def restricao():
    ultimo = um("select max(cast(instante as date)) from silver_restricao_eolica")[0]
    if ultimo is None:
        return None
    top = um("""select chave_usina, usina, estado, energia_restringida_mwh from gold_restricao_eolica_mensal
                where ano = ? and mes = ? order by energia_restringida_mwh desc limit 1""", [ultimo.year, ultimo.month])
    if top is None or not top[3]:
        return None
    dia = um("""select cast(instante as date) d, sum(restringido_mw * horas_intervalo) e
                from silver_restricao_eolica
                where chave_usina = ? and year(instante) = ? and month(instante) = ?
                group by d order by e desc limit 1""", [top[0], ultimo.year, ultimo.month])
    pts = con.execute("""select hour(instante) + minute(instante) / 60.0, geracao_mw, referencia_mw, com_restricao, horas_intervalo
                         from silver_restricao_eolica
                         where chave_usina = ? and cast(instante as date) = ? order by instante""", [top[0], dia[0]]).fetchall()
    gerada = sum(float(p[1]) * float(p[4]) for p in pts)
    horas = sum(float(p[4]) for p in pts if p[3])
    return {"usina": top[1], "estado": top[2], "data": str(dia[0]),
            "energia_restringida_mwh": round(float(dia[1]), 1), "energia_gerada_mwh": round(float(gerada), 1),
            "horas_com_restricao": float(horas),
            "semi_horario": [[round(float(h), 2), round(float(g), 2), None if r is None else round(float(r), 2), bool(c)]
                             for h, g, r, c, _ in pts]}


def matriz():
    rows = con.execute("""select ano, mes, tipo_fonte, energia_gwh, participacao_pct
                          from gold_matriz_mensal order by ano, mes, energia_gwh desc""").fetchall()
    return [{"ano": a, "mes": m, "fonte": f, "gwh": float(g), "pct": float(p)} for a, m, f, g, p in rows]


sol, ps, eol, pe, dia = usinas_e_dia()
curva_s = do_dia(ps, dia) if dia else []
curva_e = do_dia(pe, dia) if dia else []

# curva eólica do topo: janela noturna (12h → 12h); se não houver 24 h seguidas, volta ao dia corrido
eolica_topo = None
if eol and curva_e:
    ini, noite, restr, razoes = noite_com_restricao(eol, pe, dia)
    if noite:
        fim = ini + dt.timedelta(days=1)
        eolica_topo = {**resumo(eol, noite), "data": str(fim.date()), "janela": "noturna",
                       "inicio": ini.strftime("%Y-%m-%dT%H:00"), "offset_h": ini.hour,
                       "mesmo_dia_solar": fim.date() == dia or ini.date() == dia}
        if restr:
            eolica_topo["restricao"] = {
                "horario": restr, "razoes": razoes,
                "energia_restringida_mwh": round(sum(p[3] for p in restr), 1),
                "horas_com_restricao": sum(1 for p in restr if p[4]),
            }
    else:
        eolica_topo = {**resumo(eol, curva_e), "data": str(dia), "janela": "dia"}

saida = {
    "atualizado_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
    "fonte": "ONS - Dados Abertos (CC-BY 4.0): Geração por usina em base horária; Restrição de operação de eólicas. "
             "Clima: Open-Meteo (CC-BY 4.0).",
    "data": str(dia) if dia else None,
    "solar": ({**resumo(sol, curva_s), "data": str(dia)} if sol and curva_s else None),
    "eolica": eolica_topo,
    "scada": {"solar": scada("SOLAR", sol, curva_s, dia), "eolica": scada("EOLICA", eol, curva_e, dia)},
    "mmgd": mmgd(dia),
    "restricao": restricao(),
    "matriz": matriz(),
}
destino = RAIZ / "data" / "ons.json"
destino.parent.mkdir(exist_ok=True)
destino.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"Gravado {destino} ({destino.stat().st_size / 1024:.1f} KB) · dia {saida['data']}")
for k in ("solar", "eolica", "restricao", "mmgd"):
    v = saida[k]
    if k == "eolica" and v and v.get("restricao"):
        r = v["restricao"]
        print(f"  eolica: restrição ONS em {r['horas_com_restricao']} h, {r['energia_restringida_mwh']} MWh "
              f"({', '.join(x['codigo'] for x in r['razoes']) or 'sem código'})")
    elif k == "eolica" and v:
        print("  eolica: restrição do ONS ainda não publicada para as últimas noites")
    print(f"  {k}: {v.get('usina', 'MMGD')} ({v.get('estado', 'BR')}) em {v['data']}" if v else f"  {k}: sem dados")
