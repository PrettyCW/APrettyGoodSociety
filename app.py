import csv, os, threading
from flask import Flask, render_template, abort
from collections import defaultdict, OrderedDict

app = Flask(__name__)

# ─── 1) Path to the CSV ─────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
CSV_PATH = os.path.join(BASE_DIR, "data", "league_data.csv")

# ─── 2) In‐memory cache for CSV rows + tracking last modification time ────
_csv_lock = threading.Lock()
_cached_rows = None
_cached_mtime = None

def load_csv_cached():
    global _cached_rows, _cached_mtime

    try:
        current_mtime = os.path.getmtime(CSV_PATH)
    except OSError:
        return []

    with _csv_lock:
        if _cached_rows is None or current_mtime != _cached_mtime:
            rows = []
            with open(CSV_PATH, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Convert and normalize types
                    try:
                        row["player_id"] = int(row["player_id"])
                    except:
                        row["player_id"] = None

                    # Ensure player_name is never None
                    row["player_name"] = (row.get("player_name") or "").strip()

                    row["event_id"] = (row.get("event_id") or "").strip()
                    row["event_date"] = (row.get("event_date") or "").strip()

                    try:
                        row["score"] = int(row.get("score", 0))
                    except:
                        row["score"] = 0
                    try:
                        row["rank"] = int(row.get("rank", 0))
                    except:
                        row["rank"] = 0
                    try:
                        row["points"] = int(row.get("points", 0))
                    except:
                        row["points"] = 0
                    try:
                        row["event_pr"] = int(row.get("event_pr", 0))
                    except:
                        row["event_pr"] = 0
                    try:
                        row["season"] = int(row.get("season", 0))
                    except:
                        row["season"] = 0

                    # This is the important change: guard against None
                    row["tier"] = (row.get("tier") or "").strip()

                    rows.append(row)

            _cached_rows = rows
            _cached_mtime = current_mtime

        return _cached_rows


# ─── ROUTE: Home (the “pretty” homepage) ────────────────────────────────────
@app.route("/")
def home():
    return render_template("home.html")


# ─── ROUTE: Leaderboard (society) ───────────────────────────────────────────
# ─── Revised show_leaderboard to support multiple seasons ────────────
from flask import request

@app.route("/leaderboard")
@app.route("/leaderboard/season/<int:season_id>")
def show_leaderboard(season_id=None):
    data = load_csv_cached()
    seasons = sorted({r["season"] for r in data})
    if not seasons:
        abort(404)
    if season_id in seasons:
        selected_season = season_id
    else:
        selected_season = seasons[-1]
    # Build the tier list for that season:
    tiers_present = sorted({r["tier"] for r in data if r["season"]==selected_season})

    # 3) Filter data to only rows in the selected season
    season_rows = [r for r in data if r["season"] == selected_season]

    # 4) Aggregate stats per player for this season
    stats = {}
    for r in season_rows:
        pid = r["player_id"]
        if pid is None:
            continue
        if pid not in stats:
            stats[pid] = {
                "player_id": pid,
                "player_name": r["player_name"],
                "wins": 0,
                "podiums": 0,
                "top10s": 0,
                "events_played": 0,
                "points": 0,
                "event_pr_sum": 0,
                "event_pr_count": 0
            }
        agg = stats[pid]
        agg["events_played"] += 1
        agg["points"] += r["points"]
        agg["event_pr_sum"] += r["event_pr"]
        agg["event_pr_count"] += 1
        if r["rank"] == 1:
            agg["wins"] += 1
        if 1 <= r["rank"] <= 3:
            agg["podiums"] += 1
        if 1 <= r["rank"] <= 10:
            agg["top10s"] += 1

    leaderboard = []
    for agg in stats.values():
        avg_pr = agg["event_pr_sum"] / agg["event_pr_count"] if agg["event_pr_count"] else 0
        leaderboard.append({
            "player_id": agg["player_id"],
            "player_name": agg["player_name"],
            "points": agg["points"],
            "wins": agg["wins"],
            "podiums": agg["podiums"],
            "top10s": agg["top10s"],
            "events_played": agg["events_played"],
            "prettyrating": round(avg_pr)
        })

    leaderboard.sort(key=lambda x: (x["points"], x["wins"]), reverse=True)

    return render_template(
      "leaderboard.html",
      leaderboard=leaderboard,
      seasons_sorted=seasons,
      selected_season=selected_season,
      tiers_present=tiers_present,
      selected_tier=None   # no tier selected
    )


# ─── ROUTE: Event details ─────────────────────────────────────────────────
@app.route("/event/<event_id>")
def show_event(event_id):
    data = load_csv_cached()
    rows = [r for r in data if r["event_id"] == event_id]
    if not rows:
        return abort(404)

    # Sort by rank
    rows.sort(key=lambda r: r["rank"])

    # Group by tier
    from collections import OrderedDict
    participants_by_tier = OrderedDict()
    for r in rows:
        tier = r["tier"] or "Unclassified"
        participants_by_tier.setdefault(tier, []).append(r)

    # Everyone’s in the same season for this event
    season = rows[0]["season"]

    # Everyone’s in the same date
    event_date = rows[0]["event_date"]

    return render_template(
        "event.html",
        event_id=event_id,
        event_date=event_date,
        participants_by_tier=participants_by_tier,
        season=season
    )





# ─── ROUTE: Player profile ─────────────────────────────────────────────────
@app.route("/player/<int:pid>")
def show_player(pid):
    data = load_csv_cached()
    player_rows = [r for r in data if r["player_id"] == pid]
    if not player_rows:
        return abort(404)

    # Sort by event_date
    player_rows.sort(key=lambda r: r["event_date"])

    # Build per-event history
    history = []
    total_wins = 0

    # For per‐season summaries, collect:
    #   season_stats[season] = {"tiers": set(...), "event_pr_sum": ..., "event_pr_count": ...}
    season_stats = defaultdict(lambda: {
        "tiers": set(),
        "event_pr_sum": 0,
        "event_pr_count": 0
    })

    for r in player_rows:
        history.append({
            "event_id": r["event_id"],
            "event_date": r["event_date"],
            "score": r["score"],
            "rank": r["rank"],
            "event_pr": r["event_pr"],
            "season": r["season"],
            "tier": r["tier"]
        })
        if r["rank"] == 1:
            total_wins += 1

        # Aggregate into the season_stats
        s = r["season"]
        season_stats[s]["tiers"].add(r["tier"])
        season_stats[s]["event_pr_sum"] += r["event_pr"]
        season_stats[s]["event_pr_count"] += 1

    # Build a list of per-season summaries
    season_summaries = []
    for s, info in season_stats.items():
        avg_pr = (info["event_pr_sum"] / info["event_pr_count"]) if info["event_pr_count"] > 0 else 0
        season_summaries.append({
            "season": s,
            "tiers": sorted(info["tiers"]),           # e.g. ["Premier", "Challenger"]
            "pretty_rating": round(avg_pr)
        })
    # Sort seasons ascending
    season_summaries.sort(key=lambda x: x["season"])

    player_name = player_rows[0]["player_name"]

    return render_template(
        "player.html",
        player_id=pid,
        player_name=player_name,
        history=history,
        total_wins=total_wins,
        season_summaries=season_summaries
    )


# --- NEW: load 2v2 teams ---
_teams_lock = threading.Lock()
_cached_teams = None
def load_2v2_teams():
    global _cached_teams
    if _cached_teams is None:
        path = os.path.join(BASE_DIR, "data", "2v2_teams.csv")
        teams = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                teams[int(r["team_id"])] = {
                    "team_name": r["team_name"],
                    "conference": r["conference"],
                    "players": [r["player1"], r["player2"]]
                }
        _cached_teams = teams
    return _cached_teams

# --- NEW: load 2v2 matches ---
_cached_matches = None
def load_2v2_matches():
    global _cached_matches
    if _cached_matches is None:
        path = os.path.join(BASE_DIR, "data", "2v2_matches.csv")
        ms = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                        # Parse date (allow blank)
                date = r.get("date","").strip() or None
                # Parse point_diff, treat blank or invalid as None
                try:
                    diff = int(r.get("point_diff",""))
                except (ValueError, TypeError):
                    diff = None

                ms.append({
                    "match_id":   int(r["match_id"]),
                    "season":     int(r["season"]),
                    "phase":      r["phase"],
                    "date":       date,
                    "winner_id":  int(r["winner_id"]),
                    "loser_id":   int(r["loser_id"]),
                    "point_diff": diff
                })
        _cached_matches = ms
    return _cached_matches

# --- 2v2 Teams page ---
@app.route("/2v2/teams")
def teams_2v2():
    teams = load_2v2_teams()
    # convert dict to list sorted by team_name
    team_list = sorted(
        [{"team_id": tid, **info} for tid, info in teams.items()],
        key=lambda x: x["team_name"].lower()
    )
    return render_template("2v2_teams.html", teams=team_list)

# --- 2v2 Leaderboard page ---
from collections import OrderedDict

@app.route("/2v2/leaderboard")
@app.route("/2v2/leaderboard/conference/<conf_name>")
def leaderboard_2v2(conf_name=None):
    teams   = load_2v2_teams()
    matches = load_2v2_matches()

    # 1) Conferences discovery (unchanged)
    conferences = sorted({info["conference"] for info in teams.values()})
    selected_conf = conf_name if conf_name in conferences else conferences[0]

    # 2) Initialize stats for teams in this conference
    stats = {
        tid: {"wins":0, "losses":0, "matches_played":0, "point_diff":0}
        for tid, info in teams.items()
        if info["conference"] == selected_conf
    }

    # 3) Tally only completed matches (where point_diff is not None)
    for m in matches:
        diff = m["point_diff"]
        if diff is None:
            continue               # skip fixtures without a result

        wid, lid = m["winner_id"], m["loser_id"]
        if wid in stats:
            stats[wid]["wins"] += 1
            stats[wid]["matches_played"] += 1
            stats[wid]["point_diff"] += diff
        if lid in stats:
            stats[lid]["losses"] += 1
            stats[lid]["matches_played"] += 1
            stats[lid]["point_diff"] -= diff

    # 4) Build the leaderboard list
    board = []
    for tid, s in stats.items():
        board.append({
            "team_id": tid,
            "team_name": teams[tid]["team_name"],
            "wins": s["wins"],
            "losses": s["losses"],
            "matches_played": s["matches_played"],
            "point_diff": s["point_diff"]
        })

    # 5) Sort by wins, then point_diff
    board.sort(key=lambda x: (x["wins"], x["point_diff"]), reverse=True)

    return render_template(
        "2v2_leaderboard.html",
        leaderboard=board,
        conferences=conferences,
        selected_conf=selected_conf
    )


# --- 2v2 Results page ---
@app.route("/2v2/results")
def results_2v2():
    teams = load_2v2_teams()
    raw_matches = load_2v2_matches()

    # Sort by date descending, treating missing dates as empty so they sort last
    raw_matches.sort(key=lambda m: m["date"] or "", reverse=True)

    fixtures_by_region = OrderedDict()
    for m in raw_matches:
        wid, lid = m["winner_id"], m["loser_id"]
        if wid not in teams or lid not in teams:
            continue
        region = teams[wid]["conference"]
        phase = m["phase"]
        match = m.copy()
        match["winner_name"] = teams[wid]["team_name"]
        match["loser_name"]  = teams[lid]["team_name"]

        fixtures_by_region.setdefault(region, {}) \
                          .setdefault(phase, []) \
                          .append(match)

    phase_order = [
      "Group Stage - Round 1",
      "Group Stage - Round 2",
      "Group Stage - Round 3",
      "Group Stage - Round 4",
      "Quarterfinals",
      "Semi-Finals",
      "Finals"
    ]

    return render_template(
      "2v2_fixtures.html",
      fixtures_by_region=fixtures_by_region,
      phase_order=phase_order
    )

@app.route("/2v2/team/<int:team_id>")
def team_page_2v2(team_id):
    teams = load_2v2_teams()
    if team_id not in teams:
        abort(404)
    info = teams[team_id]

    all_matches = load_2v2_matches()
    matches = []
    for m in all_matches:
        if m["winner_id"] == team_id or m["loser_id"] == team_id:
            wid, lid = m["winner_id"], m["loser_id"]
            if wid in teams and lid in teams:
                m["winner_name"] = teams[wid]["team_name"]
                m["loser_name"]  = teams[lid]["team_name"]
                matches.append(m)

    matches.sort(key=lambda x: x["date"], reverse=True)
    return render_template("2v2_team.html",
                           team_id=team_id,
                           team=info,
                           matches=matches)

# ─── ROUTE: Society Players — list every distinct player in society ─────────
@app.route("/society/players")
def society_players():
    """
    Show a list of all distinct society players (by player_id, player_name),
    sorted alphabetically. Each name links to /player/<pid>.
    """
    data = load_csv_cached()
    players = {}
    for r in data:
        pid = r["player_id"]
        if pid is None:
            continue
        # If a player has multiple rows, just keep one name
        players[pid] = r["player_name"]

    # Convert to a sorted list
    player_list = [{"player_id": pid, "player_name": players[pid]} for pid in players]
    player_list.sort(key=lambda x: x["player_name"].lower())

    return render_template("society_players.html", player_list=player_list)


# ─── ROUTE: Society Results (overview by season & tier) ────────────────────
@app.route("/society/results")
def society_results():
    """
    Show a table organized by season and then tier. For each event,
    display: event_id, event_date, winner (player_name), and best_score.
    """
    data = load_csv_cached()

    # Organize by season → tier → list of events
    results = defaultdict(lambda: defaultdict(list))
    for r in data:
        season = r["season"]
        tier = r["tier"]
        event_id = r["event_id"]
        event_date = r["event_date"]
        # We’ll want to find the winner and best score per event; collect rows by event
        results[season][tier].append(r)

    # For each season & tier, collapse each event’s rows into one summary
    summary = {}
    for season, tiers in results.items():
        summary[season] = {}
        for tier, rows in tiers.items():
            # Group rows by event_id
            by_event = defaultdict(list)
            for r in rows:
                by_event[r["event_id"]].append(r)

            event_summaries = []
            for event_id, ev_rows in by_event.items():
                # Sort ev_rows by rank so the first one is the winner
                ev_rows.sort(key=lambda x: x["rank"])
                winner = ev_rows[0]["player_name"]
                best_score = ev_rows[0]["score"]
                date = ev_rows[0]["event_date"]
                event_summaries.append({
                    "event_id": event_id,
                    "event_date": date,
                    "winner": winner,
                    "best_score": best_score
                })
            # Sort events by date or event_id
            event_summaries.sort(key=lambda x: x["event_date"])
            summary[season][tier] = event_summaries

    # Convert summary into a sorted structure for template
    # e.g. seasons_sorted = [1,2,...]
    seasons_sorted = sorted(summary.keys())

    return render_template("society_results.html",
                           summary=summary,
                           seasons_sorted=seasons_sorted)


# ─── ROUTE: Society Season Overview ────────────────────────────────────────
@app.route("/society/season/<int:season_id>")
def society_season_overview(season_id):
    """
    Show links to the three tiers (Premier, Championship, Challenger) within a season.
    If a given tier has no events, show “No events yet” instead.
    """
    data = load_csv_cached()

    # Find which tiers exist in this season
    tiers_present = set(r["tier"] for r in data if r["season"] == season_id)
    # We’ll always show the three tiers, marking missing ones as empty
    all_tiers = ["1Premier", "2Championship", "3League One", "zzNon-Discord"]
    return render_template("season_overview.html",
                           season_id=season_id,
                           all_tiers=all_tiers,
                           tiers_present=tiers_present)

# Near your existing leaderboard() route, import abort and add:

@app.route("/leaderboard/season/<int:season_id>/tier/<tier_name>")
def show_leaderboard_tier(season_id, tier_name):
    data = load_csv_cached()

    # 1) Figure out which seasons are available
    seasons = sorted({r["season"] for r in data})
    if season_id not in seasons:
        return abort(404)

    # 2) Figure out which tiers exist in that season
    tiers_present = sorted({r["tier"] for r in data if r["season"] == season_id})

    if tier_name not in tiers_present:
        return abort(404)

    # 3) Filter just that season + tier
    subset = [r for r in data if r["season"]==season_id and r["tier"]==tier_name]

    # 4) Aggregate stats exactly like the main leaderboard
    stats = {}
    for r in subset:
        pid = r["player_id"]
        if pid not in stats:
            stats[pid] = {
                "player_id": pid,
                "player_name": r["player_name"],
                "wins": 0, "podiums": 0, "top10s": 0,
                "events_played": 0, "points": 0,
                "event_pr_sum": 0, "event_pr_count": 0
            }
        agg = stats[pid]
        agg["events_played"] += 1
        agg["points"] += r["points"]
        agg["event_pr_sum"] += r["event_pr"]
        agg["event_pr_count"] += 1
        if r["rank"] == 1:         agg["wins"] += 1
        if 1 <= r["rank"] <= 3:    agg["podiums"] += 1
        if 1 <= r["rank"] <= 10:   agg["top10s"] += 1

    leaderboard = []
    for agg in stats.values():
        avg_pr = agg["event_pr_sum"] / agg["event_pr_count"] if agg["event_pr_count"] else 0
        leaderboard.append({
            "player_id": agg["player_id"],
            "player_name": agg["player_name"],
            "points":    agg["points"],
            "wins":      agg["wins"],
            "podiums":   agg["podiums"],
            "top10s":    agg["top10s"],
            "events_played": agg["events_played"],
            "prettyrating":  round(avg_pr)
        })
    leaderboard.sort(key=lambda x:(x["points"], x["wins"]), reverse=True)

    return render_template(
        "leaderboard.html",
        leaderboard=leaderboard,
        seasons_sorted=seasons,
        selected_season=season_id,
        tiers_present=tiers_present,
        selected_tier=tier_name
    )


# ─── ROUTE: Specific Tier within a Season ──────────────────────────────────
@app.route("/society/season/<int:season_id>/tier/<tier_name>")
def society_tier_page(season_id, tier_name):
    """
    List all events in the given season + tier. For each event,
    show event_id, date, winner, best_score, plus a link to /event/<event_id>.
    """
    data = load_csv_cached()
    # Filter rows by season and tier
    filtered = [r for r in data if r["season"] == season_id and r["tier"] == tier_name]
    if not filtered:
        # If no events in this tier, just show a message
        return render_template("tier_page.html",
                               season_id=season_id,
                               tier_name=tier_name,
                               event_summaries=[])
    # Group by event_id
    by_event = defaultdict(list)
    for r in filtered:
        by_event[r["event_id"]].append(r)

    event_summaries = []
    for event_id, rows in by_event.items():
        # Determine winner and best score
        rows.sort(key=lambda x: x["rank"])
        winner = rows[0]["player_name"]
        best_score = rows[0]["score"]
        date = rows[0]["event_date"]
        event_summaries.append({
            "event_id": event_id,
            "event_date": date,
            "winner": winner,
            "best_score": best_score
        })
    # Sort by date
    event_summaries.sort(key=lambda x: x["event_date"])

    return render_template("tier_page.html",
                           season_id=season_id,
                           tier_name=tier_name,
                           event_summaries=event_summaries)

# ─── 1v1: Load Players ─────────────────────────────────────────────────────
_cached_1v1_players = None
def load_1v1_players():
    global _cached_1v1_players
    if _cached_1v1_players is None:
        path = os.path.join(BASE_DIR, "data", "1v1_players.csv")
        players = {}
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                pid = int(r["player_id"])
                players[pid] = {
                    "player_name": r["player_name"],
                    "conference":  r["conference"]
                }
        _cached_1v1_players = players
    return _cached_1v1_players

# ─── 1v1: Load Matches ─────────────────────────────────────────────────────
_cached_1v1_matches = None
def load_1v1_matches():
    global _cached_1v1_matches
    if _cached_1v1_matches is None:
        path = os.path.join(BASE_DIR, "data", "1v1_matches.csv")
        ms = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                # parse date or None
                date = (r.get("date","") or "").strip() or None
                # parse point_diff or None
                try:
                    diff = int(r.get("point_diff",""))
                except:
                    diff = None
                ms.append({
                    "match_id":   int(r["match_id"]),
                    "season":     int(r["season"]),
                    "phase":      r["phase"],
                    "date":       date,
                    "winner_id":  int(r["winner_id"]),
                    "loser_id":   int(r["loser_id"]),
                    "point_diff": diff
                })
        _cached_1v1_matches = ms
    return _cached_1v1_matches

# ─── 1v1 Players List ──────────────────────────────────────────────────────
@app.route("/1v1/players")
def players_1v1():
    players = load_1v1_players()
    lst = [{"player_id":pid, **info} for pid,info in players.items()]
    lst.sort(key=lambda x: x["player_name"].lower())
    return render_template("1v1_players.html", players=lst)

# ─── 1v1 Player Profile ───────────────────────────────────────────────────
@app.route("/1v1/player/<int:pid>")
def player_page_1v1(pid):
    players = load_1v1_players()
    if pid not in players:
        abort(404)
    info = players[pid]

    # find all matches for this player
    matches = [m.copy() for m in load_1v1_matches()
               if m["winner_id"]==pid or m["loser_id"]==pid]
    # attach names
    for m in matches:
        pls = load_1v1_players()
        m["winner_name"] = pls[m["winner_id"]]["player_name"]
        m["loser_name"]  = pls[m["loser_id"]]["player_name"]
        # outcome
        m["result"] = "Win" if m["winner_id"]==pid else "Loss"
    # sort by date desc, fixtures last
    matches.sort(key=lambda m: m["date"] or "", reverse=True)

    # compute summary stats only for completed matches
    stats = {"wins":0,"losses":0,"played":0,"point_diff":0}
    for m in matches:
        if m["point_diff"] is None:
            continue
        stats["played"] += 1
        stats["point_diff"] += m["point_diff"] if m["winner_id"]==pid else -m["point_diff"]
        if m["winner_id"]==pid:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

    return render_template("1v1_player.html",
                           player_id=pid,
                           player_name=info["player_name"],
                           conference=info["conference"],
                           matches=matches,
                           stats=stats)

# ─── 1v1 Leaderboard ──────────────────────────────────────────────────────
@app.route("/1v1/leaderboard")
@app.route("/1v1/leaderboard/conference/<conf_name>")
def leaderboard_1v1(conf_name=None):
    players = load_1v1_players()
    matches = load_1v1_matches()

    # conference list
    conferences = sorted({p["conference"] for p in players.values()})
    selected_conf = conf_name if conf_name in conferences else conferences[0]

    # init stats for players in that conference
    stats = {pid:{"wins":0,"losses":0,"played":0,"point_diff":0}
             for pid,p in players.items() if p["conference"]==selected_conf}

    # tally only completed matches
    for m in matches:
        if m["point_diff"] is None: continue
        wid,lid,d = m["winner_id"], m["loser_id"], m["point_diff"]
        if wid in stats:
            stats[wid]["wins"] += 1
            stats[wid]["played"] += 1
            stats[wid]["point_diff"] += d
        if lid in stats:
            stats[lid]["losses"] += 1
            stats[lid]["played"] += 1
            stats[lid]["point_diff"] -= d

    board = []
    for pid, s in stats.items():
        board.append({
            "player_id": pid,
            "player_name": players[pid]["player_name"],
            "wins": s["wins"],
            "losses": s["losses"],
            "matches_played": s["played"],
            "point_diff": s["point_diff"]
        })
    board.sort(key=lambda x:(x["wins"], x["point_diff"]), reverse=True)

    return render_template("1v1_leaderboard.html",
                           leaderboard=board,
                           conferences=conferences,
                           selected_conf=selected_conf)

# ─── 1v1 Fixtures (Results) ──────────────────────────────────────────────
@app.route("/1v1/results")
def results_1v1():
    players = load_1v1_players()
    raw_matches = load_1v1_matches()
    raw_matches.sort(key=lambda m: m["date"] or "", reverse=True)

    fixtures_by_region = OrderedDict()
    for m in raw_matches:
        pid = m["winner_id"]
        if pid not in players: continue
        region = players[pid]["conference"]
        phase = m["phase"]
        match = m.copy()
        match["winner_name"] = players[m["winner_id"]]["player_name"]
        match["loser_name"]  = players[m["loser_id"]]["player_name"]
        fixtures_by_region.setdefault(region, {}) \
                          .setdefault(phase, []) \
                          .append(match)

    phase_order = [
      "Group Stage - Round 1",
      "Group Stage - Round 2",
      "Group Stage - Round 3",
      "Group Stage - Round 4",
      "Quarterfinals",
      "Semi-Finals",
      "Finals"
    ]

    return render_template("1v1_fixtures.html",
                            fixtures_by_region=fixtures_by_region,
                            phase_order=phase_order)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
