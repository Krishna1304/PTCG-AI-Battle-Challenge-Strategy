"""
Local Pokemon TCG battle simulator.

This is INDEPENDENT local test infrastructure used to validate strategy
claims for the Kaggle "PTCG AI Battle Challenge — Strategy" track report.
It is not a submission to Kaggle's Simulation track (which closed to new
entrants on 2026-08-09).

This version models real card mechanics as closely as practical using the
actual card text in data/raw/EN_Card_Data.csv, rather than generic
abstractions:

- Energy costs are TYPED (e.g. Dragapult ex needs {Fire}{Psychic}, not
  "any 2 energy"). Colorless (the printed "*" symbol) can be paid with any
  type. See `can_pay_cost()`.
- Weakness = x2 damage, Resistance = -20 flat damage, applied in the
  official order (base -> weakness -> resistance, floor 0). Damage to
  Benched Pokemon ignores both, per official rules.
- Named Trainer cards are modeled individually (Rare Candy, Buddy-Buddy
  Poffin, Billy & O'Nare, Boxed Order, Energy Search Pro, Ultra Ball,
  Energy Search) with their real printed effects. See TRAINER_CARDS.
- Named attack/Ability side effects are modeled individually for every
  move used by the two candidate decks: Ceruledge's Infernal Slash
  (requires discarding 4 Basic Fire Energy from HAND or it deals 0
  damage), Ceruledge ex's Abyssal Flames (scales with discard pile) and
  Raging Amethyst (self-discards all energy after use), Centiskorch's
  Billowing Heat Wave (splash damage to own bench), Dragapult ex's
  Phantom Dive (bench damage to opponent), Haxorus's Dragon Pulse (self
  mill) and Bring Down the Axe (conditional KO on Special Energy -- deals
  0 in this simulation since neither deck runs Special Energy cards).
  See MOVE_EFFECTS / _effect_* methods.
- Deck construction is validated against real rules: max 4 copies of any
  named card (Pokemon or Trainer) except Basic Energy, and max 1 ACE SPEC
  card per deck (see DeckList.build_deck()).
- Self-inflicted KOs (e.g. from Billowing Heat Wave hitting your own
  bench) still award the opponent a prize, matching official rules.

Remaining simplifications (documented, not hidden):
- No Special Conditions (Poisoned/Asleep/Confused/Paralyzed).
- Dual/multiple Weakness or Resistance types are not modeled (none exist
  in this dataset; each Pokemon has at most one of each).
- Stadium cards, Pokemon Tools, and Ability text beyond Drakloak's Recon
  Directive are not implemented for other cards (neither deck uses them
  as a core mechanic).
- Bench size cap of 5 (6 Pokemon total in play) is enforced; no other
  board-state rules (e.g. Lost Zone) are modeled.

None of this is meant to perfectly replicate tournament-legal PTCG play;
it exists to compare the RELATIVE performance of the two candidate deck
archetypes (Dragon vs Fire) under identical, simplified rules and to
surface real deckbuilding tradeoffs (like Ceruledge's hidden energy-in-
hand cost) that a pure card-stats read would miss.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Cost checking (typed energy)
# ---------------------------------------------------------------------------

def can_pay_cost(attached: dict[str, int], cost: dict[str, int]) -> bool:
    """Check whether `attached` energy (by type) can pay `cost` (by type).

    Specific-type requirements must be met by that exact type. "Colorless"
    requirements can be paid by any leftover energy of any type, matching
    the real rule for the printed '*' cost symbol.
    """
    remaining = dict(attached)
    colorless_needed = 0
    for etype, amount in cost.items():
        if etype == "Colorless":
            colorless_needed += amount
            continue
        have = remaining.get(etype, 0)
        if have < amount:
            return False
        remaining[etype] = have - amount
    return sum(remaining.values()) >= colorless_needed


# ---------------------------------------------------------------------------
# Card model
# ---------------------------------------------------------------------------

@dataclass
class Move:
    name: str
    cost: dict[str, int]   # e.g. {"Fire": 1} or {"Fire": 1, "Psychic": 1} or {"Colorless": 3}
    damage: int              # base printed damage; 0 for utility/no-damage moves
    effect: str | None = None  # dispatch tag for a bespoke side effect, see MOVE_EFFECTS
    hand_cost: dict[str, int] = field(default_factory=dict)
    # ^ Cards of this type that must be discarded FROM HAND (not attached
    # energy) to use this move, e.g. Ceruledge's Infernal Slash needs 4
    # Basic Fire Energy in hand: hand_cost={"Fire": 4}. This is metadata
    # the AGENT can use to decide whether to bank energy in hand instead
    # of attaching it -- see Game._attach_energy. The actual veto/discard
    # still happens in the move's effect handler at attack time.

    @property
    def total_cost(self) -> int:
        return sum(self.cost.values())


@dataclass(frozen=True, eq=False)
class PokemonCard:
    card_id: int
    name: str
    stage: str             # "Basic", "Stage 1", "Stage 2"
    hp: int
    ptype: str              # readable type, e.g. "Fire", "Dragon"
    weakness: str | None
    resistance: str | None
    retreat_cost: int
    evolves_from: str | None
    moves: tuple[Move, ...]
    ability: str | None = None   # dispatch tag, e.g. "recon_directive"
    is_ex: bool = False    # ex cards give up 2 prizes when KO'd
    tera_bench_immune: bool = False
    # ^ [Tera] passive on Ceruledge ex / Dragapult ex: "As long as this
    # Pokemon is on your Bench, prevent all damage done to this Pokemon
    # by attacks (both yours and your opponent's)." Directly relevant
    # here since Phantom Dive snipes the opponent's bench and Billowing
    # Heat Wave splashes the user's own bench -- both would otherwise
    # ignore this real defensive passive.

    def __hash__(self) -> int:
        return hash(self.card_id)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PokemonCard) and self.card_id == other.card_id

    def damaging_moves(self) -> list[Move]:
        return [m for m in self.moves if m.damage > 0]

    def cheapest_damaging_cost(self) -> dict[str, int] | None:
        dmg_moves = self.damaging_moves()
        if not dmg_moves:
            return None
        return min(dmg_moves, key=lambda m: m.total_cost).cost

    def best_affordable_move(self, attached_energy: dict[str, int]) -> Move | None:
        affordable = [m for m in self.damaging_moves() if can_pay_cost(attached_energy, m.cost)]
        if not affordable:
            return None
        return max(affordable, key=lambda m: m.damage)


@dataclass
class InPlayPokemon:
    card: PokemonCard
    damage_taken: int = 0
    attached_energy: dict[str, int] = field(default_factory=dict)
    evolved_this_turn: bool = False
    ability_used_this_turn: bool = False
    entered_play_on_turn: int = 0  # owner's turn number (Player.turns_taken) when placed

    @property
    def remaining_hp(self) -> int:
        return self.card.hp - self.damage_taken

    @property
    def is_knocked_out(self) -> bool:
        return self.remaining_hp <= 0

    @property
    def total_energy(self) -> int:
        return sum(self.attached_energy.values())


# ---------------------------------------------------------------------------
# Trainer card registry
# ---------------------------------------------------------------------------
# Each entry: category ("Item" or "Supporter"), ace_spec (max-1-per-deck rule),
# and the name of the Game._trainer_* handler method that resolves it.

TRAINER_CARDS: dict[str, dict] = {
    "Rare Candy": {"category": "Item", "ace_spec": False},
    "Buddy-Buddy Poffin": {"category": "Item", "ace_spec": False},
    "Billy & O'Nare": {"category": "Supporter", "ace_spec": False},
    "Boxed Order": {"category": "Item", "ace_spec": False},
    "Energy Search Pro": {"category": "Item", "ace_spec": True},
    "Ultra Ball": {"category": "Item", "ace_spec": False},
    "Energy Search": {"category": "Item", "ace_spec": False},
}

NON_POKEMON_ENERGY_PREFIX = "Energy:"


def is_energy_token(token: str) -> bool:
    return token.startswith(NON_POKEMON_ENERGY_PREFIX)


def energy_type_of(token: str) -> str:
    return token.split(":", 1)[1]


# ---------------------------------------------------------------------------
# Deck definition
# ---------------------------------------------------------------------------

@dataclass
class DeckList:
    name: str
    pokemon_counts: dict[PokemonCard, int]
    energy_counts: dict[str, int]        # energy type -> count, e.g. {"Fire": 10}
    trainer_counts: dict[str, int]        # trainer card name -> count (must be in TRAINER_CARDS)

    def build_deck(self) -> list[str]:
        """Return a shuffled list of card tokens and validate deck legality:
        - exactly 60 cards
        - at most 4 copies of any named Pokemon or Trainer card
        - at most 1 ACE SPEC card total
        """
        for card, count in self.pokemon_counts.items():
            if count > 4:
                raise ValueError(f"Deck '{self.name}': {count}x {card.name} exceeds the 4-copy limit")
        ace_spec_total = 0
        for tname, count in self.trainer_counts.items():
            if tname not in TRAINER_CARDS:
                raise ValueError(f"Deck '{self.name}': unknown trainer card '{tname}'")
            if count > 4:
                raise ValueError(f"Deck '{self.name}': {count}x {tname} exceeds the 4-copy limit")
            if TRAINER_CARDS[tname]["ace_spec"]:
                ace_spec_total += count
        if ace_spec_total > 1:
            raise ValueError(f"Deck '{self.name}': {ace_spec_total} ACE SPEC cards exceeds the 1-per-deck limit")

        deck: list[str] = []
        for card, count in self.pokemon_counts.items():
            deck.extend([card.name] * count)
        for etype, count in self.energy_counts.items():
            deck.extend([f"{NON_POKEMON_ENERGY_PREFIX}{etype}"] * count)
        for tname, count in self.trainer_counts.items():
            deck.extend([tname] * count)

        total = len(deck)
        if total != 60:
            raise ValueError(f"Deck '{self.name}' has {total} cards, expected 60")
        random.shuffle(deck)
        return deck

    def card_by_name(self, name: str) -> PokemonCard:
        for card in self.pokemon_counts:
            if card.name == name:
                return card
        raise KeyError(name)

    def is_pokemon_token(self, token: str) -> bool:
        return not is_energy_token(token) and token not in TRAINER_CARDS

    def basics(self) -> list[PokemonCard]:
        return [c for c in self.pokemon_counts if c.stage == "Basic"]


# ---------------------------------------------------------------------------
# Player / game state
# ---------------------------------------------------------------------------

@dataclass
class Player:
    name: str
    deck: DeckList
    draw_pile: list[str] = field(default_factory=list)
    hand: list[str] = field(default_factory=list)
    bench: list[InPlayPokemon] = field(default_factory=list)
    active: InPlayPokemon | None = None
    discard: list[str] = field(default_factory=list)
    prizes_remaining: int = 6
    supporter_played_this_turn: bool = False
    decked_out: bool = False
    turns_taken: int = 0
    turn_ended_early: bool = False

    def setup(self) -> None:
        self.draw_pile = self.deck.build_deck()
        self.hand = []
        self.mulligan_safe_draw_opening_hand()

    def mulligan_safe_draw_opening_hand(self, hand_size: int = 7) -> None:
        """Draw an opening hand, redrawing (mulligan) if it has no Basic
        Pokemon. Simplified: no penalty draw for the opponent is modeled."""
        for _ in range(20):  # safety cap against infinite mulligan loops
            self.hand = [self.draw_pile.pop() for _ in range(hand_size) if self.draw_pile]
            has_basic = any(
                self.deck.is_pokemon_token(c) and self.deck.card_by_name(c).stage == "Basic"
                for c in self.hand
            )
            if has_basic:
                return
            self.draw_pile.extend(self.hand)
            random.shuffle(self.draw_pile)
        # give up avoiding mulligan after cap; keep last hand drawn

    def draw(self, n: int = 1) -> None:
        for _ in range(n):
            if not self.draw_pile:
                self.decked_out = True
                return
            self.hand.append(self.draw_pile.pop())

    def all_pokemon_in_play(self) -> list[InPlayPokemon]:
        return ([self.active] if self.active else []) + self.bench

    def basics_in_hand(self) -> list[str]:
        return [c for c in self.hand
                if self.deck.is_pokemon_token(c) and self.deck.card_by_name(c).stage == "Basic"]


class Game:
    """Runs a single match between two decks and returns the winner."""

    MAX_TURNS = 60  # safety cap to avoid unbounded games
    BENCH_LIMIT = 5
    LOW_HP_RETREAT_THRESHOLD = 0.35

    def __init__(self, deck_a: DeckList, deck_b: DeckList, seed: int | None = None):
        if seed is not None:
            random.seed(seed)
        self.players = [Player("A", deck_a), Player("B", deck_b)]
        for p in self.players:
            p.setup()

    def play(self) -> dict:
        for p in self.players:
            self._place_opening_basic(p)

        turn = 0
        active_idx = 0
        while turn < self.MAX_TURNS:
            turn += 1
            player = self.players[active_idx]
            opponent = self.players[1 - active_idx]
            self._take_turn(player, opponent)

            result = self._check_game_over(player, opponent)
            if result is not None:
                winner, reason = result
                return {"winner": winner.name, "turns": turn, "reason": reason}

            active_idx = 1 - active_idx

        a, b = self.players
        if a.prizes_remaining == b.prizes_remaining:
            return {"winner": "draw", "turns": turn, "reason": "turn_cap"}
        winner = a if a.prizes_remaining < b.prizes_remaining else b
        return {"winner": winner.name, "turns": turn, "reason": "turn_cap"}

    # -- setup ---------------------------------------------------------

    def _place_opening_basic(self, player: Player) -> None:
        basics = player.basics_in_hand()
        if not basics:
            player.active = None
            return
        chosen = basics[0]
        player.hand.remove(chosen)
        player.active = InPlayPokemon(card=player.deck.card_by_name(chosen), entered_play_on_turn=0)

    # -- turn structure --------------------------------------------------

    def _take_turn(self, player: Player, opponent: Player) -> None:
        if player.active is None and not player.bench:
            return  # already lost, nothing to do

        player.turns_taken += 1
        player.supporter_played_this_turn = False
        player.draw(1)

        for pk in player.all_pokemon_in_play():
            pk.evolved_this_turn = False
            pk.ability_used_this_turn = False

        player.turn_ended_early = False
        self._use_abilities(player)
        self._play_trainers(player, opponent)
        if player.turn_ended_early:
            return  # e.g. Boxed Order ends the turn immediately after use
        self._play_basics_from_hand(player)
        self._attempt_evolve(player)
        self._attach_energy(player)
        self._retreat_if_favorable(player, opponent)
        self._attack(player, opponent)

    # -- Abilities ------------------------------------------------------

    def _use_abilities(self, player: Player) -> None:
        for pk in player.all_pokemon_in_play():
            if pk.card.ability == "recon_directive" and not pk.ability_used_this_turn:
                self._effect_recon_directive(player, pk)

    def _effect_recon_directive(self, player: Player, pk: InPlayPokemon) -> None:
        """Drakloak's Ability: look at top 2 cards of deck, keep 1, bottom
        the other."""
        if len(player.draw_pile) < 1:
            return
        top_n = player.draw_pile[-min(2, len(player.draw_pile)):]
        # Prefer keeping a Pokemon/Trainer over Energy if there's a choice;
        # otherwise keep the first. This is a simple heuristic, not a
        # perfect-information optimal choice.
        keep = max(top_n, key=lambda c: 0 if is_energy_token(c) else 1)
        for c in top_n:
            player.draw_pile.remove(c)
        player.hand.append(keep)
        for c in top_n:
            if c != keep:
                player.draw_pile.insert(0, c)  # bottom of deck
        pk.ability_used_this_turn = True

    # -- Trainer cards ----------------------------------------------------

    def _play_trainers(self, player: Player, opponent: Player) -> None:
        # Supporter: at most 1 per turn (real rule)
        if not player.supporter_played_this_turn:
            for tname in list(player.hand):
                if tname in TRAINER_CARDS and TRAINER_CARDS[tname]["category"] == "Supporter":
                    if self._resolve_trainer(player, opponent, tname):
                        player.supporter_played_this_turn = True
                        break

        # Items: no per-turn limit (real rule), play greedily while useful
        played_something = True
        while played_something:
            played_something = False
            for tname in list(player.hand):
                if tname in TRAINER_CARDS and TRAINER_CARDS[tname]["category"] == "Item":
                    if self._resolve_trainer(player, opponent, tname):
                        played_something = True
                        break

    def _resolve_trainer(self, player: Player, opponent: Player, tname: str) -> bool:
        """Attempt to resolve the named trainer card. Returns True if it
        was played (removed from hand), False if its effect isn't
        currently usable (card stays in hand)."""
        handler = {
            "Rare Candy": self._trainer_rare_candy,
            "Buddy-Buddy Poffin": self._trainer_buddy_buddy_poffin,
            "Billy & O'Nare": self._trainer_billy_and_onare,
            "Boxed Order": self._trainer_boxed_order,
            "Energy Search Pro": self._trainer_energy_search_pro,
            "Ultra Ball": self._trainer_ultra_ball,
            "Energy Search": self._trainer_energy_search,
        }.get(tname)
        if handler is None:
            return False
        return handler(player, opponent)

    def _trainer_rare_candy(self, player: Player, opponent: Player) -> bool:
        """Choose a Basic in play; if a Stage 2 evolving from it is in
        hand, evolve straight to it, skipping Stage 1. Not usable on
        turn 1 or on a Basic played this turn."""
        if player.turns_taken <= 1:
            return False
        for pk in player.all_pokemon_in_play():
            if pk.card.stage != "Basic" or pk.entered_play_on_turn == player.turns_taken:
                continue
            stage2 = next(
                (c for c in player.hand
                 if player.deck.is_pokemon_token(c)
                 and player.deck.card_by_name(c).stage == "Stage 2"
                 and self._evolution_chain_root(player, c) == pk.card.name),
                None,
            )
            if stage2 is None:
                continue
            player.hand.remove(stage2)
            player.hand.remove("Rare Candy")
            pk.card = player.deck.card_by_name(stage2)
            pk.evolved_this_turn = True
            return True
        return False

    def _evolution_chain_root(self, player: Player, stage2_name: str) -> str | None:
        """Walk Stage2 -> Stage1 -> Basic and return the Basic's name."""
        stage2 = player.deck.card_by_name(stage2_name)
        if stage2.evolves_from is None:
            return None
        try:
            stage1 = player.deck.card_by_name(stage2.evolves_from)
        except KeyError:
            return None  # the Stage 1 isn't part of this deck's card pool
        return stage1.evolves_from

    def _trainer_buddy_buddy_poffin(self, player: Player, opponent: Player) -> bool:
        """Search deck for up to 2 Basic Pokemon with HP<=70 and bench them."""
        if len(player.bench) >= self.BENCH_LIMIT:
            return False
        candidates = [c for c in player.draw_pile
                      if player.deck.is_pokemon_token(c)
                      and player.deck.card_by_name(c).stage == "Basic"
                      and player.deck.card_by_name(c).hp <= 70]
        if not candidates:
            return False
        player.hand.remove("Buddy-Buddy Poffin")
        chosen = candidates[:min(2, self.BENCH_LIMIT - len(player.bench))]
        for c in chosen:
            player.draw_pile.remove(c)
            player.bench.append(InPlayPokemon(card=player.deck.card_by_name(c), entered_play_on_turn=player.turns_taken))
        random.shuffle(player.draw_pile)
        return True

    def _trainer_billy_and_onare(self, player: Player, opponent: Player) -> bool:
        """Draw 2; if hand size is then >=10, draw 2 more."""
        player.hand.remove("Billy & O'Nare")
        player.draw(2)
        if len(player.hand) >= 10:
            player.draw(2)
        return True

    def _trainer_boxed_order(self, player: Player, opponent: Player) -> bool:
        """Search deck for up to 2 Item cards; ends the turn immediately,
        so only play it if the active Pokemon can't attack this turn
        anyway (no point sacrificing a turn where we could otherwise
        attach Energy and attack)."""
        can_already_attack = (
            player.active is not None
            and player.active.card.best_affordable_move(player.active.attached_energy) is not None
        )
        if can_already_attack:
            return False
        items_in_deck = [c for c in player.draw_pile if c in TRAINER_CARDS and TRAINER_CARDS[c]["category"] == "Item"]
        if not items_in_deck:
            return False
        player.hand.remove("Boxed Order")
        chosen = items_in_deck[:2]
        for c in chosen:
            player.draw_pile.remove(c)
            player.hand.append(c)
        random.shuffle(player.draw_pile)
        player.discard.append("Boxed Order")
        player.turn_ended_early = True
        return True

    def _trainer_energy_search_pro(self, player: Player, opponent: Player) -> bool:
        """ACE SPEC: search deck for any number of Basic Energy of
        different types, put into hand. Grabs one of each type present."""
        energy_types = {energy_type_of(c) for c in player.draw_pile if is_energy_token(c)}
        if not energy_types:
            return False
        player.hand.remove("Energy Search Pro")
        for etype in energy_types:
            token = f"{NON_POKEMON_ENERGY_PREFIX}{etype}"
            player.draw_pile.remove(token)
            player.hand.append(token)
        random.shuffle(player.draw_pile)
        return True

    def _trainer_ultra_ball(self, player: Player, opponent: Player) -> bool:
        """Discard 2 other cards from hand; search deck for any Pokemon.

        Targeting: if the player has no Pokemon at all in play (needs an
        opening Basic) or no Basic currently in hand to play this turn,
        prefer fetching a Basic -- a Stage 1/2 card is dead weight in hand
        until its pre-evolution is already in play. Otherwise, prefer an
        evolution card that completes an evolution already in play.
        """
        others = [c for c in player.hand if c != "Ultra Ball"]
        if len(others) < 2:
            return False
        pokemon_in_deck = [c for c in player.draw_pile if player.deck.is_pokemon_token(c)]
        if not pokemon_in_deck:
            return False

        has_basic_in_hand = bool(player.basics_in_hand())
        board_is_thin = len(player.all_pokemon_in_play()) < 2  # active only, no bench backup
        in_play_names = {pk.card.name for pk in player.all_pokemon_in_play()}

        def priority(token: str) -> int:
            card = player.deck.card_by_name(token)
            is_usable_evolution = card.stage != "Basic" and card.evolves_from in in_play_names
            if card.stage == "Basic" and board_is_thin and not has_basic_in_hand:
                return 0  # existential: no bench backup, get a body in play first
            if is_usable_evolution:
                return 1  # a usable evolution for something already in play
            if card.stage == "Basic":
                return 2 if not has_basic_in_hand else 3
            return 4  # a Stage1/2 with no matching pre-evolution in play yet: low priority

        chosen = min(pokemon_in_deck, key=priority)

        player.hand.remove("Ultra Ball")
        for c in others[:2]:
            player.hand.remove(c)
            player.discard.append(c)
        player.draw_pile.remove(chosen)
        player.hand.append(chosen)
        random.shuffle(player.draw_pile)
        return True

    def _trainer_energy_search(self, player: Player, opponent: Player) -> bool:
        """Search deck for a single Basic Energy card, put into hand."""
        energies = [c for c in player.draw_pile if is_energy_token(c)]
        if not energies:
            return False
        player.hand.remove("Energy Search")
        chosen = energies[0]
        player.draw_pile.remove(chosen)
        player.hand.append(chosen)
        random.shuffle(player.draw_pile)
        return True

    # -- Basics / evolution -----------------------------------------------

    def _play_basics_from_hand(self, player: Player) -> None:
        while len(player.bench) < self.BENCH_LIMIT:
            basics = player.basics_in_hand()
            if not basics:
                break
            chosen = basics[0]
            player.hand.remove(chosen)
            player.bench.append(InPlayPokemon(
                card=player.deck.card_by_name(chosen),
                entered_play_on_turn=player.turns_taken,
            ))

    def _attempt_evolve(self, player: Player) -> None:
        for pk in player.all_pokemon_in_play():
            if pk.evolved_this_turn:
                continue
            if player.turns_taken <= 1 or pk.entered_play_on_turn == player.turns_taken:
                continue  # real rule: no evolving turn 1, or the turn a Pokemon entered play
            evolution_name = next(
                (c for c in player.hand
                 if player.deck.is_pokemon_token(c)
                 and player.deck.card_by_name(c).evolves_from == pk.card.name),
                None,
            )
            if evolution_name is None:
                continue
            player.hand.remove(evolution_name)
            pk.card = player.deck.card_by_name(evolution_name)
            pk.evolved_this_turn = True

    # -- Energy -----------------------------------------------------------

    def _attach_energy(self, player: Player) -> None:
        """Attach at most one Energy per turn (real rule). Picks the
        Energy type + target Pokemon combination that gets closest to
        completing that Pokemon's cheapest damaging move.

        Before attaching, checks whether any in-play Pokemon has a move
        whose ATTACHED cost is already met but that also needs cards of
        this energy type discarded FROM HAND to use (e.g. Ceruledge's
        Infernal Slash: 1 Fire attached + 4 Fire discarded from hand).
        In that case the agent banks that energy type in hand instead of
        attaching more of it, since attaching wouldn't help pay a
        hand-discard cost. Without this, the agent would attach every
        Fire Energy the instant it's drawn and never accumulate enough
        in hand to ever use Infernal Slash."""
        energy_in_hand = [c for c in player.hand if is_energy_token(c)]
        if not energy_in_hand:
            return
        candidates = player.all_pokemon_in_play()
        if not candidates:
            return

        banked_types = self._energy_types_to_bank_for_hand_costs(player, candidates)
        attachable = [c for c in energy_in_hand if energy_type_of(c) not in banked_types]
        if not attachable:
            return  # hold everything in hand; it's all being banked for a hand-discard cost
        energy_in_hand = attachable

        best = None  # (need_after, token, pk)
        for pk in candidates:
            cost = pk.card.cheapest_damaging_cost()
            if cost is None:
                continue
            for token in energy_in_hand:
                etype = energy_type_of(token)
                trial = dict(pk.attached_energy)
                trial[etype] = trial.get(etype, 0) + 1
                if can_pay_cost(trial, cost):
                    need_after = 0
                else:
                    need_after = sum(cost.values()) - sum(min(trial.get(t, 0), c) for t, c in cost.items() if t != "Colorless")
                key = (0 if can_pay_cost(trial, cost) else 1, need_after)
                if best is None or key < best[0]:
                    best = (key, token, pk)

        if best is None:
            token = energy_in_hand[0]
            target = player.active or candidates[0]
        else:
            _, token, target = best

        player.hand.remove(token)
        etype = energy_type_of(token)
        target.attached_energy[etype] = target.attached_energy.get(etype, 0) + 1

    def _energy_types_to_bank_for_hand_costs(self, player: Player, candidates: list[InPlayPokemon]) -> set[str]:
        """Return energy types the agent should keep in hand (not attach)
        because some in-play Pokemon has a move needing that many of that
        type discarded from hand once its attached cost is otherwise met."""
        banked: set[str] = set()
        for pk in candidates:
            for move in pk.card.damaging_moves():
                if not move.hand_cost:
                    continue
                attached_cost_met = can_pay_cost(pk.attached_energy, move.cost)
                if attached_cost_met:
                    banked.update(move.hand_cost.keys())
        return banked

    # -- Retreat ------------------------------------------------------------

    def _retreat_if_favorable(self, player: Player, opponent: Player) -> None:
        active = player.active
        if active is None or not player.bench:
            return

        low_hp = active.remaining_hp <= active.card.hp * self.LOW_HP_RETREAT_THRESHOLD
        retreat_cost = active.card.retreat_cost
        can_afford_retreat = active.total_energy >= retreat_cost
        if not (low_hp and can_afford_retreat):
            return

        replacement = max(player.bench, key=lambda pk: pk.remaining_hp)
        if replacement.remaining_hp <= active.remaining_hp:
            return

        self._pay_retreat_cost(active, retreat_cost)
        player.bench.remove(replacement)
        player.bench.append(active)
        player.active = replacement

    def _pay_retreat_cost(self, pk: InPlayPokemon, amount: int) -> None:
        remaining = amount
        for etype in list(pk.attached_energy.keys()):
            while remaining > 0 and pk.attached_energy.get(etype, 0) > 0:
                pk.attached_energy[etype] -= 1
                remaining -= 1
            if pk.attached_energy.get(etype, 0) == 0:
                del pk.attached_energy[etype]

    # -- Attacking -----------------------------------------------------------

    def _attack(self, player: Player, opponent: Player) -> None:
        active = player.active
        if active is None or opponent.active is None:
            return
        move = active.card.best_affordable_move(active.attached_energy)
        if move is None:
            return

        # Resolve any move-specific pre-conditions/side effects. A handler
        # may veto the attack (e.g. Infernal Slash with <4 Fire Energy in
        # hand) by returning damage=0 via the returned override.
        damage = move.damage
        effect_fn = MOVE_EFFECTS.get(move.effect)
        if effect_fn is not None:
            damage = effect_fn(self, player, opponent, active, move, damage)

        damage = self._apply_weakness_resistance(active, opponent.active, damage)
        if damage <= 0:
            return

        self._deal_damage(opponent, opponent.active, damage, attacker=player)

    def _apply_weakness_resistance(self, attacker: InPlayPokemon, defender: InPlayPokemon, damage: int) -> int:
        if damage <= 0:
            return damage
        if defender.card.weakness is not None and attacker.card.ptype == defender.card.weakness:
            damage *= 2
        if defender.card.resistance is not None and attacker.card.ptype == defender.card.resistance:
            damage = max(0, damage - 20)
        return damage

    def _deal_damage(self, defender_player: Player, target: InPlayPokemon, damage: int, attacker: Player) -> None:
        """Deal damage to `target` (one of defender_player's Pokemon) and
        handle a resulting knockout, awarding the prize to `attacker`.
        Used both for normal attacks and for self/bench splash effects.

        Respects the [Tera] bench-damage-immunity passive (Ceruledge ex,
        Dragapult ex): if `target` is currently benched and has
        tera_bench_immune, the damage is fully prevented.
        """
        is_benched = target is not defender_player.active
        if is_benched and target.card.tera_bench_immune:
            return
        target.damage_taken += damage
        if target.is_knocked_out:
            self._handle_knockout(defender_player, target, attacker)

    def _handle_knockout(self, defender_player: Player, ko_pokemon: InPlayPokemon, attacker: Player) -> None:
        """Award prizes to `attacker` for the KO. Real rule: it doesn't
        matter how the KO happened (opponent's attack, self-damage, etc.)
        -- the opponent of the player who lost the Pokemon always takes
        the prize(s)."""
        name = ko_pokemon.card.name.lower()
        if name.startswith("mega ") and name.endswith(" ex"):
            prizes_taken = 3
        elif name.endswith(" ex"):
            prizes_taken = 2
        else:
            prizes_taken = 1
        attacker.prizes_remaining = max(0, attacker.prizes_remaining - prizes_taken)

        if defender_player.active is ko_pokemon:
            defender_player.active = None
            if defender_player.bench:
                defender_player.active = defender_player.bench.pop(0)
        elif ko_pokemon in defender_player.bench:
            defender_player.bench.remove(ko_pokemon)

    # -- Win condition -----------------------------------------------------

    def _check_game_over(self, player: Player, opponent: Player) -> tuple[Player, str] | None:
        """Win conditions (simplified real rules):
        - A player whose prizes_remaining hits 0 WINS.
        - A player with no Active and no Benched Pokemon LOSES.
        - A player forced to draw from an empty deck LOSES.
        """
        for p, other in ((player, opponent), (opponent, player)):
            if p.prizes_remaining <= 0:
                return p, "prizes_taken"
            if p.decked_out:
                return other, "opponent_decked_out"
            if p.active is None and not p.bench:
                return other, "no_pokemon_left"
        return None


# ---------------------------------------------------------------------------
# Move-specific effects
# ---------------------------------------------------------------------------
# Each handler: (game, player, opponent, attacker_pk, move, base_damage) -> final_damage
# May mutate player/opponent state (discards, bench damage, etc.) and may
# return 0 to veto the attack's direct damage.

def _effect_infernal_slash(game: Game, player: Player, opponent: Player,
                            attacker: InPlayPokemon, move: Move, damage: int) -> int:
    """Ceruledge - Infernal Slash: discard 4 Basic Fire Energy from HAND.
    If you can't, the attack does nothing. This is a real, meaningful
    resource cost that a "1 energy / 220 damage" stats read misses
    entirely -- the attacker needs a steady stream of Fire Energy draws
    to hand, not just 1 energy attached."""
    fire_in_hand = [c for c in player.hand if is_energy_token(c) and energy_type_of(c) == "Fire"]
    if len(fire_in_hand) < 4:
        return 0
    for c in fire_in_hand[:4]:
        player.hand.remove(c)
        player.discard.append(c)
    return damage


def _effect_abyssal_flames(game: Game, player: Player, opponent: Player,
                            attacker: InPlayPokemon, move: Move, damage: int) -> int:
    """Ceruledge ex - Abyssal Flames: +20 damage for each Energy card in
    your discard pile."""
    energy_in_discard = sum(1 for c in player.discard if is_energy_token(c))
    return damage + 20 * energy_in_discard


def _effect_raging_amethyst(game: Game, player: Player, opponent: Player,
                             attacker: InPlayPokemon, move: Move, damage: int) -> int:
    """Ceruledge ex - Raging Amethyst: discard all Energy from this
    Pokemon after attacking (self-cost, paid after damage is dealt)."""
    for etype in list(attacker.attached_energy.keys()):
        player.discard.extend([f"{NON_POKEMON_ENERGY_PREFIX}{etype}"] * attacker.attached_energy[etype])
    attacker.attached_energy.clear()
    return damage


def _effect_billowing_heat_wave(game: Game, player: Player, opponent: Player,
                                 attacker: InPlayPokemon, move: Move, damage: int) -> int:
    """Centiskorch - Billowing Heat Wave: also does 30 damage to each of
    YOUR OWN Benched Pokemon (no Weakness/Resistance on bench damage,
    per official rules). This can KO your own bench -- a real drawback a
    flat "130 damage / 1 energy" stats read misses."""
    for bench_pk in list(player.bench):
        game._deal_damage(player, bench_pk, 30, attacker=opponent)
    return damage


def _effect_phantom_dive(game: Game, player: Player, opponent: Player,
                          attacker: InPlayPokemon, move: Move, damage: int) -> int:
    """Dragapult ex - Phantom Dive: put 6 damage counters (60 damage) on
    the opponent's Benched Pokemon, distributed 1 counter (10 damage) at
    a time onto the lowest-HP bench target first (a reasonable
    damage-maximizing heuristic for "in any way you like"). No Weakness/
    Resistance on bench damage, per official rules."""
    remaining = 60
    while remaining > 0 and opponent.bench:
        target = min(opponent.bench, key=lambda pk: pk.remaining_hp)
        hit = min(10, remaining)
        game._deal_damage(opponent, target, hit, attacker=player)
        remaining -= hit
        # _deal_damage already removes `target` from opponent.bench if it
        # was KO'd, so the next loop iteration naturally re-picks among
        # whatever remains on the bench.
    return damage


def _effect_dragon_pulse_mill(game: Game, player: Player, opponent: Player,
                               attacker: InPlayPokemon, move: Move, damage: int) -> int:
    """Haxorus - Dragon Pulse: discard the top 3 cards of your own deck
    (self-mill, a real risk of decking yourself out that a pure damage
    stat ignores)."""
    for _ in range(3):
        if not player.draw_pile:
            break
        player.discard.append(player.draw_pile.pop())
    return damage


def _effect_bring_down_the_axe(game: Game, player: Player, opponent: Player,
                                attacker: InPlayPokemon, move: Move, damage: int) -> int:
    """Haxorus - Bring Down the Axe: KOs the opponent's Active only if it
    has a Special Energy attached. Neither candidate deck runs Special
    Energy, so this always whiffs here -- modeled as 0 damage rather than
    silently granted, since assuming a free KO would be wrong."""
    return 0


MOVE_EFFECTS = {
    "infernal_slash": _effect_infernal_slash,
    "abyssal_flames": _effect_abyssal_flames,
    "raging_amethyst": _effect_raging_amethyst,
    "billowing_heat_wave": _effect_billowing_heat_wave,
    "phantom_dive": _effect_phantom_dive,
    "dragon_pulse_mill": _effect_dragon_pulse_mill,
    "bring_down_the_axe": _effect_bring_down_the_axe,
}
