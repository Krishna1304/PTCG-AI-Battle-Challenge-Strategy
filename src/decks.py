"""Builds the two candidate 60-card DeckLists from real competition card
data, for use with src/simulator.py.

Deck A: Dragon control/tempo
  Dreepy -> Drakloak -> Dragapult ex, + Mega Latias ex (secondary attacker)
Deck B: Fire aggro
  Charcadet -> Ceruledge / Ceruledge ex, + Centiskorch (secondary attacker)

See report/strategy_report_draft.md for the deck-building rationale and
report/simulation_results.md for validation results.

Card selection notes (fixed after the first simulation pass):
- All Pokemon in a deck share a consistent printing, matched to that
  deck's actual energy base (Fire+Psychic for Dragon, Fire for Fire Aggro).
- Haxorus (originally planned as Deck A's secondary attacker) was cut: it
  requires Fighting+Metal energy (a 3rd/4th type the deck doesn't run) AND
  evolves from Fraxure<-Axew, neither of which were in the deck, making it
  permanently unplayable. Replaced with Mega Latias ex, a Basic Pokemon
  that fits the deck's existing Fire+Psychic energy base.
"""

import pandas as pd

from data_loader import load_card_data, categorize_cards, readable_type, parse_damage
from simulator import Move, PokemonCard, DeckList

# Move name -> effect dispatch tag used by simulator.MOVE_EFFECTS.
# Only moves with a real side effect beyond flat damage need an entry.
MOVE_EFFECT_TAGS = {
    "Infernal Slash": "infernal_slash",
    "Abyssal Flames": "abyssal_flames",
    "Raging Amethyst": "raging_amethyst",
    "Billowing Heat Wave": "billowing_heat_wave",
    "Phantom Dive": "phantom_dive",
    "Dragon Pulse": "dragon_pulse_mill",
    "Bring Down the Axe": "bring_down_the_axe",
}

# Move name -> {energy type: count} that must be discarded FROM HAND (not
# attached) to use the move. Lets the agent plan ahead by banking energy
# in hand instead of always attaching it immediately. See Move.hand_cost.
MOVE_HAND_COSTS = {
    "Infernal Slash": {"Fire": 4},
}

# Card Name -> Ability dispatch tag used by simulator.Game._use_abilities.
ABILITY_TAGS = {
    "Drakloak": "recon_directive",
}

STAGE_COL = "Stage (Pok\u00e9mon)/Type (Energy and Trainer)"


def _parse_typed_cost(cost_str) -> dict[str, int]:
    """Parse a raw Cost string like '{R}{P}' or '\u25cf\u25cf' into a typed
    cost dict, e.g. {'Fire': 1, 'Psychic': 1} or {'Colorless': 2}."""
    if pd.isna(cost_str):
        return {}
    from data_loader import ENERGY_SYMBOLS
    cost: dict[str, int] = {}
    s = str(cost_str)
    i = 0
    while i < len(s):
        if s[i] == "{" and i + 2 < len(s) and s[i + 2] == "}":
            symbol = s[i + 1]
            name = ENERGY_SYMBOLS.get(symbol, symbol)
            cost[name] = cost.get(name, 0) + 1
            i += 3
        elif s[i] == "\u25cf":
            cost["Colorless"] = cost.get("Colorless", 0) + 1
            i += 1
        else:
            i += 1
    return cost


def _build_pokemon_card(pokemon_df: pd.DataFrame, card_id: int) -> PokemonCard:
    rows = pokemon_df[pokemon_df["Card ID"] == card_id]
    if rows.empty:
        raise KeyError(f"Card ID {card_id} not found")
    first = rows.iloc[0]

    moves = []
    for _, row in rows.iterrows():
        move_name = row["Move Name"]
        if pd.isna(move_name) or str(move_name).startswith(("[Ability]", "[Tera]")):
            continue
        cost = _parse_typed_cost(row["Cost"])
        damage = parse_damage(row["Damage"]) or 0
        effect_tag = MOVE_EFFECT_TAGS.get(str(move_name))
        hand_cost = MOVE_HAND_COSTS.get(str(move_name), {})
        moves.append(Move(name=str(move_name), cost=cost, damage=damage, effect=effect_tag, hand_cost=hand_cost))

    stage_raw = str(first[STAGE_COL])
    stage = (
        "Basic" if "Basic" in stage_raw else
        "Stage 2" if "Stage 2" in stage_raw else
        "Stage 1" if "Stage 1" in stage_raw else stage_raw
    )

    name = str(first["Card Name"])
    weakness = readable_type(first["Weakness"]) if pd.notna(first["Weakness"]) else None
    resistance = readable_type(first["Resistance (Type)"]) if pd.notna(first["Resistance (Type)"]) else None

    # [Tera] passive: prevents all damage to this Pokemon while Benched.
    # Detected from the raw move text rather than hardcoded by name, so
    # it generalizes to any future card built with this helper.
    has_tera_bench_immunity = any(
        str(row["Move Name"]) == "[Tera]"
        and "prevent all damage" in str(row["Effect Explanation"]).lower()
        and "bench" in str(row["Effect Explanation"]).lower()
        for _, row in rows.iterrows()
    )

    return PokemonCard(
        card_id=card_id,
        name=name,
        stage=stage,
        hp=int(first["HP"]),
        ptype=readable_type(first["Type"]),
        weakness=weakness,
        resistance=resistance,
        retreat_cost=int(first["Retreat"]) if pd.notna(first["Retreat"]) else 0,
        evolves_from=str(first["Previous stage"]) if pd.notna(first["Previous stage"]) else None,
        moves=tuple(moves),
        ability=ABILITY_TAGS.get(name),
        is_ex="ex" in name.lower().split(),
        tera_bench_immune=has_tera_bench_immunity,
    )


def build_dragon_deck() -> DeckList:
    """Dragon Control. Energy base: Fire + Psychic (matches Dreepy/
    Drakloak/Dragapult ex/Mega Latias ex's actual printed costs)."""
    df = load_card_data("EN")
    pokemon = categorize_cards(df)["pokemon"]

    dreepy = _build_pokemon_card(pokemon, 119)          # TWM 128, Basic, 70 HP, 1 retreat
    drakloak = _build_pokemon_card(pokemon, 120)        # TWM 129, Stage 1, 90 HP, 1 retreat
    dragapult_ex = _build_pokemon_card(pokemon, 121)    # TWM 130, Stage 2, 320 HP, 1 retreat
    mega_latias_ex = _build_pokemon_card(pokemon, 754)  # MEG 100, Basic, 280 HP, 1 retreat

    # 14 pokemon + 27 energy (14 Fire / 13 Psychic) + 19 trainer = 60
    #
    # Note: an earlier version of this deck ran 25 trainer cards (heavy on
    # Billy & O'Nare / Ultra Ball / Boxed Order / Energy Search), which
    # simulation revealed decked the deck out (0 cards left to draw) in
    # roughly 1 in 10 games -- see report/simulation_results.md. The
    # card-advantage engines below are trimmed to a level a real deck
    # builder would actually run, matching Fire Aggro's density more
    # closely (still uses every named Trainer card, just fewer copies).
    return DeckList(
        name="Dragon Control",
        pokemon_counts={
            dreepy: 4,
            drakloak: 3,
            dragapult_ex: 3,
            mega_latias_ex: 4,
        },
        energy_counts={"Fire": 14, "Psychic": 13},
        trainer_counts={
            "Rare Candy": 4,
            "Buddy-Buddy Poffin": 4,
            "Billy & O'Nare": 3,
            "Ultra Ball": 3,
            "Boxed Order": 2,
            "Energy Search": 2,
            "Energy Search Pro": 1,  # ACE SPEC, max 1 per deck
        },
    )


def build_fire_deck() -> DeckList:
    """Fire Aggro. Energy base: Fire only (matches Charcadet/Ceruledge/
    Ceruledge ex/Centiskorch's actual printed costs)."""
    df = load_card_data("EN")
    pokemon = categorize_cards(df)["pokemon"]

    charcadet = _build_pokemon_card(pokemon, 796)     # PFL 19, Basic, 70 HP, 2 retreat
    ceruledge = _build_pokemon_card(pokemon, 797)     # PFL 20, Stage 1, 140 HP, 2 retreat
    ceruledge_ex = _build_pokemon_card(pokemon, 320)  # SSP 36, Stage 1, 270 HP, 2 retreat
    sizzlipede = _build_pokemon_card(pokemon, 717)    # MEG 29, Basic, 80 HP, 2 retreat
    centiskorch = _build_pokemon_card(pokemon, 934)   # SSP 28, Stage 1, 130 HP, 2 retreat

    # 14 pokemon + 25 energy (all Fire) + 21 trainer = 60
    #
    # Note: an earlier version of this deck omitted Sizzlipede, the Basic
    # that Centiskorch evolves from. Since only Basic Pokemon can be
    # played from hand, Centiskorch (a Stage 1) could NEVER legally enter
    # play without it -- 3 permanently-dead card slots. Fixed by adding
    # Sizzlipede, matching the pattern already used for Charcadet/Ceruledge.
    return DeckList(
        name="Fire Aggro",
        pokemon_counts={
            charcadet: 4,
            ceruledge: 3,
            ceruledge_ex: 2,
            sizzlipede: 2,
            centiskorch: 3,
        },
        energy_counts={"Fire": 25},
        trainer_counts={
            "Buddy-Buddy Poffin": 4,
            "Billy & O'Nare": 4,
            "Ultra Ball": 4,
            "Boxed Order": 4,
            "Energy Search": 4,
            "Energy Search Pro": 1,  # ACE SPEC, max 1 per deck
        },
    )


if __name__ == "__main__":
    for build in (build_dragon_deck, build_fire_deck):
        deck = build()
        pokemon_total = sum(deck.pokemon_counts.values())
        energy_total = sum(deck.energy_counts.values())
        trainer_total = sum(deck.trainer_counts.values())
        total = pokemon_total + energy_total + trainer_total
        print(f"{deck.name}: {total} cards "
              f"({pokemon_total} pokemon, {energy_total} energy, {trainer_total} trainer)")
        cards = deck.build_deck()  # validates legality (60 cards, 4-copy, ACE SPEC limits)
        print(f"  build_deck() OK, {len(cards)} cards, legality checks passed")
