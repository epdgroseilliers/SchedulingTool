
import streamlit as st
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL


@st.cache_resource
def get_engine() -> Engine:
    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        "Server=MAGSQLSERVER;"
        "Database=Physique;"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    connection_url = URL.create(
        "mssql+pyodbc", query={"odbc_connect": connection_string}
    )
    return create_engine(connection_url)


@st.cache_resource
def get_bilateral_engine() -> Engine:
    """Engine for the server holding PhysiqueBilateral — a different box
    from get_engine()'s. Database=master because the bilateral tables are
    reached by three-part name, as the Excel macro did.
    """
    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        r"Server=MAGAPPSERVER\SQLSERVER;"
        "Database=master;"
        "Trusted_Connection=yes;"
        "Encrypt=yes;"
        "TrustServerCertificate=yes;"
    )
    connection_url = URL.create(
        "mssql+pyodbc", query={"odbc_connect": connection_string}
    )
    return create_engine(connection_url)
