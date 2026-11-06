"""
IA: previsão de geração solar hora a hora para o dia seguinte.

1. Usa a usina solar fixa do portfólio (pipeline/usinas.py: Complexo Solar Janaúba, MG), a mesma do
   painel SCADA. Se ela não estiver nos dados, cai na maior usina solar física dos últimos 30 dias.
2. Busca o tempo da região no Open-Meteo (gratuito, sem chave): irradiância global, direta e difusa,
   temperatura e nebulosidade, dos últimos ~90 dias e da previsão para amanhã.
3. Treina um HistGradientBoostingRegressor (scikit-learn) com a geração DISPONÍVEL x clima: nas horas em que
   o ONS cortou a usina, o alvo é o verificado + o cortado; cortes sem dado do ONS (patamares e quedas fortes
   que o tempo não explica) ficam fora do treino.
4. Avalia nos últimos 14 dias contra dois modelos simples: persistência (repetir ontem) e
   regressão linear só com irradiância.
5. Retreina com todo o histórico, prevê amanhã e grava data/previsao_solar.json para o site.
6. Guarda cada previsão publicada em data/historico_previsao.json e mede o erro REAL do dia seguinte
   (previsão feita com o tempo previsto, comparada depois com o que a usina gerou).

Coordenadas: do conjunto "fator de capacidade" do ONS (que traz latitude e longitude) ou do cadastro em
pipeline/usinas.py. Só na falta das duas usa um ponto representativo das regiões solares do estado.
SOLAR_LAT e SOLAR_LON continuam valendo para forçar um ponto.
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
from sklearn.base import clone
from sklearn.model_selection import GroupKFold, cross_val_predict

from usinas import capacidade_e_local, selecionar, serie

RAIZ = Path(__file__).resolve().parent.parent
SAIDA = RAIZ / "data" / "previsao_solar.json"
HISTORICO = RAIZ / "data" / "historico_previsao.json"  # {data: [[hora, MW previsto], ...]}
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
    sel = selecionar(con, "SOLAR")
    if not sel:
        falhar("Sem dados de usinas solares na Silver.")
    sel["estado"] = (sel["estado"] or "").upper()
    return sel


def geracao_horaria(con, chaves):
    df = pd.DataFrame(serie(con, chaves), columns=["instante", "mw"])
    df["instante"] = pd.to_datetime(df["instante"])
    return df.set_index("instante")["mw"].astype(float)


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
    # Convenções de horário diferentes: no Open-Meteo, a radiação das "07:00" é a média de 06:00-07:00;
    # no ONS, a geração das "06:00" é a média de 06:00-07:00. Desloca o clima 1 h para trás para o
    # rótulo de hora significar o mesmo intervalo nas duas fontes.
    df.index = pd.to_datetime(h["time"]) - pd.Timedelta(hours=1)
    return df


def elevacao_solar(indice, lat, lon):
    """Seno da elevação do sol no meio de cada hora (o rótulo é o início da hora, horário de Brasília, UTC-3).
    Diz ao modelo onde o sol está sem precisar de "dia do ano", que com 90 dias de histórico só serviria
    para decorar o período (inclusive os cortes de cada semana)."""
    t = indice + pd.Timedelta(minutes=30)
    doy = t.dayofyear.values
    b = 2 * math.pi * (doy - 81) / 364
    eot = 9.87 * np.sin(2 * b) - 7.53 * np.cos(b) - 1.5 * np.sin(b)          # equação do tempo, minutos
    hora_solar = t.hour.values + t.minute.values / 60 + (lon + 45) / 15 + eot / 60  # UTC-3 = meridiano 45° O
    ang = np.radians(15 * (hora_solar - 12))
    dec = np.radians(23.44 * np.sin(2 * math.pi * (284 + doy) / 365))
    la = math.radians(lat)
    return np.sin(la) * np.sin(dec) + np.cos(la) * np.cos(dec) * np.cos(ang)


def montar_features(clima, lat, lon):
    f = clima.copy()
    # horas vizinhas ajudam o modelo com a inércia de nuvens e o ângulo dos rastreadores no começo e no fim do dia
    for v in ["shortwave_radiation", "direct_radiation"]:
        f[v + "_ant"] = f[v].shift(1)
        f[v + "_prox"] = f[v].shift(-1)
    f["hora"] = f.index.hour
    f["sol_elev"] = elevacao_solar(f.index, lat, lon)
    # irradiância relativa à de céu limpo aproximada: separa "dia nublado" de "sol baixo"
    ceu_limpo = 1000 * np.clip(f["sol_elev"].to_numpy(), 0, None) ** 1.15
    f["indice_ceu"] = np.where(ceu_limpo > 50, f["shortwave_radiation"].to_numpy() / np.maximum(ceu_limpo, 1), np.nan)
    return f


def restricao_horaria(con, u):
    """Restrição de operação do ONS (constrained-off) da usina, hora a hora: MW cortados (referência - verificada,
    somado nas séries do complexo) e se houve corte. Vem do conjunto de restrição de usinas fotovoltaicas;
    vazio se ele não existir. Como só parte do complexo pode ser cortada, o disponível é verificado + cortado."""
    try:
        padrao = u.get("padrao") or u["usina"].upper()
        df = con.execute("""
            select date_trunc('hour', instante) instante,
                   sum(restringido_mw * horas_intervalo) corte_mw,
                   bool_or(com_restricao) com_lim
            from silver_restricao_solar
            where strip_accents(upper(coalesce(usina, ''))) like ?
            group by 1""", [f"%{padrao}%"]).df()
    except Exception as e:  # tabela ainda não criada
        print("AVISO: restrição solar indisponível:", e)
        return pd.DataFrame(columns=["corte_mw", "com_lim"])
    df["instante"] = pd.to_datetime(df["instante"])
    return df.set_index("instante")


def patamares(mw, ghi, capacidade):
    """Sem os dados de restrição, detecta cortes pelo formato: 3 h ou mais de potência parada (variação < 1% da
    capacidade) abaixo de 90% dela, enquanto a irradiância muda. Sol variando e usina travada = limitação externa."""
    dmw = mw.diff().abs() < 0.01 * capacidade
    dghi = ghi.diff().abs() > 0.10 * ghi.clip(lower=50)
    candidato = dmw & (mw > 0.05 * capacidade) & (mw < 0.90 * capacidade) & (ghi > 50)
    marca = pd.Series(False, index=mw.index)
    pos = {t: k for k, t in enumerate(mw.index)}

    def fechar(run):
        if len(run) >= 2 and dghi.reindex(run).any():  # 2 variações paradas seguidas = 3 horas no mesmo patamar
            marca[run] = True
            k = pos[run[0]] - 1
            if k >= 0:
                marca.iloc[k] = True  # a hora em que o patamar começou

    run = []
    for t, c in candidato.items():
        if c:
            run.append(t)
        else:
            fechar(run)
            run = []
    fechar(run)
    return marca


def quedas_sem_tempo(modelo, df, cols, capacidade):
    """Cortes que o ONS não publicou: horas em que a usina gerou menos da metade do que o próprio modelo
    espera para aquele tempo, por 3 h ou mais seguidas. Corte só reduz geração, nunca aumenta; por isso a
    limpeza é só para baixo. Nuvem fora do previsto também cai aqui às vezes; o efeito é pequeno porque o
    clima do treino é o observado (análise), não a previsão."""
    # cada dia é julgado por um modelo treinado SEM ele (validação cruzada por dia); senão o modelo decora o
    # próprio corte e passa a "esperar" a geração baixa
    grupos = df.index.normalize()
    esp_v = cross_val_predict(clone(modelo), df[cols], df["alvo"], groups=grupos,
                              cv=GroupKFold(n_splits=min(5, grupos.nunique())))
    esp = pd.Series(np.clip(esp_v, 0, None), index=df.index)
    baixo = (esp > 0.15 * capacidade) & (df["alvo"] < 0.5 * esp) & ~df["corte_ons"]
    grupo = (baixo != baixo.shift()).cumsum()
    tamanho = baixo.groupby(grupo).transform("sum")
    return baixo & (tamanho >= 3)


def historico_previsoes(nova_data, nova_curva, ger, capacidade):
    """Guarda a previsão de amanhã e mede o erro das previsões passadas contra o que a usina gerou.
    Esse é o erro que importa: a previsão foi feita com o tempo PREVISTO, um dia antes."""
    try:
        hist = json.loads(HISTORICO.read_text(encoding="utf-8")) if HISTORICO.exists() else {}
    except Exception:
        hist = {}
    hist[nova_data] = nova_curva
    hist = {d: hist[d] for d in sorted(hist)[-60:]}
    HISTORICO.write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    erros, dias = [], []
    for d, curva in hist.items():
        prev = pd.Series({pd.Timestamp(f"{d}T{h:02d}:00"): float(mw) for h, mw in curva})
        real = ger.reindex(prev.index)
        if real.notna().sum() < 20:
            continue
        ok = real.notna()
        erros.append(np.abs(prev[ok] - real[ok]).values)
        dias.append(d)
    if not dias:
        return None
    dias, erros = dias[-DIAS_TESTE:], erros[-DIAS_TESTE:]
    mae = float(np.mean(np.concatenate(erros)))
    return {"dias": len(dias), "de": dias[0], "ate": dias[-1], "mae_mw": round(mae, 2),
            "nmae_pct": round(100 * mae / capacidade, 2)}


def metricas(real, prev, capacidade):
    erro = prev - real
    mae = float(np.mean(np.abs(erro)))
    return {"mae_mw": round(mae, 2), "rmse_mw": round(float(np.sqrt(np.mean(erro ** 2))), 2),
            "nmae_pct": round(100 * mae / capacidade, 2)}


def main():
    con = duckdb.connect(str(RAIZ / "ons.duckdb"), read_only=True)
    u = escolher_usina(con)
    local = capacidade_e_local(con, "SOLAR", u)
    lat = float(os.getenv("SOLAR_LAT", "nan")); lon = float(os.getenv("SOLAR_LON", "nan"))
    origem_ponto = "SOLAR_LAT/SOLAR_LON"
    if math.isnan(lat) or math.isnan(lon):
        if local["lat"] is not None and local["lon"] is not None:
            lat, lon, origem_ponto = local["lat"], local["lon"], "localização da usina"
        elif u["estado"] in PONTO_UF:
            (lat, lon), origem_ponto = PONTO_UF[u["estado"]], "ponto representativo do estado"
        else:
            falhar(f"Sem coordenadas para a usina '{u['usina']}'. Defina SOLAR_LAT e SOLAR_LON.")
    print(f"Usina: {u['usina']} ({u['estado']}) · clima em {lat}, {lon} ({origem_ponto})")

    ger = geracao_horaria(con, u["chaves"])
    try:
        clima = obter_clima(lat, lon)
    except Exception as e:
        falhar(f"Não foi possível obter o clima no Open-Meteo: {e}")
    feats = montar_features(clima, lat, lon)
    cols = list(feats.columns)

    # o HistGradientBoosting aceita valores ausentes nas variáveis; só a geração precisa existir
    base = feats.join(ger.rename("mw"), how="inner")
    base = base[base["mw"].notna() & (base["mw"] >= 0)]
    if len(base) < 24 * 30:
        falhar(f"Histórico curto para treinar ({len(base)} horas em comum entre ONS e clima).")
    capacidade = float(base["mw"].quantile(0.995))  # referência de potência para o erro normalizado

    # ---------- cortes do ONS: o tempo explica o que a usina PODIA gerar, não a ordem de limitar ----------
    # Alvo do modelo = geração disponível: a referência do ONS nas horas com restrição e a verificada nas demais.
    # Horas cortadas sem referência (detectadas pelo patamar) ficam fora do treino e da avaliação.
    lim = restricao_horaria(con, u).reindex(base.index)
    base["corte_ons"] = lim["com_lim"].fillna(False).astype(bool) & (lim["corte_mw"].fillna(0) > 0)
    base["ref_mw"] = np.where(base["corte_ons"], base["mw"] + lim["corte_mw"].fillna(0), np.nan)
    base["patamar"] = patamares(base["mw"], base["shortwave_radiation"].fillna(0), capacidade) & ~base["corte_ons"]
    base["alvo"] = np.where(base["corte_ons"], base["ref_mw"], base["mw"])
    base["queda"] = False
    base["limpa"] = ~base["corte_ons"] & ~base["patamar"]
    capacidade = max(capacidade, float(base["alvo"].quantile(0.995)))
    print(f"Horas com corte do ONS: {int(base['corte_ons'].sum())} · patamares suspeitos fora do treino: {int(base['patamar'].sum())}")

    # ---------- avaliação: últimos 14 dias fora do treino ----------
    corte = base.index.max().normalize() - pd.Timedelta(days=DIAS_TESTE - 1)
    treino, teste = base[base.index < corte], base[base.index >= corte]
    modelo = HistGradientBoostingRegressor(max_iter=400, learning_rate=0.05, max_leaf_nodes=31,
                                           l2_regularization=1.0, random_state=42)
    tr = treino[~treino["patamar"]]
    tr = tr[~quedas_sem_tempo(modelo, tr, cols, capacidade)]
    modelo.fit(tr[cols], tr["alvo"])
    prev_ml = np.clip(modelo.predict(teste[cols]), 0, None)
    prev_ml[np.nan_to_num(teste["shortwave_radiation"].values) <= 0] = 0

    # cortes não publicados no período de teste: mesma regra do treino (real < 50% do esperado por 3 h ou mais)
    teste = teste.copy()
    esp = pd.Series(prev_ml, index=teste.index)
    baixo = (esp > 0.15 * capacidade) & (teste["mw"] < 0.5 * esp) & ~teste["corte_ons"] & ~teste["patamar"]
    teste["queda"] = baixo & (baixo.groupby((baixo != baixo.shift()).cumsum()).transform("sum") >= 3)

    # avaliação principal nas horas sem corte: nelas, verificado = disponível, e a comparação é justa para todos.
    # O erro em todas as horas também é publicado, para ninguém precisar confiar só na versão limpa.
    limpa = (teste["limpa"] & ~teste["queda"]).values
    persist = ger.shift(24, freq="h").reindex(teste.index)  # mesma hora do dia anterior
    ok = persist.notna().values & limpa
    lin = LinearRegression().fit(tr[["shortwave_radiation"]].fillna(0), tr["alvo"])
    prev_lin = np.clip(lin.predict(teste[["shortwave_radiation"]].fillna(0)), 0, None)

    m_ml = metricas(teste["mw"].values[limpa], prev_ml[limpa], capacidade)
    m_ml_todas = metricas(teste["mw"].values, prev_ml, capacidade)
    m_pers = metricas(teste["mw"].values[ok], persist.values[ok], capacidade)
    m_lin = metricas(teste["mw"].values[limpa], prev_lin[limpa], capacidade)
    ganho = round(100 * (1 - m_ml["mae_mw"] / m_pers["mae_mw"]), 1) if m_pers["mae_mw"] > 0 else None
    print(f"Teste {DIAS_TESTE} dias | modelo nMAE {m_ml['nmae_pct']}% | persistência {m_pers['nmae_pct']}% | "
          f"linear {m_lin['nmae_pct']}% | ganho sobre persistência {ganho}% | modelo em todas as horas "
          f"{m_ml_todas['nmae_pct']}% | horas com corte suspeito no teste: {int(teste['queda'].sum())}")

    # ---------- modelo final e previsão de amanhã ----------
    final = base[~base["patamar"]]
    descartes = quedas_sem_tempo(modelo, final, cols, capacidade)
    base.loc[descartes[descartes].index, "queda"] = True
    final = final[~descartes]
    modelo.fit(final[cols], final["alvo"])
    print(f"Horas fora do treino por queda que o tempo não explica: {int(descartes.sum())}")
    amanha = (pd.Timestamp.now(tz=FUSO).tz_localize(None).normalize() + pd.Timedelta(days=1))
    fut = feats[(feats.index >= amanha) & (feats.index < amanha + pd.Timedelta(days=1))]
    if fut.empty:
        falhar("O Open-Meteo não retornou a previsão do tempo para amanhã.")
    prev_fut = np.clip(modelo.predict(fut[cols]), 0, None)
    prev_fut[np.nan_to_num(fut["shortwave_radiation"].values) <= 0] = 0

    curva_amanha = [[int(i.hour), round(float(v), 1)] for i, v in zip(fut.index, prev_fut)]
    dia_seguinte = historico_previsoes(amanha.strftime("%Y-%m-%d"), curva_amanha, ger, capacidade)
    if dia_seguinte:
        print(f"Erro real da previsão do dia seguinte ({dia_seguinte['dias']} dias): nMAE {dia_seguinte['nmae_pct']}%")

    ult7 = teste[teste.index >= teste.index.max().normalize() - pd.Timedelta(days=6)]
    prev7 = pd.Series(prev_ml, index=teste.index).reindex(ult7.index)

    saida = {
        "atualizado_em": agora(),
        "usina": u["usina"], "estado": u["estado"], "fixada": u["fixada"], "lat": lat, "lon": lon,
        "ponto_clima": origem_ponto,
        "capacidade_ref_mw": round(capacidade, 1),
        "modelo": "HistGradientBoostingRegressor (scikit-learn)",
        "variaveis": ["irradiância global, direta e difusa", "temperatura", "nebulosidade", "hora", "posição do sol",
                      "índice de céu limpo"],
        "fontes": "Geração: ONS Dados Abertos (CC-BY 4.0). Clima: Open-Meteo (CC-BY 4.0).",
        "horas_treino": int((~base["patamar"]).sum()), "dias_teste": DIAS_TESTE,
        "horas_corte_ons": int(base["corte_ons"].sum()), "horas_patamar": int(base["patamar"].sum()),
        "horas_avaliadas": int(limpa.sum()), "horas_queda_treino": int(base["queda"].sum()),
        "variaveis_modelo": cols,
        "alinhamento_horario": "clima deslocado -1 h (Open-Meteo: média da hora anterior; ONS: hora iniciando no rótulo)",
        "metricas": {"modelo": m_ml, "modelo_todas_horas": m_ml_todas, "persistencia": m_pers,
                     "linear_irradiancia": m_lin, "ganho_sobre_persistencia_pct": ganho,
                     "dia_seguinte_real": dia_seguinte},
        # [hora, verificada, prevista, podia gerar (referência ONS) ou null, corte: "ons" | "patamar" | "queda" | null]
        "backtest": [[i.strftime("%Y-%m-%dT%H:00"), round(float(r), 2), round(float(p), 2),
                      None if pd.isna(ref) else round(float(ref), 2),
                      "ons" if c else ("patamar" if pt else ("queda" if q else None))]
                     for i, r, p, ref, c, pt, q in zip(ult7.index, ult7["mw"], prev7.values, ult7["ref_mw"],
                                                       ult7["corte_ons"], ult7["patamar"], ult7["queda"])],
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
