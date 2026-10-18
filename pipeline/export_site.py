"""
Gera data/ons.json para os gráficos do portfólio, a partir das tabelas Gold/Silver do DuckDB.

- solar / eolica: no mês mais recente, a usina com mais energia e o seu dia de maior geração, hora a hora.
- restricao: no mês mais recente, o parque eólico com mais energia restringida e o seu pior dia, meia em meia hora.
- matriz: participação das fontes nos meses processados.
"""
import datetime as dt
import json
from pathlib import Path

import duckdb

RAIZ = Path(__file__).resolve().parent.parent
con = duckdb.connect(str(RAIZ / "ons.duckdb"), read_only=True)


def um(sql, params=None):
    r = con.execute(sql, params or []).fetchone()
    return r


def curva(tipo):
    ultimo = um("select max(data) from gold_geracao_diaria_usina where tipo_fonte = ?", [tipo])[0]
    if ultimo is None:
        return None
    top = um("""select chave_usina, any_value(usina), any_value(estado), sum(energia_mwh) e
                from gold_geracao_diaria_usina
                where tipo_fonte = ? and year(data) = ? and month(data) = ?
                group by chave_usina order by e desc limit 1""", [tipo, ultimo.year, ultimo.month])
    dia = um("""select data, energia_mwh, pico_mw from gold_geracao_diaria_usina
                where chave_usina = ? and year(data) = ? and month(data) = ?
                order by energia_mwh desc limit 1""", [top[0], ultimo.year, ultimo.month])
    horas = con.execute("""select hour(instante) h, round(sum(geracao_mw), 2)
                           from silver_geracao_usina
                           where chave_usina = ? and cast(instante as date) = ?
                           group by h order by h""", [top[0], dia[0]]).fetchall()
    return {"usina": top[1], "estado": top[2], "data": str(dia[0]), "energia_mwh": float(dia[1]),
            "pico_mw": float(dia[2]), "horario": [[int(h), float(v)] for h, v in horas]}


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


saida = {
    "atualizado_em": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
    "fonte": "ONS - Dados Abertos (CC-BY 4.0): Geração por usina em base horária; Restrição de operação de eólicas",
    "solar": curva("SOLAR"),
    "eolica": curva("EOLICA"),
    "restricao": restricao(),
    "matriz": matriz(),
}
destino = RAIZ / "data" / "ons.json"
destino.parent.mkdir(exist_ok=True)
destino.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"Gravado {destino} ({destino.stat().st_size / 1024:.1f} KB)")
for k in ("solar", "eolica", "restricao"):
    v = saida[k]
    print(f"  {k}: {v['usina']} ({v['estado']}) em {v['data']}" if v else f"  {k}: sem dados")
