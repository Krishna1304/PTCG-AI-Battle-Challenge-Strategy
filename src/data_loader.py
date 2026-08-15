"""Utilities for loading the Pokemon TCG card datasets."""

import re
from pathlib import Path
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Energy type symbol -> full name, based on the symbols seen in the Type/Cost columns
ENERGY_SYMBOLS = {
    "G": "Grass", "R": "Fire", "W": "Water", "L": "Lightning",
    "P": "Psychic", "F": "Fighting", "D": "Darkness", "M": "Metal",
    "Y": "Fairy", "C": "Colorless", "N": "Dragon",
}

# The 'Type' column wraps most types in curly braces (e.g. '{G}'), but Dragon
# type has no energy symbol in the TCG and shows up as the raw kanji '竜'
# ("dragon") in this dataset instead.
TYPE_LABELS = {f"{{{symbol}}}": name for symbol, name in ENERGY_SYMBOLS.items()}
TYPE_LABELS["竜"] = "Dragon"


def readable_type(type_value: str) -> str:
    """Map a raw Type column value (e.g. '{G}', '竜') to a readable name."""
    if pd.isna(type_value):
        return type_value
    return TYPE_LABELS.get(str(type_value), str(type_value))


def load_card_data(language: str = "EN") -> pd.DataFrame:
    """Load the card dataset for the given language ("EN" or "JP").

    Returns a DataFrame with columns:
    Card ID, Card Name, Expansion, Collection No.,
    Stage (Pokemon)/Type (Energy and Trainer), Rule, Category,
    Previous stage, HP, Type, Weakness, Resistance (Type), Retreat,
    Move Name, Cost, Damage, Effect Explanation
    """
    language = language.upper()
    if language not in {"EN", "JP"}:
        raise ValueError("language must be 'EN' or 'JP'")

    path = DATA_DIR / f"{language}_Card_Data.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find {path}. Download it first with:\n"
            f'  python -m kaggle competitions download '
            f'pokemon-tcg-ai-battle-challenge-strategy -f "{language}_Card_Data.csv" -p data/raw'
        )
    return pd.read_csv(path)


def categorize_cards(df: pd.DataFrame) -> dict:
    """Split a card dataframe into Pokemon / Trainer / Energy subsets.

    Uses the 'Stage (Pokemon)/Type (Energy and Trainer)' column to
    distinguish card kinds, since there's no single clean "card type" column.
    """
    stage_col = "Stage (Pokémon)/Type (Energy and Trainer)"
    is_energy = df[stage_col].astype(str).str.contains("Energy", na=False)
    is_pokemon = df["HP"].notna()
    is_trainer = ~is_energy & ~is_pokemon

    return {
        "pokemon": df[is_pokemon].copy(),
        "trainer": df[is_trainer].copy(),
        "energy": df[is_energy].copy(),
    }


def parse_cost(cost: str) -> dict:
    """Parse a move cost string like '{G}{G}●' into energy counts.

    Curly-brace tokens ({G}, {R}, ...) are specific energy types.
    '●' (filled circle) tokens represent Colorless energy requirements.
    Returns a dict of {energy_name: count}, e.g. {'Grass': 2, 'Colorless': 1}.
    Returns {} for NaN/empty cost (e.g. Abilities with no attack cost).
    """
    if pd.isna(cost):
        return {}

    counts: dict = {}
    for symbol in re.findall(r"\{(\w)\}", str(cost)):
        name = ENERGY_SYMBOLS.get(symbol, symbol)
        counts[name] = counts.get(name, 0) + 1

    colorless_count = str(cost).count("\u25cf")  # '●'
    if colorless_count:
        counts["Colorless"] = counts.get("Colorless", 0) + colorless_count

    return counts


def total_energy_cost(cost: str) -> int:
    """Total number of energy required for a move, regardless of type."""
    return sum(parse_cost(cost).values())


def parse_damage(damage: str) -> int | None:
    """Parse a damage string (e.g. '120', '30x', '90+') into a base int value.

    Modifiers like '×'/'x' (multiplier, e.g. per-coin-flip) or '+' (bonus
    damage from an effect) are stripped since they depend on game state.
    Returns None if the move has no direct damage value (e.g. status/utility moves).
    """
    if pd.isna(damage):
        return None
    match = re.search(r"\d+", str(damage))
    return int(match.group()) if match else None


def build_matchup_matrix(pokemon_df: pd.DataFrame) -> pd.DataFrame:
    """Build a type x type matchup matrix.

    Cell [attacker_type, defender_type] = number of unique defender_type
    Pokemon that are Weak to attacker_type (i.e. attacker_type deals 2x
    damage against them). Types are converted to readable names.
    Useful for picking an archetype type that has broad weakness coverage
    against the rest of the metagame, while checking what it itself is
    weak to.
    """
    df = pokemon_df.drop_duplicates(subset=["Card ID"]).copy()
    df["Type"] = df["Type"].apply(readable_type)
    df["Weakness"] = df["Weakness"].apply(readable_type)

    types = sorted(df["Type"].dropna().unique())
    matrix = pd.DataFrame(0, index=types, columns=types)

    for defender_type, weakness in zip(df["Type"], df["Weakness"]):
        if pd.isna(weakness) or pd.isna(defender_type):
            continue
        if weakness in matrix.index and defender_type in matrix.columns:
            matrix.loc[weakness, defender_type] += 1

    matrix.index.name = "attacker_type (deals 2x to...)"
    matrix.columns.name = "defender_type"
    return matrix


def add_move_metrics(pokemon_df: pd.DataFrame) -> pd.DataFrame:
    """Add parsed Cost/Damage columns for move-level efficiency analysis.

    Adds: energy_cost (int), base_damage (float, NaN if no direct damage),
    damage_per_energy (float, NaN if cost is 0 or damage is missing).
    """
    out = pokemon_df.copy()
    out["energy_cost"] = out["Cost"].apply(total_energy_cost)
    out["base_damage"] = out["Damage"].apply(parse_damage)
    out["damage_per_energy"] = out["base_damage"] / out["energy_cost"].replace(0, pd.NA)
    return out


if __name__ == "__main__":
    en = load_card_data("EN")
    print(f"Loaded {len(en)} EN cards")
    groups = categorize_cards(en)
    for name, subset in groups.items():
        print(f"  {name}: {len(subset)}")
