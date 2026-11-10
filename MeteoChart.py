from __future__ import annotations

import html
import time
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import extra_streamlit_components as stx


STATIONS_URL = "https://klimat.imgw.pl/json/stacje.json"
WEATHER_DATA_URL = "https://hydro-back.imgw.pl/station/meteo/data"

DEFAULT_STATION_ID = "352230399"
REQUEST_TIMEOUT_SECONDS = 30


st.set_page_config(
    page_title="Temperatura IMGW",
    page_icon="🌡️",
    layout="wide",
)


def get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://klimat.imgw.pl/",
        "Origin": "https://klimat.imgw.pl",
    }

    response = requests.get(
        url,
        params=params,
        headers=headers,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )

    if response.status_code == 403:
        raise requests.HTTPError(
            "Serwer IMGW odrzucił żądanie (HTTP 403). "
            "Sprawdź, czy adres API jest dostępny i spróbuj ponownie."
        )

    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=3600)
def load_stations() -> list[dict[str, Any]]:
    stations = get_json(STATIONS_URL)

    if not isinstance(stations, list):
        raise ValueError("Nieprawidłowy format listy stacji.")

    filtered_stations = [
        station
        for station in stations
        if station.get("rodzaj stacji") == "synoptyczna"
        and not station.get("data_do")
        and station.get("kod 9-znakowy") is not None
    ]

    return filtered_stations


@st.cache_data(ttl=300)
def load_weather_data(
    station_id: str,
    hours_interval: int,
) -> pd.DataFrame:

    response = get_json(
        WEATHER_DATA_URL,
        params={
            "id": station_id,
            "hoursInterval": hours_interval,
        },
    )

    def parse_series(
        entries: list[dict[str, Any]],
        column: str,
    ) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        warsaw_timezone = ZoneInfo("Europe/Warsaw")

        for entry in entries:
            raw_date = entry.get("date")
            value = entry.get("value")

            if raw_date is None or value is None:
                continue

            try:
                # Daty z API są traktowane jako UTC.
                measured_date = pd.to_datetime(
                    raw_date,
                    utc=True,
                ).tz_convert(warsaw_timezone)
                measurement = float(value)
            except (TypeError, ValueError):
                continue

            rows.append(
                {
                    "date": measured_date,
                    column: measurement,
                }
            )

        if not rows:
            return pd.DataFrame(
                {
                    "date": pd.Series(dtype="datetime64[ns, UTC]"),
                    column: pd.Series(dtype="float64"),
                }
            )

        return pd.DataFrame(rows)

    temperature_data = parse_series(
        response.get("temperature", []),
        "temperature",
    )
    precipitation_data = parse_series(
        response.get("precip", []),
        "precipitation",
    )

    return temperature_data.merge(
        precipitation_data,
        on="date",
        how="outer",
    )


def calculate_hours_interval(start_date: date) -> int:
    now = datetime.now()
    start_datetime = datetime.combine(start_date, datetime.min.time())

    hours_interval = int((now - start_datetime).total_seconds() / 3600) + 1
    return max(1, min(hours_interval, 720))


def format_station(station: dict[str, Any]) -> str:
    name = str(station.get("nazwa stacji", "Nieznana stacja"))
    station_id = str(station.get("kod 9-znakowy", ""))

    return f"{name} ({station_id})"


def create_weather_chart(data: pd.DataFrame) -> px.line:
    chart_data = data.copy()
    chart_data["date_label"] = chart_data["date"].dt.strftime("%Y-%m-%d %H:%M")

    figure = px.line(
        chart_data,
        x="date",
        y="temperature",
        markers=True,
        labels={
            "date": "Data i godzina",
            "temperature": "Temperatura (°C)",
        },
        hover_data={
            "date_label": True,
            "temperature": ":.1f",
        },
    )

    figure.update_traces(
        line={"color": "deepskyblue", "width": 3},
        marker={"size": 6},
        hovertemplate=(
            "Data: %{customdata[0]}<br>"
            "Temperatura: %{y:.1f} °C"
            "<extra></extra>"
        ),
    )

    precipitation_data = chart_data.dropna(subset=["precipitation"])
    figure.add_trace(
        go.Bar(
            x=precipitation_data["date"],
            y=precipitation_data["precipitation"],
            name="Opady",
            marker_color="rgba(144, 238, 144, 0.5)",
            yaxis="y2",
            customdata=precipitation_data[["date_label"]].to_numpy(),
            hovertemplate=(
                "Data: %{customdata[0]}<br>"
                "Opady: %{y:.2f} mm"
                "<extra></extra>"
            ),
        )
    )

    figure.update_layout(
        autosize=True,
        height=None, #300,
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
        yaxis2={
            "title": "Opady (mm/h)",
            "overlaying": "y",
            "side": "right",
            "rangemode": "tozero",
            "tickformat": ".1f"
        },
        barmode="overlay",
    )

    return figure


def main() -> None:
    #st.title("Temperatura na stacjach IMGW")

    try:
        stations = load_stations()
    except requests.RequestException as error:
        st.error(f"Nie udało się pobrać listy stacji: {error}")
        return
    except ValueError as error:
        st.error(str(error))
        return

    if not stations:
        st.warning("Nie znaleziono aktywnych stacji synoptycznych.")
        return

    station_ids = [
        str(station["kod 9-znakowy"])
        for station in stations
    ]

    stations_by_id = {
        str(station["kod 9-znakowy"]): station
        for station in stations
    }

    cookie_manager = stx.CookieManager(key="station_cookie_manager")
    saved_station_id = st.context.cookies.get("selected_station_id")
    if "selected_station_id" not in st.session_state:
        default_station_id = (
            DEFAULT_STATION_ID
            if DEFAULT_STATION_ID in stations_by_id
            else station_ids[0]
        )
        st.session_state.selected_station_id = (
            saved_station_id
            if saved_station_id in stations_by_id
            else default_station_id
        )
    st.session_state.setdefault(
        "_station_cookie_synced_id",
        saved_station_id,
    )

    if "start_date" not in st.session_state:
        st.session_state.start_date = datetime.now().date()

    with st.sidebar:
        st.header("Ustawienia")

        selected_station_id = st.selectbox(
            "Stacja",
            options=station_ids,
            format_func=lambda station_id: format_station(
                stations_by_id[station_id]
            ),
            key="selected_station_id",
        )

        synced_station_id = st.session_state._station_cookie_synced_id
        if synced_station_id != selected_station_id:
            cookie_manager.set(
                "selected_station_id",
                selected_station_id,
                key="save_selected_station",
                path="/",
                expires_at=datetime.now() + timedelta(days=365),
                same_site="lax",
            )
            st.session_state._station_cookie_synced_id = selected_station_id
            if synced_station_id in stations_by_id:
                st.cache_data.clear()

        selected_date = st.date_input(
            "Okres od",
            value=st.session_state.start_date,
            max_value=datetime.now().date(),
        )

        if selected_date > datetime.now().date():
            st.error("Data początkowa nie może być w przyszłości.")
            selected_date = datetime.now().date()

        st.session_state.start_date = selected_date

        if st.button("Odśwież", width='stretch'):
            st.cache_data.clear()
            st.rerun()

        # st.caption(
        #     f"Ostatni zapis stacji: "
        #     f"{html.escape(format_station(stations_by_id[selected_station_id]))}"
        # )

    hours_interval = calculate_hours_interval(selected_date)

    try:
        data = load_weather_data(
            selected_station_id,
            hours_interval,
        )
    except requests.RequestException as error:
        st.error(f"Nie udało się pobrać danych meteorologicznych: {error}")
        if getattr(error, "response", None) is not None:
            st.code(
                f"HTTP: {error.response.status_code}\n"
                f"URL: {error.response.url}\n"
                f"Headers: {dict(error.response.headers)}\n"
                f"Body: {error.response.text[:1000]}"
    )
        return

    if data.empty:
        st.info("Brak danych meteorologicznych dla wybranego okresu.")
        return
    
    # Ujednolicenie dat: UTC-aware -> naive datetime.
    # Zachowuje lokalną godzinę już skorygowaną wcześniej o +1 godzinę.
    if isinstance(data["date"].dtype, pd.DatetimeTZDtype):
        data["date"] = data["date"].dt.tz_localize(None)

    now = pd.Timestamp.now()
    start_datetime = pd.Timestamp.combine(
        selected_date,
        datetime.min.time(),
    )

    data = data[
        (data["date"] >= start_datetime)
        & (data["date"] <= now)
    ].sort_values("date")

    if data.empty:
        st.info("Brak danych w wybranym zakresie dat.")
        return

    st.subheader("Temperatura na stacji IMGW: " + format_station(stations_by_id[selected_station_id]))
    st.plotly_chart(
        create_weather_chart(data),
        width ='stretch',
        height = 'stretch'
    )


if __name__ == "__main__":
    main()