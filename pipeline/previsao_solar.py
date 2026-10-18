"""
IA: previsão de geração solar hora a hora para o dia seguinte.

1. Escolhe a usina solar com mais energia nos últimos 30 dias (dados ONS já tratados pelo dbt).
2. Busca o tempo da região no Open-Meteo (gratuito, sem chave): irradiância global, direta e difusa,
   temperatura e nebulosidade, dos últimos ~90 dias e da previsão para amanhã.
3. Treina um HistGradientBoostingRegressor (scikit-learn) com geração real x clima.
4. Avalia nos últimos 14 dias contra dois modelos simples: persistência (repetir ontem) e
   regressão linear só com irradiância.
5. Retreina com todo o histórico, prevê amanhã e grava data/previsao_solar.json para o site.

Coordenadas: o conjunto horário do ONS não traz latitude/longitude, então usa um ponto representativo
das regiões solares do estado. Para uma usina específica, defina SOLAR_LAT e SOLAR_LON.
"""
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import requests
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LinearRegression

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "data" / "previsao_solar.json"
DIAS_TESTE = 14
FUSO = "America/Sao_Paulo"

# Ponto aproximado das principais regiões de usinas solares de cada estado (lat, lon)
PONTO_UF = {
    "MG": (-17.35, -44.94), "BA": (-12.10, -42.40), "PI": (-8.40, -42.20), "RN": (-5.80, -37.10),
    "CE": (-5.20, -39.30), "PE": (-8.30, -38.00), "PB": (-7.10, -36.60), "SP": (-21.70, -49.40),
    "GO": (-15.90, -49.30), "TO": (-11.00, -48.40), "MS": (-20.80, -54.60), "PR": (-24.70, -51.40),
    "SE": (-10.60, -37.40), "AL": (-9.60, -36.70), "MA": (-5.50, -45.20), "RS": (-30.00, -53.50),
    "SC": (-27.20, -50.40), "RJ": (-22.40, -42.40), "ES": (-19.50, -40.60), "MT": (-15.60, -56.10),
    "PA": (-3.50, -51.90), "AM": (-3.10, -60.00), "DF": (-15.80, -47.90),
}
VARS = ["shortwave_radiation", "direct_radiation", "diffuse_radiation", "temperature_2m", "cloud_cover"]


def falhar(msg):
    """Não derruba o pipeline: grava o motivo no JSON e sai com sucesso."""
    print("AVISO:", msg)
    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps({"erro": msg, "atualizado_em": agora()}, ensure_ascii=False), encoding="utf-8")
    sys.exit(0)


def agora():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")


def escolher_usina(con):
    r = con.execute("""
        with ult as (select max(data) d from gold_geracao_diaria_usina where tipo_fonte = 'SOLAR')
        select chave_usina, any_value(usina), any_value(estado), sum(energia_mwh) e
        from gold_geracao_diaria_usina, ult
        where tipo_fonte = 'SOLAR' and data > ult.d - interval 30 day
        group by chave_usina order by e desc limit 1""").fetchone()
    if not r:
        falhar("Sem dados de usinas solares na Gold.")
    return {"chave": r[0], "usina": r[1], "estado": (r[2] or "").upper()}


def geracao_horaria(con, chave):
    df = con.execute("""select instante, sum(geracao_mw) mw from silver_geracao_usina
                        where chave_usina = ? group by instante order by instante""", [chave]).df()
    df["instante"] = pd.to_datetime(df["instante"])
    return df.set_index("instante")["mw"]


def obter_clima(lat, lon):
    arquivo = os.getenv("PREVISAO_CLIMA_ARQUIVO")  # usado nos testes locais
    if arquivo:
        dados = json.loads(Path(arquivo).read_text())
    else:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=60, params={
            "latitude": lat, "longitude": lon, "hourly": ",".join(VARS),
            "past_days": 92, "forecast_days": 2, "timezone": FUSO})
        r.raise_for_status()
        dados = r.json()
    h = dados["hourly"]
    df = pd.DataFrame({v: h[v] for v in VARS})
    df.index = pd.to_datetime(h["time"])
    return df


def montar_features(clima):
    f = clima.copy()
    # o ONS registra a média da hora; vizinhas absorvem diferença de convenção (início ou fim do intervalo)
    for v in ["shortwave_radiation", "direct_radiation"]:
        f[v + "_ant"] = f[v].shift(1)
        f[v + "_prox"] = f[v].shift(-1)
    f["hora"] = f.index.hour
    doy = f.index.dayofyear
    f["dia_sen"] = np.sin(2 * math.pi * doy / 365.25)
    f["dia_cos"] = np.cos(2 * math.pi * doy / 365.25)
    return f


def metricas(real, prev, capacidade):
    erro = prev - real
    mae = float(np.mean(np.abs(erro)))
    return {"mae_mw": round(mae, 2), "rmse_mw": round(float(np.sqrt(np.mean(erro ** 2))), 2),
            "nmae_pct": round(100 * mae / capacidade, 2)}


def main():
    con = duckdb.connect(str(RAIZ / "ons.duckdb"), read_only=True)
    u = escolher_usina(con)
    lat = float(os.getenv("SOLAR_LAT", "nan")); lon = float(os.getenv("SOLAR_LON", "nan"))
    if math.isnan(lat) or math.isnan(lon):
        if u["estado"] not in PONTO_UF:
            falhar(f"Sem coordenadas para o estado '{u['estado']}'. Defina SOLAR_LAT e SOLAR_LON.")
        lat, lon = PONTO_UF[u["estado"]]
    print(f"Usina: {u['usina']} ({u['estado']}) · ponto do clima: {lat}, {lon}")

    ger = geracao_horaria(con, u["chave"])
    try:
        clima = obter_clima(lat, lon)
    except Exception as e:
        falhar(f"Não foi possível obter o clima no Open-Meteo: {e}")
    feats = montar_features(clima)
    cols = list(feats.columns)

    # o HistGradientBoosting aceita valores ausentes nas variáveis; só a geração precisa existir
    base = feats.join(ger.rename("mw"), how="inner")
    base = base[base["mw"].notna() & (base["mw"] >= 0)]
    if len(base) < 24 * 30:
        falhar(f"Histórico curto para treinar ({len(base)} horas em comum entre ONS e clima).")
    capacidade = float(base["mw"].quantile(0.995))  # referência de potência para o erro normalizado

    # ---------- avaliação: últimos 14 dias fora do treino ----------
    corte = base.index.max().normalize() - pd.Timedelta(days=DIAS_TESTE - 1)
    treino, teste = base[base.index < corte], base[base.index >= corte]
    modelo = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
                                           l2_regularization=1.0, random_state=42)
    modelo.fit(treino[cols], treino["mw"])
    prev_ml = np.clip(modelo.predict(teste[cols]), 0, None)
    prev_ml[np.nan_to_num(teste["shortwave_radiation"].values) <= 0] = 0

    persist = ger.shift(24, freq="h").reindex(teste.index)  # mesma hora do dia anterior
    ok = persist.notna().values
    lin = LinearRegression().fit(treino[["shortwave_radiation"]].fillna(0), treino["mw"])
    prev_lin = np.clip(lin.predict(teste[["shortwave_radiation"]].fillna(0)), 0, None)

    m_ml = metricas(teste["mw"].values, prev_ml, capacidade)
    m_pers = metricas(teste["mw"].values[ok], persist.values[ok], capacidade)
    m_lin = metricas(teste["mw"].values, prev_lin, capacidade)
    ganho = round(100 * (1 - m_ml["mae_mw"] / m_pers["mae_mw"]), 1) if m_pers["mae_mw"] > 0 else None
    print(f"Teste {DIAS_TESTE} dias | modelo nMAE {m_ml['nmae_pct']}% | persistência {m_pers['nmae_pct']}% | "
          f"linear {m_lin['nmae_pct']}% | ganho sobre persistência {ganho}%")

    # ---------- modelo final e previsão de amanhã ----------
    modelo.fit(base[cols], base["mw"])
    amanha = (pd.Timestamp.now(tz=FUSO).tz_localize(None).normalize() + pd.Timedelta(days=1))
    fut = feats[(feats.index >= amanha) & (feats.index < amanha + pd.Timedelta(days=1))]
    if fut.empty:
        falhar("O Open-Meteo não retornou a previsão do tempo para amanhã.")
    prev_fut = np.clip(modelo.predict(fut[cols]), 0, None)
    prev_fut[np.nan_to_num(fut["shortwave_radiation"].values) <= 0] = 0

    ult7 = teste[teste.index >= teste.index.max().normalize() - pd.Timedelta(days=6)]
    prev7 = pd.Series(prev_ml, index=teste.index).reindex(ult7.index)

    saida = {
        "atualizado_em": agora(),
        "usina": u["usina"], "estado": u["estado"], "lat": lat, "lon": lon,
        "capacidade_ref_mw": round(capacidade, 1),
        "modelo": "HistGradientBoostingRegressor (scikit-learn)",
        "variaveis": ["irradiância global, direta e difusa", "temperatura", "nebulosidade", "hora", "época do ano"],
        "fontes": "Geração: ONS Dados Abertos (CC-BY 4.0). Clima: Open-Meteo (CC-BY 4.0).",
        "horas_treino": int(len(base)), "dias_teste": DIAS_TESTE,
        "metricas": {"modelo": m_ml, "persistencia": m_pers, "linear_irradiancia": m_lin, "ganho_sobre_persistencia_pct": ganho},
        "backtest": [[i.strftime("%Y-%m-%dT%H:00"), round(float(r), 2), round(float(p), 2)]
                     for i, r, p in zip(ult7.index, ult7["mw"], prev7.values)],
        "amanha": {"data": amanha.strftime("%Y-%m-%d"),
                   "energia_mwh": round(float(prev_fut.sum()), 1),
                   "horario": [[i.strftime("%Y-%m-%dT%H:00"), round(float(p), 2), round(float(g), 0)]
                               for i, p, g in zip(fut.index, prev_fut, fut["shortwave_radiation"].fillna(0))]},
    }
    SAIDA.parent.mkdir(exist_ok=True)
    SAIDA.write_text(json.dumps(saida, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Gravado {SAIDA} · amanhã ({saida['amanha']['data']}): {saida['amanha']['energia_mwh']} MWh previstos")


if __name__ == "__main__":
    main()
