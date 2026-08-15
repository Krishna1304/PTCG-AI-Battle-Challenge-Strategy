# Strategic Design of a Pokémon TCG AI Agent

**Pokémon TCG AI Battle Challenge — Strategy Track**
Kaggle competition: `pokemon-tcg-ai-battle-challenge-strategy`

## Executive Summary

This report presents a data-driven approach to designing a Pokémon TCG
playing agent: analyze the full card pool to identify statistically
strong archetypes, build candidate decks around them, and validate
those choices by simulating actual gameplay against real card
mechanics rather than relying on summary statistics alone.

**Recommendation: a Dragon-type control deck anchored on Dragapult ex
and Mega Latias ex.** In 1,000 simulated games against a Fire-type
aggro alternative, this deck won 85.9% of matches. The analysis
behind this recommendation, and the reasoning for why simulation
changed our initial read of the data, are detailed below.

The most important finding of this project is methodological: **a
card's raw stats (damage-per-energy, HP, weakness) are not a reliable
predictor of in-game performance.** Two attackers with near-identical
"1 energy for ~220 damage" profiles can behave completely differently
once their full printed text — hidden resource costs, self-damage
clauses, evolution requirements — is taken into account. Section 5
documents this directly.

---

## 1. Data and Methodology

The competition card pool (`EN_Card_Data.csv`, 2,022 rows / 1,061
unique cards) was loaded and split into Pokémon, Trainer, and Energy
cards. For each Pokémon attack, energy cost and damage were parsed
into structured values, enabling a damage-per-energy efficiency metric
across the entire pool. A type-vs-type matchup matrix was also built
from the Weakness column to identify which types are best positioned
to exploit the rest of the metagame, and which are safest from being
exploited themselves.

Two archetypes were shortlisted from this analysis and built into
full, legal 60-card decklists using real card names, stats, and
printed move text. A custom local battle simulator was then built to
play these decks against each other under a shared rule-based agent,
producing a statistically robust win-rate comparison (1,000 games per
matchup) rather than a one-off playtest.

Full exploratory analysis, code, and intermediate results are in
`notebooks/01_card_data_eda.ipynb` and `src/`.

## 2. Card Pool Analysis

Damage-per-energy efficiency and weakness exposure by type:

| Type | Attackers | Avg HP | Avg dmg/energy | Weak to |
|---|---|---|---|---|
| **Dragon** | 31 | 148.3 | **41.7** | *none* |
| Fighting | 101 | 128.8 | 35.5 | Grass, Psychic |
| Metal | 57 | 139.6 | 34.7 | Fire |
| Lightning | 60 | 138.7 | 34.2 | Fighting |
| Fire | 83 | 137.1 | 33.9 | Water |
| Water | 125 | 128.1 | 31.3 | Lightning |
| Grass | 122 | 114.8 | 30.2 | Fire |
| Darkness | 90 | 130.9 | 30.1 | Fighting |
| Psychic | 116 | 114.7 | 28.1 | Darkness |

Two findings stood out:

- **Dragon-type Pokémon have no assigned weakness at all** in this
  game — the type simply doesn't appear in the Weakness column. Combined
  with the best average damage-per-energy in the pool, this makes
  Dragon an attractive foundation: it cannot be punished by the game's
  primary damage-multiplier mechanic.
- **Fire is the single best "predator" type**, with 220 unique Pokémon
  across the pool weak to it — more than any other type. It's also
  strong on raw efficiency (5th of 9 types). Its natural counter is
  Water (Fire itself is weak to Water, and 99 Water-type attackers
  exist in the pool).

These two signals produced our two candidate archetypes: a Dragon
control deck built around efficiency and weakness-immunity, and a Fire
aggro deck built around a single highly efficient attacker plus broad
matchup coverage.

## 3. Candidate Decks

### Deck A — Dragon Control (recommended)

Evolution line Dreepy → Drakloak → Dragapult ex, supported by Mega
Latias ex as a second attacking threat that shares the same Fire +
Psychic energy base.

| Count | Card | Role |
|---|---|---|
| 4 | Dreepy | Basic attacker / early game |
| 3 | Drakloak | Mid-evolution; Recon Directive Ability filters draws |
| 3 | Dragapult ex | Primary attacker — Phantom Dive: 200 dmg + 60 bench dmg |
| 4 | Mega Latias ex | Secondary attacker — Illusory Impulse: 300 dmg, no weakness |
| 4 | Rare Candy | Skips straight to Dragapult ex, bypassing Drakloak |
| 4 | Buddy-Buddy Poffin | Fetches low-HP Basics (Dreepy) to the bench |
| 3 | Billy & O'Nare | Draw power |
| 3 | Ultra Ball | Flexible Pokémon search |
| 2 | Boxed Order | Item search (used only when no attack is otherwise available) |
| 2 | Energy Search | Basic Energy search |
| 1 | Energy Search Pro | ACE SPEC — searches multiple Energy types at once |
| 27 | Basic Energy (14 Fire / 13 Psychic) | Matches the deck's Fire+Psychic cost |

14 Pokémon + 19 Trainer + 27 Energy = 60 cards. See
`src/decks.py::build_dragon_deck()` for the exact, validated list.

### Deck B — Fire Aggro (comparison baseline)

Evolution line Charcadet → Ceruledge / Ceruledge ex, supported by
Sizzlipede → Centiskorch.

| Count | Card | Role |
|---|---|---|
| 4 | Charcadet | Basic attacker |
| 3 | Ceruledge | Primary attacker — Infernal Slash: 220 dmg for 1 Energy attached (plus a hidden hand cost, see Section 5) |
| 2 | Ceruledge ex | Bulkier alternate attacker — Abyssal Flames scales with discard pile |
| 2 | Sizzlipede | Basic attacker |
| 3 | Centiskorch | Secondary attacker — Billowing Heat Wave: 130 dmg, also hits own bench |
| 4 | Buddy-Buddy Poffin | Basic search |
| 4 | Billy & O'Nare | Draw power |
| 4 | Ultra Ball | Flexible Pokémon search |
| 4 | Boxed Order | Item search |
| 4 | Energy Search | Basic Energy search |
| 1 | Energy Search Pro | ACE SPEC |
| 25 | Basic Fire Energy | Matches the deck's all-Fire cost |

14 Pokémon + 21 Trainer + 25 Energy = 60 cards. See
`src/decks.py::build_fire_deck()`.

## 4. Simulation Methodology

A rule-based agent plays both decks identically:

1. Draw for turn, resolve any Ability (e.g. Drakloak's Recon Directive)
2. Play Supporter (max 1/turn) and Item cards while useful
3. Bench any Basic Pokémon in hand
4. Evolve where legal (not on turn 1, not the turn a Pokémon entered play)
5. Attach one Energy, prioritizing whichever Pokémon+type combination
   gets closest to affording its cheapest attack — while banking Energy
   in hand instead of attaching it if a move needs cards discarded from
   hand rather than attached (see Section 5)
6. Retreat proactively if the active Pokémon is critically low on HP
   and a healthier bench Pokémon is available
7. Attack with the best affordable move

The simulator models the decks' actual printed card text rather than
generic placeholders: typed Energy costs, named Trainer card effects,
per-move side effects (bench damage, self-discard costs, hand-discard
requirements), Weakness (×2) / Resistance (−20) applied in the official
order with bench damage correctly exempt from both, and official prize
rules (1 prize per regular knockout, 2 for "ex", 3 for "Mega ___ ex").
20 automated tests (`src/test_simulator.py`) verify these rules
directly, including regression tests for two deckbuilding bugs found
during development (see Section 6).

## 5. Simulation Results

**1,000 games, Dragon Control vs. Fire Aggro, starting player
alternated to cancel out first-move advantage:**

| Deck | Wins | Win rate |
|---|---|---|
| **Dragon Control** | 859 | **85.9%** |
| Fire Aggro | 141 | 14.1% |

Average game length: 15.0 turns. Result held consistently across
independent 500-game and 1,000-game runs (87.0% / 85.9%).

Tracing the mechanism behind these losses directly in the simulation
logs revealed two concrete, card-text-level causes — not just "Dragon
has better stats":

**Ceruledge's headline attack is far less reliable than its stat line
suggests.** Infernal Slash is printed as "1 Fire Energy → 220 damage,"
but its full text requires *discarding 4 additional Basic Fire Energy
cards from hand*. Across simulated games, this attack only landed on
roughly 1 in 5 attempts, because accumulating 4 spare Energy cards in
hand competes directly with the normal need to attach Energy to the
rest of the board every turn. A stats-only read of this card — the
kind that would appear in any simple efficiency table — misses this
entirely.

**Fire Aggro's Basic Pokémon count is thin.** With only 6 Basic
Pokémon in 60 cards (10%) and a fragile primary attacker (Charcadet,
70 HP), the deck is exposed to running out of Pokémon outright: 68% of
Dragon's wins came specifically from Fire Aggro's active Pokémon being
knocked out with an empty bench, not from losing a slower damage race.

Dragon Control's advantages — no weakness exposure, and a shorter,
more resilient path to its main attacker — compound directly into
this result.

## 6. Agent Design Implications

Beyond deck selection, this analysis surfaces concrete rules an
AI agent should follow regardless of which deck it plays:

- **Energy banking.** An agent that always attaches Energy the instant
  it's drawn will never be able to use cards with a hand-discard cost
  like Infernal Slash. The agent must recognize when a move's
  *attached* cost is already satisfied but a *hand* cost isn't, and
  hold matching cards back instead.
- **Board presence as a survival metric, not just a damage race.** An
  agent should track its own Basic Pokémon count as a standing risk
  factor — running low on bench backups is a loss condition on its own,
  independent of the HP totals on the board.
- **Bench-targeting moves cut both ways.** Some attacks damage the
  *opponent's* bench (a way to pick off weak backups early); others
  damage the *user's own* bench (a real cost to weigh against their
  raw damage output). An agent needs to evaluate both directions before
  firing these moves.
- **Weakness immunity is a durable strategic asset**, not just a minor
  efficiency bonus — it removes an entire category of matchup risk
  that persists across every game, not just specific pairings.

## 7. Limitations

This simulation is a simplified model, not a full implementation of
tournament-legal Pokémon TCG. It does not model: Special Conditions
(Poisoned, Asleep, Confused, Paralyzed), Stadium cards, most Pokémon
Tools, or Abilities beyond the one used in these two decks. It
compares exactly two decks rather than a full metagame. The rule-based
agent is a deliberately simple heuristic; a more sophisticated policy
(e.g. one that sequences Energy attachment even more precisely to
maximize Infernal Slash's landing rate) could shift these numbers.
None of these gaps affect the core methodological finding — that
printed card text can reverse conclusions drawn from summary stats —
which is independent of how complete the rule coverage is.

## 8. Reproducibility

All code is included in this submission under `src/`:

- `src/data_loader.py` — loads and parses the raw card dataset
- `src/decks.py` — builds both decklists from real card data
- `src/simulator.py` — the battle engine and all card-specific logic
- `src/run_simulations.py` — runs a batch and reports win rates
- `src/test_simulator.py` — 20 automated tests covering game rules

To reproduce the headline result:

```powershell
python -m pip install -r requirements.txt
python -m pytest src/test_simulator.py -v
python src/run_simulations.py 1000
```

Full exploratory data analysis is in `notebooks/01_card_data_eda.ipynb`.
