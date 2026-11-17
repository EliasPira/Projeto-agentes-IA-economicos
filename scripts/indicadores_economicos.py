# scripts/indicadores_economicos.py

import os
import sys
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# ============================================================
# Carregar variáveis de ambiente (.env para uso local)
# ============================================================
load_dotenv()

# ============================================================
# Configurações de diretório e arquivo de saída
# ============================================================
ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

ARQUIVO_SAIDA = DATA_DIR / "indicadores_economicos.csv"

# ============================================================
# Indicadores - BACEN (SGS)
# ============================================================
INDICADORES_SGS = {
    "IPCA": 433,
    "SELIC": 432,
    "PIB": 4380,
    "DÓLAR": 1,
    "COMMODITIES": 22795,
    "IGP-M": 189,
}

GRAMAS_POR_ONCA_TROY = 31.1035

# ============================================================
# Indicadores - Ipeadata (API OData4)
# ============================================================
INDICADORES_IPEA = {
    "PETROLEO_BRENT": "EIA366_PBRENT366",  # US$/barril, FOB
}

IPEA_BASE_URL = "http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='{codigo}')"


# ============================================================
# Função para coletar indicadores no BACEN (SGS)
# ============================================================

def coletar_indicadores_bacen(
    indicadores: dict,
    n_ultimos: int = 20
) -> pd.DataFrame:
    """
    Coleta os últimos `n_ultimos` registros de cada série do SGS (BACEN)
    e retorna um DataFrame consolidado com as colunas:
    [data, valor, indicador, data_coleta].
    """
    todos_dados = []

    for nome, codigo in indicadores.items():
        url = (
            f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}"
            f"/dados/ultimos/{n_ultimos}?formato=json"
        )

        print(f"🔄 Coletando indicador '{nome}' (código {codigo}) do BACEN...")
        try:
            response = requests.get(url, timeout=30)
        except Exception as e:
            print(f"❌ Erro de conexão ao buscar {nome} (código {codigo}): {e}")
            continue

        if response.status_code != 200:
            print(
                f"❌ Erro HTTP ao buscar {nome} (código {codigo}). "
                f"Status: {response.status_code}"
            )
            continue

        try:
            dados = response.json()
        except Exception as e:
            print(f"❌ Erro ao decodificar JSON para {nome}: {e}")
            continue

        if not dados:
            print(f"ℹ️ Nenhum dado retornado para {nome}.")
            continue

        df = pd.DataFrame(dados)

        if "valor" not in df.columns or "data" not in df.columns:
            print(f"⚠️ Estrutura inesperada ao buscar {nome}: {df.columns.tolist()}")
            continue

        df["valor"] = (
            df["valor"]
            .astype(str)
            .str.replace(",", ".", regex=False)
        )
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df.dropna(subset=["valor"], inplace=True)

        if df.empty:
            print(f"ℹ️ Após conversão, não há valores numéricos válidos para {nome}.")
            continue

        df["indicador"] = nome
        df["data_coleta"] = datetime.now().date()

        todos_dados.append(df[["data", "valor", "indicador", "data_coleta"]])

    if not todos_dados:
        print("⚠️ Nenhum indicador do BACEN pôde ser coletado com sucesso.")
        return pd.DataFrame(columns=["data", "valor", "indicador", "data_coleta"])

    return pd.concat(todos_dados, ignore_index=True)


# ============================================================
# Função para coletar o Ouro (R$/g) via yfinance
# ============================================================

def coletar_ouro_yfinance(df_dolar: pd.DataFrame, n_ultimos: int = 20) -> pd.DataFrame:
    """
    Coleta o histórico de preço do ouro (futuro GC=F, em USD/onça troy)
    via yfinance, converte para R$/grama usando a cotação do dólar
    (série SGS 1) já coletada, e retorna um DataFrame no mesmo formato
    dos demais indicadores: [data, valor, indicador, data_coleta].
    """
    print("🔄 Coletando indicador 'OURO_R$/g' (GC=F) via yfinance...")

    try:
        ouro = yf.Ticker("GC=F")
        hist = ouro.history(period=f"{n_ultimos + 10}d")
    except Exception as e:
        print(f"❌ Erro ao buscar dados do ouro via yfinance: {e}")
        return pd.DataFrame(columns=["data", "valor", "indicador", "data_coleta"])

    if hist.empty:
        print("ℹ️ Nenhum dado retornado pelo yfinance para o ouro.")
        return pd.DataFrame(columns=["data", "valor", "indicador", "data_coleta"])

    hist = hist.reset_index()
    hist["data"] = pd.to_datetime(hist["Date"]).dt.strftime("%d/%m/%Y")
    hist = hist[["data", "Close"]].rename(columns={"Close": "usd_onca"})
    hist.dropna(subset=["usd_onca"], inplace=True)
    hist = hist.tail(n_ultimos)

    if df_dolar.empty:
        print("⚠️ Cotação do dólar não disponível; não é possível converter o ouro para R$/g.")
        return pd.DataFrame(columns=["data", "valor", "indicador", "data_coleta"])

    dolar = df_dolar[["data", "valor"]].rename(columns={"valor": "usd_brl"})

    # Usa o último dólar disponível para preencher dias sem cotação exata
    merged = pd.merge(hist, dolar, on="data", how="left")
    merged["usd_brl"] = merged["usd_brl"].ffill().bfill()
    merged.dropna(subset=["usd_brl"], inplace=True)

    if merged.empty:
        print("ℹ️ Não foi possível casar datas do ouro com a cotação do dólar.")
        return pd.DataFrame(columns=["data", "valor", "indicador", "data_coleta"])

    merged["valor"] = (merged["usd_onca"] / GRAMAS_POR_ONCA_TROY) * merged["usd_brl"]
    merged["indicador"] = "OURO_R$/g"
    merged["data_coleta"] = datetime.now().date()

    return merged[["data", "valor", "indicador", "data_coleta"]]


# ============================================================
# Função para coletar indicadores no Ipeadata (OData4)
# ============================================================

def coletar_indicadores_ipea(
    indicadores: dict,
    n_ultimos: int = 20
) -> pd.DataFrame:
    """
    Coleta os últimos `n_ultimos` registros de cada série do Ipeadata
    (API OData4, sem necessidade de chave) e retorna um DataFrame
    consolidado com as colunas: [data, valor, indicador, data_coleta].
    """
    todos_dados = []

    for nome, codigo in indicadores.items():
        url = IPEA_BASE_URL.format(codigo=codigo)
        params = {"$format": "json"}

        print(f"🔄 Coletando indicador '{nome}' (série '{codigo}') do Ipeadata...")
        try:
            response = requests.get(url, params=params, timeout=30)
        except Exception as e:
            print(f"❌ Erro de conexão ao buscar {nome} (série {codigo}): {e}")
            continue

        if response.status_code != 200:
            print(
                f"❌ Erro HTTP ao buscar {nome} (série {codigo}). "
                f"Status: {response.status_code}"
            )
            continue

        try:
            payload = response.json()
        except Exception as e:
            print(f"❌ Erro ao decodificar JSON para {nome}: {e}")
            continue

        registros = payload.get("value", [])
        if not registros:
            print(f"ℹ️ Nenhum dado retornado para {nome}.")
            continue

        df = pd.DataFrame(registros)

        # Colunas típicas do Ipeadata: VALDATA (data) e VALVALOR (valor)
        if "VALDATA" not in df.columns or "VALVALOR" not in df.columns:
            print(f"⚠️ Estrutura inesperada ao buscar {nome}: {df.columns.tolist()}")
            continue

        df = df[["VALDATA", "VALVALOR"]].rename(
            columns={"VALDATA": "data", "VALVALOR": "valor"}
        )

        # Normalizar data (vem em formato ISO com timezones mistos,
        # ex: 2026-11-11T00:00:00-02:00) -> usar utc=True para evitar erro
        df["data"] = pd.to_datetime(df["data"], errors="coerce", utc=True).dt.strftime("%d/%m/%Y")
        df["valor"] = pd.to_numeric(df["valor"], errors="coerce")
        df.dropna(subset=["valor", "data"], inplace=True)

        if df.empty:
            print(f"ℹ️ Após conversão, não há valores numéricos válidos para {nome}.")
            continue

        # Ordenar por data e pegar os últimos N registros
        df = df.sort_values("data", key=lambda s: pd.to_datetime(s, format="%d/%m/%Y"))
        df = df.tail(n_ultimos)

        df["indicador"] = nome
        df["data_coleta"] = datetime.now().date()

        todos_dados.append(df[["data", "valor", "indicador", "data_coleta"]])

    if not todos_dados:
        print("⚠️ Nenhum indicador do Ipeadata pôde ser coletado com sucesso.")
        return pd.DataFrame(columns=["data", "valor", "indicador", "data_coleta"])

    return pd.concat(todos_dados, ignore_index=True)



# ============================================================
# Execução principal
# ============================================================

def main():
    df_bacen = coletar_indicadores_bacen(INDICADORES_SGS, n_ultimos=20)
    df_ipea = coletar_indicadores_ipea(INDICADORES_IPEA, n_ultimos=20)

    df_dolar = df_bacen[df_bacen["indicador"] == "DÓLAR"].copy() if not df_bacen.empty else pd.DataFrame()
    df_ouro = coletar_ouro_yfinance(df_dolar, n_ultimos=20)

    df_indicadores = pd.concat([df_bacen, df_ipea, df_ouro], ignore_index=True)

    if df_indicadores.empty:
        print("ℹ️ Nenhum dado consolidado para salvar em CSV.")
        return

    try:
        df_indicadores.to_csv(ARQUIVO_SAIDA, index=False, encoding="utf-8-sig")
        print(f"✅ Arquivo '{ARQUIVO_SAIDA}' salvo com sucesso ({len(df_indicadores)} linhas).")
    except Exception as e:
        print(f"❌ Erro ao salvar arquivo '{ARQUIVO_SAIDA}': {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
