import requests
from functools import cache


@cache
def query_imf(query):
    return requests.get(query).json()


@cache
def get_metric(metric: str) -> dict[str, dict[str, float]]:
    return requests.get(
        f"https://www.imf.org/external/datamapper/api/v1/{metric}"
    ).json()
