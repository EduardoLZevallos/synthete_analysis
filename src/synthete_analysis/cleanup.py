import polars as pl

def normalize_metric_dict(metric_dict):
    rows = []
    for country, years in metric_dict.items():
        for year, value in years.items():
            rows.append({"country": country, "year": int(year), "value": value})
    return rows

def add_all_years(df):
    """ this is used to figure out which years are missing from a metric.  a year with a null lets me easily see missing data points """
    years = pl.DataFrame({"year": range(1980, 2031)})
    countries = df.select("country").unique()
    full_grid = countries.join(years, how="cross")
    return full_grid.join(df, on=["country", "year"], how="left")
