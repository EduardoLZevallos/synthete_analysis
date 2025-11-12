import polars as pl
import numpy as np
from typing import Union
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score

def get_synthete_band(country_code: str, df: pl.DataFrame, neighbor_n: int = 15, metric_col: str = "cagr") -> pl.DataFrame | None:
    """
    Compute Doyle-style synthetic peer band for a specific country.
    
    Creates a synthetic peer group by finding neighbors based on GDP per capita,
    then selecting the best performers while excluding extreme outliers.
    
    Args:
        country_code (str): ISO country code to find peers for
        df (pl.DataFrame): DataFrame containing country economic data
        neighbor_n (int, optional): Number of neighbors to consider on each side. Defaults to 15.
        metric_col (str, optional): Column name for ranking metric. Defaults to "cagr".
    
    Returns:
        Optional[pl.DataFrame]: DataFrame containing the synthetic peer band countries,
                               or None if country not found
    """
    df = df.sort("average_gdp_per_capita")
    if "index" not in df.columns:
        df = df.with_row_index("index")

    target_row = df.filter(pl.col("country") == country_code)
    if target_row.is_empty():
        return None
    idx = target_row.select("index").item()

    # 15 neighbors below and above (not including self)
    lower = df.filter(pl.col("index") < idx).tail(neighbor_n)
    upper = df.filter(pl.col("index") > idx).head(neighbor_n)

    # Top 5 by metric in each, drop the highest; bottom 5 by metric in each, drop the lowest
    top5lower = lower.sort(metric_col, descending=True).head(5)
    top5upper = upper.sort(metric_col, descending=True).head(5)
    band_lower = top5lower.tail(4)  # drop topmost
    band_upper = top5upper.tail(4)
    band_countries = pl.concat([band_lower, band_upper])

    return band_countries


def get_all_synthete_stats_vectorized(df: pl.DataFrame, neighbor_n: int = 15, metric_col: str = "cagr") -> pl.DataFrame:
    """
    Fixed vectorized computation that matches get_synthete_band exactly.
    
    Computes synthetic statistics (mean, second best, second worst) for all countries
    in the dataset using vectorized operations for better performance.
    
    Args:
        df (pl.DataFrame): DataFrame containing country economic data
        neighbor_n (int, optional): Number of neighbors to consider on each side. Defaults to 15.
        metric_col (str, optional): Column name for ranking metric. Defaults to "cagr".
    
    Returns:
        pl.DataFrame: DataFrame with synthetic statistics for each country including
                     synthete_mean, band_second_best, band_second_worst columns
    """
    df_sorted = df.sort("average_gdp_per_capita")
    
    results:list[pl.DataFrame] = []
    
    for country_data in df_sorted.iter_rows(named=True):
        country = country_data['country']
        idx = country_data['index']
        
        # Get neighbors - exactly like get_synthete_band
        lower = df_sorted.filter(pl.col("index") < idx).tail(neighbor_n)
        upper = df_sorted.filter(pl.col("index") > idx).head(neighbor_n)
        
        # Apply the EXACT same logic as get_synthete_band
        if lower.height == 0 and upper.height == 0:
            continue  # No neighbors at all
            
        # Get top 5 from each side (if they exist)
        top5lower = lower.sort(metric_col, descending=True).head(5) if lower.height > 0 else pl.DataFrame()
        top5upper = upper.sort(metric_col, descending=True).head(5) if upper.height > 0 else pl.DataFrame()
        
        # Drop the topmost from each (if they have any)
        band_lower = top5lower.tail(4) if top5lower.height > 0 else pl.DataFrame()
        band_upper = top5upper.tail(4) if top5upper.height > 0 else pl.DataFrame()
        
        # Combine - this matches get_synthete_band's pl.concat logic
        if band_lower.height == 0 and band_upper.height == 0:
            band_countries = pl.DataFrame()
        elif band_lower.height == 0:
            band_countries = band_upper
        elif band_upper.height == 0:
            band_countries = band_lower
        else:
            band_countries = pl.concat([band_lower, band_upper])
        
        # Apply the same < 8 check as your original code
        if band_countries.height < 8:
            continue
            
        # Compute statistics using Polars aggregations
        stats = band_countries.select([
            pl.col(metric_col).mean().alias("synthete_mean"),
            pl.col(metric_col).sort().slice(-2, 1).first().alias("band_second_best"), 
            pl.col(metric_col).sort().slice(1, 1).first().alias("band_second_worst")
        ]).with_columns([
            pl.lit(country).alias("country"),
            pl.lit(idx).alias("index")
        ])
        
        results.append(stats)
    
    if not results:
        return pl.DataFrame()
        
    return pl.concat(results)


def compute_synthete_means(target_country: str, df: pl.DataFrame, neighbor_n: int = 15) -> tuple[float, float]:
    """
    Compute synthetic means for primary balance and CAGR for a target country.
    
    Args:
        target_country (str): ISO country code to compute means for
        df (pl.DataFrame): DataFrame containing country economic data
        neighbor_n (int, optional): Number of neighbors to consider. Defaults to 15.
    
    Returns:
        tuple[float, float]: tuple containing (pb_mean, cagr_mean) where:
            - pb_mean: Mean primary balance of synthetic peer band
            - cagr_mean: Mean CAGR of synthetic peer band
            Returns (np.nan, np.nan) if insufficient data
    """
    band_df = get_synthete_band(target_country, df, neighbor_n=neighbor_n, metric_col="cagr")
    if band_df is None or band_df.shape[0] < 8:
        return np.nan, np.nan
    pb_values = band_df['average_primary_balance'].to_numpy()
    cagr_values = band_df['cagr'].to_numpy()
    # Guard against NoneType or nan, use np.nanmean
    pb_mean = np.nanmean([v for v in pb_values if v is not None])
    cagr_mean = np.nanmean([v for v in cagr_values if v is not None])
    return pb_mean, cagr_mean

def get_all_synthete_means_vectorized(df: pl.DataFrame, neighbor_n: int = 15) -> pl.DataFrame:
    """
    Compute synthete means for all countries in a vectorized way.
    
    Efficiently computes synthetic peer means for all countries that have
    sufficient data for meaningful comparison.
    
    Args:
        df (pl.DataFrame): DataFrame containing country economic data
        neighbor_n (int, optional): Number of neighbors to consider. Defaults to 15.
    
    Returns:
        pl.DataFrame: DataFrame containing synthetic and actual values for each country
                     with columns: country, synth_pb, synth_cagr, actual_pb, actual_cagr
    """
    df_sorted = df.sort("average_gdp_per_capita")
    
    results = []
    
    for country_data in df_sorted.iter_rows(named=True):
        country = country_data['country']
        
        # Skip countries without primary balance data
        if country_data["average_primary_balance"] is None:
            continue
            
        band_df = get_synthete_band(country, df_sorted, neighbor_n=neighbor_n, metric_col="cagr")
        if band_df is None or band_df.shape[0] < 8:
            continue
            
        # Use Polars aggregations
        stats = band_df.select([
            pl.col("average_primary_balance").mean().alias("synth_pb"),
            pl.col("cagr").mean().alias("synth_cagr")
        ]).with_columns([
            pl.lit(country).alias("country"),
            pl.lit(country_data["average_primary_balance"]).alias("actual_pb"),
            pl.lit(country_data["cagr"]).alias("actual_cagr")
        ])
        
        results.append(stats)
    
    if not results:
        return pl.DataFrame()
        
    return pl.concat(results)

def compute_synthete_means_country(target_country: str, df: pl.DataFrame, neighbor_n: int = 15) -> tuple[float, float]:
    """
    Compute synthetic means for primary balance and CAGR for a single target country.
    
    Args:
        target_country (str): ISO country code to compute means for
        df (pl.DataFrame): DataFrame containing country economic data
        neighbor_n (int, optional): Number of neighbors to consider. Defaults to 15.
    
    Returns:
        tuple[float, float]: tuple containing (pb_mean, cagr_mean) where:
            - pb_mean: Mean primary balance of synthetic peer band
            - cagr_mean: Mean CAGR of synthetic peer band
            Returns (np.nan, np.nan) if insufficient data
    """
    band_df = get_synthete_band(target_country, df, neighbor_n=neighbor_n, metric_col="cagr")
    if band_df is None or band_df.shape[0] < 8:
        return (np.nan, np.nan)
    pb_values = band_df['average_primary_balance'].to_numpy()
    cagr_values = band_df['cagr'].to_numpy()
    pb_mean = np.nanmean([v for v in pb_values if v is not None])
    cagr_mean = np.nanmean([v for v in cagr_values if v is not None])
    return (pb_mean, cagr_mean)


def compute_all_synthete_means(df: pl.DataFrame) -> pl.DataFrame:
    """
    Compute synthete means for all countries at once.
    
    Processes all countries in the dataset to compute their synthetic peer
    means for primary balance and CAGR metrics.
    
    Args:
        df (pl.DataFrame): DataFrame containing country economic data
    
    Returns:
        pl.DataFrame: DataFrame with columns country, synth_pb, synth_cagr
                     containing synthetic means for each valid country
    """
    results = []
    df_sorted = df.sort("average_gdp_per_capita")

    for country_data in df_sorted.iter_rows(named=True):
        country = country_data['country']

        # Skip countries without primary balance data
        if country_data["average_primary_balance"] is None:
            continue

        pb, cagr = compute_synthete_means_country(country, df_sorted)
        results.append({
            "country": country,
            "synth_pb": pb if not np.isnan(pb) else None,
            "synth_cagr": cagr if not np.isnan(cagr) else None
        })
    
    return pl.DataFrame(results)

# Function to compute regression stats for each group
def compute_group_regression(group_df: pl.DataFrame) -> dict[str, Union[float, np.floating]]:
    """
    Compute regression statistics for a group using linear regression.
    
    Performs linear regression with dev_pb as independent variable (X) and 
    dev_cagr as dependent variable (Y) to analyze the relationship between
    primary balance deviation and CAGR deviation.
    
    Args:
        group_df (pl.DataFrame): DataFrame containing the group data with
                               'dev_pb' and 'dev_cagr' columns
    
    Returns:
        dict[str, Union[float, np.floating]]: dictionary containing:
            - slope: The regression coefficient (slope of the line)
            - intercept: The y-intercept of the regression line
            - r2: The coefficient of determination (R-squared value)
            Returns NaN values if group has <= 1 observation
    """
    if len(group_df) <= 1:
        return {"slope": np.nan, "intercept": np.nan, "r2": np.nan}
    
    x = group_df["dev_pb"].to_numpy().reshape(-1, 1)
    y = group_df["dev_cagr"].to_numpy()
    
    reg = LinearRegression().fit(x, y)
    y_pred = reg.predict(x)
    
    return {
        "slope": reg.coef_[0],
        "intercept": reg.intercept_,
        "r2": r2_score(y, y_pred)
    }