"""Regression tests for the local Pokemon TCG battle simulator.

Run with: python -m pytest test_simulator.py -v   (from src/)
or:       python test_simulator.py                (runs as a plain script)
"""

from decks import build_dragon_deck, build_fire_deck
from simulator import (
    DeckList, Game, InPlayPokemon, Move, PokemonCard,
    can_pay_cost, NON_POKEMON_ENERGY_PREFIX,
)


def make_card(name, stage, hp, ptype, weakness=None, resistance=None, retreat=1,
              evolves_from=None, moves=(), ability=None, card_id=None):
    return PokemonCard(
        card_id=card_id if card_id is not None else abs(hash(name)) % 100000,
        name=name, stage=stage, hp=hp, ptype=ptype, weakness=weakness,
        resistance=resistance, retreat_cost=retreat, evolves_from=evolves_from,
        moves=tuple(moves), ability=ability, is_ex="ex" in name.lower().split(),
    )


def simple_deck(name, pokemon_counts, energy_counts, filler_trainer_count=0):
    """Build a legal 60-card deck for test purposes. Uses Energy Search
    (a real, harmless card already registered in TRAINER_CARDS) as filler
    up to 4 copies, then pads remaining slots with extra Basic Energy."""
    pokemon_total = sum(pokemon_counts.values())
    energy_total = sum(energy_counts.values())
    remaining = 60 - pokemon_total - energy_total
    trainer_counts = {}
    if remaining > 0:
        filler = min(4, remaining)
        trainer_counts["Energy Search"] = filler
        remaining -= filler
    if remaining > 0:
        # pad with more of the first energy type to hit exactly 60
        first_type = next(iter(energy_counts))
        energy_counts = dict(energy_counts)
        energy_counts[first_type] += remaining
    return DeckList(name, pokemon_counts, energy_counts, trainer_counts)


def energy_token(etype: str) -> str:
    return f"{NON_POKEMON_ENERGY_PREFIX}{etype}"


# ---------------------------------------------------------------------------
# Deck-construction legality
# ---------------------------------------------------------------------------

def test_real_decks_are_exactly_60_cards_and_legal():
    for build in (build_dragon_deck, build_fire_deck):
        deck = build()
        cards = deck.build_deck()  # raises if illegal
        assert len(cards) == 60, f"{deck.name} has {len(cards)} cards, expected 60"


def test_deck_rejects_more_than_4_copies():
    card = make_card("Solo", "Basic", 100, "Fire", moves=[Move("Hit", {"Colorless": 1}, 10)])
    deck = DeckList("Illegal", {card: 5}, {"Fire": 55}, {})
    try:
        deck.build_deck()
        assert False, "Expected ValueError for 5 copies of a named card"
    except ValueError as e:
        assert "4-copy" in str(e)


def test_deck_rejects_more_than_1_ace_spec():
    card = make_card("Solo", "Basic", 100, "Fire", moves=[Move("Hit", {"Colorless": 1}, 10)])
    deck = DeckList("Illegal", {card: 4}, {"Fire": 52}, {"Energy Search Pro": 2, "Energy Search": 2})
    try:
        deck.build_deck()
        assert False, "Expected ValueError for 2 ACE SPEC cards"
    except ValueError as e:
        assert "ACE SPEC" in str(e)


def test_real_decks_have_at_least_one_basic():
    for build in (build_dragon_deck, build_fire_deck):
        deck = build()
        assert deck.basics(), f"{deck.name} has no Basic Pokemon"


def test_real_decks_have_complete_evolution_chains():
    """Regression test: every non-Basic Pokemon's full evolution chain
    (down to a Basic) must be present in the SAME deck, or that card can
    never legally enter play. Caught twice: Haxorus (needs Fraxure<-Axew,
    neither present) and Centiskorch (needs Sizzlipede, not present)."""
    for build in (build_dragon_deck, build_fire_deck):
        deck = build()
        names_in_deck = {c.name for c in deck.pokemon_counts}
        for card in deck.pokemon_counts:
            current = card
            while current.evolves_from is not None:
                assert current.evolves_from in names_in_deck, (
                    f"{deck.name}: {card.name} needs '{current.evolves_from}' "
                    f"in the deck to ever be playable, but it's missing"
                )
                current = next(c for c in deck.pokemon_counts if c.name == current.evolves_from)


# ---------------------------------------------------------------------------
# Typed energy cost checking
# ---------------------------------------------------------------------------

def test_can_pay_cost_specific_type_required():
    assert can_pay_cost({"Fire": 1}, {"Fire": 1}) is True
    assert can_pay_cost({"Psychic": 1}, {"Fire": 1}) is False


def test_can_pay_cost_colorless_accepts_any_type():
    assert can_pay_cost({"Water": 2}, {"Colorless": 2}) is True
    assert can_pay_cost({"Water": 1}, {"Colorless": 2}) is False


def test_can_pay_cost_mixed_specific_and_colorless():
    # {Fire:1, Psychic:1} cost, paid with 1 Fire + 1 anything
    assert can_pay_cost({"Fire": 1, "Grass": 1}, {"Fire": 1, "Colorless": 1}) is True
    # missing the required specific Fire type entirely
    assert can_pay_cost({"Grass": 2}, {"Fire": 1, "Colorless": 1}) is False


# ---------------------------------------------------------------------------
# Evolution timing
# ---------------------------------------------------------------------------

def test_cannot_evolve_on_first_turn():
    """Regression test for a bug where a Basic could evolve into a huge
    attacker and OHKO the opponent's Basic on turn 1."""
    basic = make_card("Basic", "Basic", 70, "Fire", weakness="Water", retreat=2,
                       moves=[Move("Tackle", {"Colorless": 1}, 10)])
    evo = make_card("Evo", "Stage 1", 140, "Fire", weakness="Water", retreat=2,
                     evolves_from="Basic", moves=[Move("Big Hit", {"Colorless": 1}, 220)])
    opp_basic = make_card("OppBasic", "Basic", 70, "Dragon", retreat=1,
                           moves=[Move("Bite", {"Colorless": 1}, 10)])

    deck_a = simple_deck("A", {basic: 4, evo: 4}, {"Colorless": 20})
    deck_b = simple_deck("B", {opp_basic: 4}, {"Colorless": 20})

    g = Game(deck_a, deck_b, seed=0)
    for p in g.players:
        g._place_opening_basic(p)

    a, b = g.players
    g._take_turn(a, b)

    assert a.active.card.name == "Basic", "Should not have evolved on turn 1"
    assert b.active is not None, "Opponent's basic should have survived turn 1"
    assert b.active.damage_taken < 70, "Damage dealt should be a normal Basic attack, not an OHKO"


# ---------------------------------------------------------------------------
# Weakness / Resistance
# ---------------------------------------------------------------------------

def test_weakness_doubles_damage():
    attacker = make_card("Attacker", "Basic", 100, "Fire",
                          moves=[Move("Ember", {"Colorless": 1}, 50)])
    weak_defender = make_card("WeakDefender", "Basic", 200, "Grass", weakness="Fire",
                               moves=[Move("Vine", {"Colorless": 1}, 10)])

    deck_a = simple_deck("A", {attacker: 4}, {"Colorless": 20})
    deck_b = simple_deck("B", {weak_defender: 4}, {"Colorless": 20})

    g = Game(deck_a, deck_b, seed=2)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.active.attached_energy = {"Colorless": 1}
    g._attack(a, b)

    assert b.active.damage_taken == 100, f"Expected 50*2=100 weakness damage, got {b.active.damage_taken}"


def test_resistance_reduces_damage_by_20():
    attacker = make_card("Attacker", "Basic", 100, "Fire",
                          moves=[Move("Ember", {"Colorless": 1}, 50)])
    resistant_defender = make_card("ResistDefender", "Basic", 200, "Grass", resistance="Fire",
                                    moves=[Move("Vine", {"Colorless": 1}, 10)])

    deck_a = simple_deck("A", {attacker: 4}, {"Colorless": 20})
    deck_b = simple_deck("B", {resistant_defender: 4}, {"Colorless": 20})

    g = Game(deck_a, deck_b, seed=2)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.active.attached_energy = {"Colorless": 1}
    g._attack(a, b)

    assert b.active.damage_taken == 30, f"Expected 50-20=30 resisted damage, got {b.active.damage_taken}"


def test_weakness_and_resistance_do_not_apply_to_bench_damage():
    """Official rule: bench damage (e.g. from Phantom Dive) ignores
    Weakness and Resistance entirely."""
    attacker = make_card("Attacker", "Basic", 100, "Fire",
                          moves=[Move("Splash", {"Colorless": 1}, 0, effect=None)])
    bench_mon = make_card("BenchMon", "Basic", 100, "Grass", weakness="Fire",
                           moves=[Move("Vine", {"Colorless": 1}, 10)])

    deck_a = simple_deck("A", {attacker: 4}, {"Colorless": 20})
    deck_b = simple_deck("B", {bench_mon: 4}, {"Colorless": 20})
    g = Game(deck_a, deck_b, seed=1)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    b.bench = [InPlayPokemon(card=bench_mon, entered_play_on_turn=0)]

    # Deal 30 bench damage directly, as Phantom Dive's handler would,
    # and confirm no weakness doubling occurred despite BenchMon being
    # weak to Fire.
    g._deal_damage(b, b.bench[0], 30, attacker=a)
    assert b.bench[0].damage_taken == 30, "Bench damage should not be weakness-doubled"


# ---------------------------------------------------------------------------
# Named move effects (the real mechanics a pure stats table misses)
# ---------------------------------------------------------------------------

def test_infernal_slash_requires_4_fire_energy_in_hand():
    """Ceruledge's Infernal Slash needs to discard 4 Basic Fire Energy
    from HAND (not the battlefield) or it does 0 damage."""
    from decks import build_fire_deck
    deck = build_fire_deck()
    ceruledge = deck.card_by_name("Ceruledge")

    # 300 HP so a full 220-damage hit doesn't KO it -- we want to check
    # the exact damage_taken value, which requires .active to survive.
    opp = make_card("Opp", "Basic", 300, "Water", moves=[Move("Splash", {"Colorless": 1}, 10)])
    deck_b = simple_deck("B", {opp: 4}, {"Colorless": 20})

    g = Game(deck, deck_b, seed=5)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.active.card = ceruledge
    a.active.attached_energy = {"Fire": 1}

    # Case 1: not enough Fire Energy in hand -> attack does nothing
    a.hand = [c for c in a.hand if not c.startswith(NON_POKEMON_ENERGY_PREFIX)]
    g._attack(a, b)
    assert b.active.damage_taken == 0, "Infernal Slash should whiff without 4 Fire Energy in hand"

    # Case 2: exactly 4 Fire Energy in hand -> full damage, energy discarded
    a.hand = [energy_token("Fire")] * 4
    g._attack(a, b)
    assert b.active.damage_taken == 220, f"Expected 220 damage, got {b.active.damage_taken}"
    assert energy_token("Fire") not in a.hand, "The 4 Fire Energy should have been discarded from hand"


def test_billowing_heat_wave_damages_own_bench():
    """Centiskorch's Billowing Heat Wave deals 30 to the user's OWN bench
    -- a real drawback missed by a flat damage-per-energy stat."""
    from decks import build_fire_deck
    deck = build_fire_deck()
    centiskorch = deck.card_by_name("Centiskorch")
    ally = make_card("Ally", "Basic", 100, "Fire", moves=[Move("Hit", {"Colorless": 1}, 10)])
    opp = make_card("Opp", "Basic", 200, "Water", moves=[Move("Splash", {"Colorless": 1}, 10)])

    deck_b = simple_deck("B", {opp: 4}, {"Colorless": 20})
    g = Game(deck, deck_b, seed=6)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.active.card = centiskorch
    a.active.attached_energy = {"Fire": 1}
    a.bench = [InPlayPokemon(card=ally, entered_play_on_turn=0)]

    g._attack(a, b)
    assert a.bench[0].damage_taken == 30, "Billowing Heat Wave should splash 30 damage onto the user's own bench"


def test_phantom_dive_damages_opponent_bench():
    from decks import build_dragon_deck
    deck = build_dragon_deck()
    dragapult_ex = deck.card_by_name("Dragapult ex")
    enemy_bench_mon = make_card("EnemyBench", "Basic", 100, "Water", moves=[Move("Splash", {"Colorless": 1}, 10)])
    # 300 HP so the 200-damage main hit doesn't KO it (a KO would promote
    # the bench Pokemon to active, clearing b.bench -- we want to check
    # bench-splash and main damage independently here).
    opp_active = make_card("OppActive", "Basic", 300, "Water", moves=[Move("Splash", {"Colorless": 1}, 10)])

    dreepy = make_card("StandInBasic", "Basic", 70, "Dragon",
                        moves=[Move("Bite", {"Colorless": 1}, 10)])
    deck_a = simple_deck("A", {dreepy: 4}, {"Fire": 10, "Psychic": 10})
    deck_b = simple_deck("B", {opp_active: 2, enemy_bench_mon: 2}, {"Colorless": 20})
    g = Game(deck_a, deck_b, seed=7)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.active.card = dragapult_ex  # manually place Dragapult ex, bypassing evolution setup
    a.active.attached_energy = {"Fire": 1, "Psychic": 1}
    b.active.card = opp_active
    b.active.damage_taken = 0
    b.bench = [InPlayPokemon(card=enemy_bench_mon, entered_play_on_turn=0)]

    g._attack(a, b)
    assert b.bench[0].damage_taken == 60, f"Phantom Dive should deal 60 bench damage, got {b.bench[0].damage_taken}"
    assert b.active.damage_taken == 200, "Phantom Dive's main target should also take its 200 base damage"


# ---------------------------------------------------------------------------
# Prizes / win conditions
# ---------------------------------------------------------------------------

def test_prizes_taken_means_that_player_wins():
    attacker = make_card("Attacker", "Basic", 100, "Fire",
                          moves=[Move("Ember", {"Colorless": 1}, 500)])
    fragile = make_card("Fragile", "Basic", 10, "Grass",
                         moves=[Move("Vine", {"Colorless": 1}, 10)])

    deck_a = simple_deck("A", {attacker: 4}, {"Colorless": 20})
    deck_b = simple_deck("B", {fragile: 4}, {"Colorless": 20})

    g = Game(deck_a, deck_b, seed=3)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.active.attached_energy = {"Colorless": 1}
    g._attack(a, b)  # A knocks out B's active (regular Pokemon: 1 prize)
    assert a.prizes_remaining == 5, f"Attacker should have taken 1 prize, prizes_remaining={a.prizes_remaining}"

    a.prizes_remaining = 0
    result = g._check_game_over(a, b)
    assert result is not None and result[0] is a, "Player with prizes_remaining==0 should be declared WINNER"
    assert result[1] == "prizes_taken"


def test_ex_pokemon_gives_up_2_prizes_and_mega_ex_gives_up_3():
    attacker = make_card("Attacker", "Basic", 100, "Fire", moves=[Move("Ember", {"Colorless": 1}, 500)])

    for name, expected_prizes in [("Foo ex", 2), ("Mega Foo ex", 3), ("Plain", 1)]:
        target = make_card(name, "Basic", 10, "Grass", moves=[Move("Vine", {"Colorless": 1}, 10)])
        deck_a = simple_deck("A", {attacker: 4}, {"Colorless": 20})
        deck_b = simple_deck("B", {target: 4}, {"Colorless": 20})
        g = Game(deck_a, deck_b, seed=8)
        for p in g.players:
            g._place_opening_basic(p)
        a, b = g.players
        a.active.attached_energy = {"Colorless": 1}
        g._attack(a, b)
        taken = 6 - a.prizes_remaining
        assert taken == expected_prizes, f"{name}: expected {expected_prizes} prizes taken, got {taken}"


def test_self_knockout_still_awards_prize_to_opponent():
    """Real rule: however a KO happens (including self-inflicted, e.g.
    from Billowing Heat Wave splash), the OPPONENT of the player who lost
    the Pokemon takes the prize."""
    attacker = make_card("Attacker", "Basic", 100, "Fire", moves=[Move("Ember", {"Colorless": 1}, 10)])
    fragile_ally = make_card("FragileAlly", "Basic", 20, "Fire", moves=[Move("Vine", {"Colorless": 1}, 10)])
    opp = make_card("Opp", "Basic", 100, "Water", moves=[Move("Splash", {"Colorless": 1}, 10)])

    deck_a = simple_deck("A", {attacker: 4}, {"Colorless": 20})
    deck_b = simple_deck("B", {opp: 4}, {"Colorless": 20})
    g = Game(deck_a, deck_b, seed=9)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.bench = [InPlayPokemon(card=fragile_ally, entered_play_on_turn=0)]

    before = b.prizes_remaining
    g._deal_damage(a, a.bench[0], 30, attacker=b)  # simulate a's own bench mon getting KO'd
    assert a.bench == [] or a.bench[0].is_knocked_out is False  # removed from bench once KO'd
    assert b.prizes_remaining == before - 1, "Opponent should take a prize even for a self-inflicted KO"


# ---------------------------------------------------------------------------
# Full-game smoke tests
# ---------------------------------------------------------------------------

def test_full_game_completes_without_crashing():
    for seed in range(30):
        dragon = build_dragon_deck()
        fire = build_fire_deck()
        result = Game(dragon, fire, seed=seed).play()
        assert result["winner"] in ("A", "B", "draw")
        assert 1 <= result["turns"] <= Game.MAX_TURNS


def test_retreat_swaps_active_with_healthier_bench_pokemon():
    hurt_active = make_card("Hurt", "Basic", 100, "Fire", retreat=0,
                             moves=[Move("Ember", {"Colorless": 1}, 10)])
    healthy_bench = make_card("Healthy", "Basic", 100, "Fire", retreat=0,
                               moves=[Move("Ember", {"Colorless": 1}, 10)])
    opp = make_card("Opp", "Basic", 100, "Water", retreat=1,
                     moves=[Move("Splash", {"Colorless": 1}, 10)])

    deck_a = simple_deck("A", {hurt_active: 2, healthy_bench: 2}, {"Colorless": 20})
    deck_b = simple_deck("B", {opp: 4}, {"Colorless": 20})

    g = Game(deck_a, deck_b, seed=4)
    for p in g.players:
        g._place_opening_basic(p)
    a, b = g.players
    a.active.card = hurt_active
    a.active.damage_taken = 80  # 20/100 HP remaining, well under 35% threshold
    a.bench = [InPlayPokemon(card=healthy_bench, entered_play_on_turn=0)]

    g._retreat_if_favorable(a, b)
    assert a.active.card.name == "Healthy", "Should have retreated into the healthier bench Pokemon"


if __name__ == "__main__":
    tests = [
        test_real_decks_are_exactly_60_cards_and_legal,
        test_deck_rejects_more_than_4_copies,
        test_deck_rejects_more_than_1_ace_spec,
        test_real_decks_have_at_least_one_basic,
        test_real_decks_have_complete_evolution_chains,
        test_can_pay_cost_specific_type_required,
        test_can_pay_cost_colorless_accepts_any_type,
        test_can_pay_cost_mixed_specific_and_colorless,
        test_cannot_evolve_on_first_turn,
        test_weakness_doubles_damage,
        test_resistance_reduces_damage_by_20,
        test_weakness_and_resistance_do_not_apply_to_bench_damage,
        test_infernal_slash_requires_4_fire_energy_in_hand,
        test_billowing_heat_wave_damages_own_bench,
        test_phantom_dive_damages_opponent_bench,
        test_prizes_taken_means_that_player_wins,
        test_ex_pokemon_gives_up_2_prizes_and_mega_ex_gives_up_3,
        test_self_knockout_still_awards_prize_to_opponent,
        test_full_game_completes_without_crashing,
        test_retreat_swaps_active_with_healthier_bench_pokemon,
    ]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)
