"""Run a batch of Dragon vs Fire simulated matches and report win-rate stats.

Usage: python run_simulations.py [n_games]
"""

import sys
from collections import Counter

from decks import build_dragon_deck, build_fire_deck
from simulator import Game


def run_batch(n_games: int = 200) -> None:
    outcomes = Counter()
    turn_counts = []
    reasons = Counter()
    deckout_victim = Counter()

    for i in range(n_games):
        dragon = build_dragon_deck()
        fire = build_fire_deck()

        # Alternate which deck is "A" (goes first) to cancel out first-move advantage
        if i % 2 == 0:
            game = Game(dragon, fire, seed=i)
            result = game.play()
            winner_deck = {"A": "Dragon", "B": "Fire", "draw": "draw"}[result["winner"]]
            label = {"A": "Dragon", "B": "Fire"}
        else:
            game = Game(fire, dragon, seed=i)
            result = game.play()
            winner_deck = {"A": "Fire", "B": "Dragon", "draw": "draw"}[result["winner"]]
            label = {"A": "Fire", "B": "Dragon"}

        outcomes[winner_deck] += 1
        turn_counts.append(result["turns"])
        reasons[result["reason"]] += 1
        if result["reason"] == "opponent_decked_out":
            # the winner's opponent decked out; find which deck that was
            loser_key = "B" if result["winner"] == "A" else "A"
            deckout_victim[label[loser_key]] += 1

    print(f"Ran {n_games} games (Dragon Control vs Fire Aggro)\n")
    print("Win counts:")
    for deck, count in outcomes.most_common():
        pct = 100 * count / n_games
        print(f"  {deck:10s}: {count:4d}  ({pct:.1f}%)")

    print(f"\nAvg game length: {sum(turn_counts) / len(turn_counts):.1f} turns")
    print(f"Min/Max turns:   {min(turn_counts)} / {max(turn_counts)}")
    print("\nEnd reasons:")
    for reason, count in reasons.most_common():
        print(f"  {reason}: {count}")

    if deckout_victim:
        print("\nWhich deck decked itself out (lost by empty deck):")
        for deck, count in deckout_victim.most_common():
            print(f"  {deck}: {count}")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    run_batch(n)
