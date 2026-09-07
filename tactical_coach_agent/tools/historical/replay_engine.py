"""
Replay Engine

Replays a Cricsheet match ball-by-ball and produces a snapshot after
every delivery.
"""


def replay_match(match_data: dict) -> list:
    snapshots = []
    innings_data = match_data["innings"]

    # -------------------------------------------------------
    # Calculate first innings total to determine chase target
    # -------------------------------------------------------
    first_innings = innings_data[0]
    first_innings_runs = 0

    for over_data in first_innings["overs"]:
        for delivery in over_data["deliveries"]:
            first_innings_runs += delivery["runs"]["total"]

    target = first_innings_runs + 1

    # -------------------------------------------------------
    # Replay every innings
    # -------------------------------------------------------
    for innings_index, innings in enumerate(innings_data):
        batting_team = innings["team"]

        if innings_index == 0:
            bowling_team = (
                innings_data[1]["team"]
                if len(innings_data) > 1
                else None
            )
            innings_target = None
        else:
            bowling_team = innings_data[0]["team"]
            innings_target = target

        runs = 0
        wickets = 0

        # Every delivery played so far
        ball_history = []

        # Bowling statistics accumulated during this innings
        bowling_stats = {}
        batter_stats = {}
        matchup_stats = {}

        for over_data in innings["overs"]:
            current_over = over_data["over"]

            for delivery in over_data["deliveries"]:
                delivery_runs = delivery["runs"]["total"]

                # ---------------------------------------------------
                # Wickets
                # ---------------------------------------------------
                delivery_wickets = 0

                if "wickets" in delivery:
                    delivery_wickets = len(delivery["wickets"])

                runs += delivery_runs
                wickets += delivery_wickets

                # ---------------------------------------------------
                # Legal delivery
                # Wides and no-balls do not count as legal balls
                # ---------------------------------------------------
                extras = delivery.get("extras", {}) or {}

                is_legal = not (
                    "wides" in extras or
                    "noballs" in extras
                )

                # ---------------------------------------------------
                # Exact delivery label
                #
                # Example:
                # 7.1
                # 7.2
                # 7.3
                # 7.4
                # 7.5
                # 7.6
                #
                # actual_delivery is preferred if available.
                # ---------------------------------------------------
                actual_del = delivery.get("actual_delivery")

                if actual_del is not None:
                    snapshot_over = str(actual_del)
                else:
                    ball_number = delivery.get("ball", 1)
                    snapshot_over = f"{current_over}.{ball_number}"

                # ---------------------------------------------------
                # Ball history
                # ---------------------------------------------------
                ball_history.append(
                    {
                        "over": current_over,
                        "runs": delivery_runs,
                        "wickets": delivery_wickets,
                    }
                )

                # ---------------------------------------------------
                # Bowling statistics
                # ---------------------------------------------------
                bowler = delivery["bowler"]
                batter = delivery.get("batter") or delivery.get("striker")

                if bowler not in bowling_stats:
                    bowling_stats[bowler] = {
                        "balls": 0,
                        "runs": 0,
                        "wickets": 0,
                    }

                if is_legal:
                    bowling_stats[bowler]["balls"] += 1

                bowling_stats[bowler]["runs"] += delivery_runs
                bowling_stats[bowler]["wickets"] += delivery_wickets

                if batter:
                    batter_figures = batter_stats.setdefault(
                        batter,
                        {"balls": 0, "runs": 0, "fours": 0, "sixes": 0, "dismissals": 0},
                    )
                    if is_legal:
                        batter_figures["balls"] += 1
                    batter_runs = delivery.get("runs", {}).get("batter", 0)
                    batter_figures["runs"] += batter_runs
                    batter_figures["fours"] += batter_runs == 4
                    batter_figures["sixes"] += batter_runs == 6
                    batter_figures["dismissals"] += sum(
                        1
                        for wicket in delivery.get("wickets", [])
                        if wicket.get("player_out") == batter
                    )

                    batter_matchups = matchup_stats.setdefault(batter, {})
                    matchup = batter_matchups.setdefault(
                        bowler,
                        {"balls": 0, "runs": 0, "dismissals": 0},
                    )
                    if is_legal:
                        matchup["balls"] += 1
                    matchup["runs"] += delivery.get("runs", {}).get("batter", 0)
                    matchup["dismissals"] += sum(
                        1
                        for wicket in delivery.get("wickets", [])
                        if wicket.get("player_out") == batter
                    )

                # ---------------------------------------------------
                # Recent 5 overs
                #
                # Important:
                # At over 7.4, this considers overs 3-7
                # plus the current over.
                #
                # At early innings, it only uses overs that
                # actually exist instead of pretending 5 overs
                # have been played.
                # ---------------------------------------------------
                recent_over_numbers = {
                    ball["over"]
                    for ball in ball_history
                    if current_over - 4 <= ball["over"] <= current_over
                }

                previous_five_overs = [
                    ball
                    for ball in ball_history
                    if ball["over"] in recent_over_numbers
                ]

                recent_runs = sum(
                    ball["runs"]
                    for ball in previous_five_overs
                )

                recent_wickets = sum(
                    ball["wickets"]
                    for ball in previous_five_overs
                )

                recent_overs_span = len(recent_over_numbers)

                # ---------------------------------------------------
                # Snapshot
                # ---------------------------------------------------
                snapshot = {
                    "batting_team": batting_team,
                    "bowling_team": bowling_team,

                    "runs": runs,
                    "wickets": wickets,

                    # Exact point in match
                    "overs": snapshot_over,
                    "snapshot_over": snapshot_over,

                    "target": innings_target,

                    "striker": (
                        delivery.get("batter")
                        or delivery.get("striker")
                    ),
                    "non_striker": delivery.get("non_striker"),

                    # Bowler who bowled THIS delivery
                    "current_bowler": bowler,

                    "venue": match_data.get(
                        "info", {}
                    ).get("venue"),

                    "match_type": match_data.get(
                        "info", {}
                    ).get("match_type"),

                    # ------------------------------------------------
                    # Recent form / momentum
                    # ------------------------------------------------
                    "recent_runs": recent_runs,
                    "recent_wickets": recent_wickets,
                    "recent_overs_span": recent_overs_span,

                    # ------------------------------------------------
                    # Bowling information
                    # ------------------------------------------------
                    "available_bowlers": list(
                        bowling_stats.keys()
                    ),

                    "bowling_stats": {
                        name: dict(figures)
                        for name, figures in bowling_stats.items()
                    },
                    "matchup_stats": {
                        batter_name: {
                            bowler_name: dict(figures)
                            for bowler_name, figures in bowlers.items()
                        }
                        for batter_name, bowlers in matchup_stats.items()
                    },
                    "batter_stats": {
                        name: dict(figures)
                        for name, figures in batter_stats.items()
                    },
                }

                snapshots.append(snapshot)

    return snapshots