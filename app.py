import csv, os, threading
from flask import Flask, render_template, abort, redirect, url_for, request
from collections import defaultdict, OrderedDict

app = Flask(__name__)

# ─── HELPER FUNCTIONS ──────────────────────────────────────────────────────

def safe_int(value, default=0):
    """Safely convert value to int, returning default if conversion fails"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def safe_string(value):
    """Safely convert value to string, returning empty string if None"""
    return (value or "").strip()

def calculate_player_stats(season_rows):
    """Calculate aggregated stats for players from season data"""
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
    
    # Convert to leaderboard format
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
    return leaderboard

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
                    row["player_id"] = safe_int(row.get("player_id"))
                    row["player_name"] = safe_string(row.get("player_name"))
                    row["event_id"] = safe_string(row.get("event_id"))
                    row["event_date"] = safe_string(row.get("event_date"))
                    row["score"] = safe_int(row.get("score"))
                    row["rank"] = safe_int(row.get("rank"))
                    row["points"] = safe_int(row.get("points"))
                    row["event_pr"] = safe_int(row.get("event_pr"))
                    row["season"] = safe_int(row.get("season"))
                    row["tier"] = safe_string(row.get("tier"))
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

    # Filter data to only rows in the selected season
    season_rows = [r for r in data if r["season"] == selected_season]

    # Calculate aggregated stats per player for this season
    leaderboard = calculate_player_stats(season_rows)

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


# ─── 2v2 Teams Data ────────────────────────────────────────────────────────
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

# ─── 2v2 Matches Data ──────────────────────────────────────────────────────
_cached_matches = None
def load_2v2_matches():
    global _cached_matches
    if _cached_matches is None:
        path = os.path.join(BASE_DIR, "data", "2v2_matches.csv")
        ms = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                date = safe_string(r.get("date")) or None
                ms.append({
                    "match_id": safe_int(r.get("match_id")),
                    "season": safe_int(r.get("season")),
                    "phase": safe_string(r.get("phase")),
                    "date": date,
                    "winner_id": safe_int(r.get("winner_id")),
                    "loser_id": safe_int(r.get("loser_id")),
                    "point_diff": safe_int(r.get("point_diff")) if r.get("point_diff", "").strip() else None
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


# ─── ROUTE: Head-to-Head Checker ──────────────────────────────────────────
@app.route("/head-to-head", methods=["GET", "POST"])
def head_to_head():
    """
    Compare two players' performances in the same events.
    Shows which events they both played in and who would have won between them.
    """
    data = load_csv_cached()
    
    # Get all unique players for dropdown
    all_players = sorted(set(safe_string(r["player_name"]) for r in data if r["player_name"]))
    
    if request.method == "GET":
        # Show the form
        return render_template("head_to_head.html", all_players=all_players)
    
    # POST - process the form
    player1_name = request.form.get("player1", "").strip()
    player2_name = request.form.get("player2", "").strip()
    
    if not player1_name or not player2_name:
        return render_template("head_to_head.html", 
                             all_players=all_players,
                             error="Please enter both player names.")
    
    # Find all events where both players participated
    player1_events = {}
    player2_events = {}
    
    for r in data:
        player_name = safe_string(r["player_name"]).lower()
        if player_name == player1_name.lower():
            event_key = f"{r['season']}-{r['event_id']}"
            player1_events[event_key] = r
        elif player_name == player2_name.lower():
            event_key = f"{r['season']}-{r['event_id']}"
            player2_events[event_key] = r
    
    # Find common events
    common_events = []
    player1_wins = 0
    player2_wins = 0
    ties = 0
    
    for event_key in player1_events:
        if event_key in player2_events:
            p1_data = player1_events[event_key]
            p2_data = player2_events[event_key]
            
            # Determine winner (lower score wins in golf)
            p1_score = safe_int(p1_data["score"])
            p2_score = safe_int(p2_data["score"])
            
            if p1_score < p2_score:
                winner = player1_name
                player1_wins += 1
            elif p2_score < p1_score:
                winner = player2_name
                player2_wins += 1
            else:
                winner = "Tie"
                ties += 1
            
            common_events.append({
                "season": p1_data["season"],
                "event_id": p1_data["event_id"],
                "event_date": p1_data["event_date"],
                "player1_name": p1_data["player_name"],
                "player1_score": p1_score,
                "player1_rank": safe_int(p1_data["rank"]),
                "player1_tier": p1_data["tier"],
                "player2_name": p2_data["player_name"],
                "player2_score": p2_score,
                "player2_rank": safe_int(p2_data["rank"]),
                "player2_tier": p2_data["tier"],
                "winner": winner
            })
    
    # Sort by season and event date
    common_events.sort(key=lambda x: (x["season"], x["event_date"]))
    
    summary = {
        "total_events": len(common_events),
        "player1_wins": player1_wins,
        "player2_wins": player2_wins,
        "ties": ties
    }
    
    return render_template("head_to_head.html", 
                         all_players=all_players,
                         player1_name=player1_name,
                         player2_name=player2_name,
                         common_events=common_events,
                         summary=summary)

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

    # Filter just that season + tier
    subset = [r for r in data if r["season"]==season_id and r["tier"]==tier_name]

    # Calculate aggregated stats per player
    leaderboard = calculate_player_stats(subset)

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
                date = safe_string(r.get("date")) or None
                ms.append({
                    "match_id": safe_int(r.get("match_id")),
                    "season": safe_int(r.get("season")),
                    "phase": safe_string(r.get("phase")),
                    "date": date,
                    "winner_id": safe_int(r.get("winner_id")),
                    "loser_id": safe_int(r.get("loser_id")),
                    "point_diff": safe_int(r.get("point_diff")) if r.get("point_diff", "").strip() else None
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

# ═══════════════════════════════════════════════════════════════════════════════
# ─── TEAMS FUNCTIONALITY ──────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

def load_team_data():
    """Load team data from team_data.csv with team names"""
    team_assignments = []
    teams = {}
    
    try:
        with open(os.path.join(BASE_DIR, "data", "team_data.csv"), newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Skip empty rows
                if not row.get("player_id") or row.get("player_id") == "":
                    continue
                    
                # Parse team_data.csv row with new team_name column
                player_id = safe_int(row.get("player_id"))
                team_id = safe_int(row.get("team_id"))
                team_name = safe_string(row.get("team_name"))
                opponent_player_id = row.get("event_1v1_opponent", "")
                team_opponent = row.get("team_opponent", "")
                event = safe_string(row.get("event"))
                season = safe_int(row.get("season"))
                
                # Convert team_opponent to int if it's not "Society Avg"
                if team_opponent == "Society Avg":
                    team_opponent_id = None
                    opponent_player_id = "Avg"
                else:
                    team_opponent_id = safe_int(team_opponent) if team_opponent else None
                    opponent_player_id = safe_int(opponent_player_id) if opponent_player_id.isdigit() else opponent_player_id
                
                team_assignments.append({
                    "player_id": player_id,
                    "team_id": team_id,
                    "team_name": team_name,
                    "opponent_player_id": opponent_player_id,
                    "team_opponent": team_opponent_id,
                    "event": event,
                    "season": season
                })
                
                # Build teams dictionary with actual team names
                if team_id not in teams:
                    teams[team_id] = {
                        "team_id": team_id,
                        "team_name": team_name,
                        "season": season
                    }
                
    except (FileNotFoundError, ValueError, KeyError) as e:
        print(f"Error loading team data: {e}")
        return {}, []
    
    return teams, team_assignments

def calculate_team_standings(season):
    """Calculate team standings based on 1v1 match results"""
    teams, team_assignments = load_team_data()
    league_data = load_csv_cached()
    
    # Filter to specific season
    season_assignments = [ta for ta in team_assignments if ta["season"] == season]
    season_league_data = [ld for ld in league_data if ld["season"] == season]
    
    # Create player score lookup by event
    player_scores = {}
    for ld in season_league_data:
        event_id = ld["event_id"]
        player_id = ld["player_id"]
        if event_id not in player_scores:
            player_scores[event_id] = {}
        player_scores[event_id][player_id] = ld["score"]
    
    # Calculate society average by event
    society_averages = {}
    for event_id in player_scores:
        scores = list(player_scores[event_id].values())
        if scores:
            society_averages[event_id] = int(sum(scores) / len(scores))  # rounded down
    
    # Initialize team standings
    team_standings = {}
    for assignment in season_assignments:
        team_id = assignment["team_id"]
        if team_id not in team_standings:
            # Get team info if available, otherwise use default
            team_info = teams.get(team_id, {"team_name": f"Team {team_id}", "season": season})
            team_standings[team_id] = {
                "team_name": team_info["team_name"],
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "points": 0.0,
                "matches_played": 0,
                "team_wins": 0,
                "team_losses": 0,
                "team_ties": 0,
                "team_matches_played": 0,
                "points_by_event": {}  # Track points per event for team match calculation
            }
    
    # Process 1v1 matches
    processed_matches = set()
    
    for assignment in season_assignments:
        player1_id = assignment["player_id"]
        team1_id = assignment["team_id"]
        event = assignment["event"]
        
        # Initialize event tracking for this team if needed
        if event not in team_standings[team1_id]["points_by_event"]:
            team_standings[team1_id]["points_by_event"][event] = 0.0
        
        # Find corresponding opponent assignment
        opponent_assignments = [
            ta for ta in season_assignments 
            if ta["event"] == event and ta["player_id"] == assignment["opponent_player_id"]
        ] if assignment["opponent_player_id"] != "Avg" else []
        
        if assignment["opponent_player_id"] == "Avg":
            # Playing against society average
            event_id = event
            player1_score = player_scores.get(event_id, {}).get(player1_id)
            avg_score = society_averages.get(event_id, 0)
            
            if player1_score is not None:
                if player1_score < avg_score:  # Lower score wins in golf
                    team_standings[team1_id]["wins"] += 1
                    team_standings[team1_id]["points"] += 1.0
                    team_standings[team1_id]["points_by_event"][event] += 1.0
                elif player1_score > avg_score:
                    team_standings[team1_id]["losses"] += 1
                else:
                    team_standings[team1_id]["ties"] += 1
                    team_standings[team1_id]["points"] += 0.5
                    team_standings[team1_id]["points_by_event"][event] += 0.5
                team_standings[team1_id]["matches_played"] += 1
            else:
                # Player DNP vs Society Average = Loss
                team_standings[team1_id]["losses"] += 1
                team_standings[team1_id]["matches_played"] += 1
            
        elif opponent_assignments:
            opponent = opponent_assignments[0]
            player2_id = opponent["player_id"]
            team2_id = opponent["team_id"]
            
            # Only process each match once by ensuring we process it from the lower player ID
            # This prevents double counting since both players have assignments pointing to each other
            if player1_id >= player2_id:
                continue
            
            # Ensure opponent team exists in standings (safety check)
            if team2_id not in team_standings:
                continue
            
            # Initialize event tracking for both teams if needed
            if event not in team_standings[team2_id]["points_by_event"]:
                team_standings[team2_id]["points_by_event"][event] = 0.0
            
            event_id = event
            player1_score = player_scores.get(event_id, {}).get(player1_id)
            player2_score = player_scores.get(event_id, {}).get(player2_id)
            
            # Determine match result
            if player1_score is None and player2_score is None:
                # Both DNP - tie
                team_standings[team1_id]["ties"] += 1
                team_standings[team1_id]["points"] += 0.5
                team_standings[team1_id]["points_by_event"][event] += 0.5
                team_standings[team1_id]["matches_played"] += 1
                team_standings[team2_id]["ties"] += 1
                team_standings[team2_id]["points"] += 0.5
                team_standings[team2_id]["points_by_event"][event] += 0.5
                team_standings[team2_id]["matches_played"] += 1
            elif player1_score is None:
                # Player 1 DNP - Player 2 wins
                team_standings[team1_id]["losses"] += 1
                team_standings[team1_id]["matches_played"] += 1
                team_standings[team2_id]["wins"] += 1
                team_standings[team2_id]["points"] += 1.0
                team_standings[team2_id]["points_by_event"][event] += 1.0
                team_standings[team2_id]["matches_played"] += 1
            elif player2_score is None:
                # Player 2 DNP - Player 1 wins
                team_standings[team1_id]["wins"] += 1
                team_standings[team1_id]["points"] += 1.0
                team_standings[team1_id]["points_by_event"][event] += 1.0
                team_standings[team1_id]["matches_played"] += 1
                team_standings[team2_id]["losses"] += 1
                team_standings[team2_id]["matches_played"] += 1
            else:
                # Both played
                if player1_score < player2_score:  # Lower score wins
                    team_standings[team1_id]["wins"] += 1
                    team_standings[team1_id]["points"] += 1.0
                    team_standings[team1_id]["points_by_event"][event] += 1.0
                    team_standings[team1_id]["matches_played"] += 1
                    team_standings[team2_id]["losses"] += 1
                    team_standings[team2_id]["matches_played"] += 1
                elif player1_score > player2_score:
                    team_standings[team1_id]["losses"] += 1
                    team_standings[team1_id]["matches_played"] += 1
                    team_standings[team2_id]["wins"] += 1
                    team_standings[team2_id]["points"] += 1.0
                    team_standings[team2_id]["points_by_event"][event] += 1.0
                    team_standings[team2_id]["matches_played"] += 1
                else:
                    # Tie
                    team_standings[team1_id]["ties"] += 1
                    team_standings[team1_id]["points"] += 0.5
                    team_standings[team1_id]["points_by_event"][event] += 0.5
                    team_standings[team1_id]["matches_played"] += 1
                    team_standings[team2_id]["ties"] += 1
                    team_standings[team2_id]["points"] += 0.5
                    team_standings[team2_id]["points_by_event"][event] += 0.5
                    team_standings[team2_id]["matches_played"] += 1
    
    # Calculate team match records based on points per event
    # Season 2: 4 players per team, so 2.5+ points = win, 2.0 = tie, 1.5- = loss
    # Season 3: 5 players per team, so 3.0+ points = win, 2.5 = tie, 2.0- = loss
    
    # Determine team size for this season
    if season == 2:
        win_threshold = 2.5
        tie_threshold = 2.0
    else:  # Season 3 and beyond
        win_threshold = 3.0
        tie_threshold = 2.5
    
    # Get all unique events for this season
    all_events = set()
    for team_id, stats in team_standings.items():
        all_events.update(stats["points_by_event"].keys())
    
    # Process each event to determine team match results
    for event in all_events:
        # Get all teams and their points for this event
        teams_in_event = []
        for team_id, stats in team_standings.items():
            event_points = stats["points_by_event"].get(event, 0.0)
            if event_points > 0 or any(ta["event"] == event and ta["team_id"] == team_id for ta in season_assignments):
                teams_in_event.append((team_id, event_points))
        
        # Determine team match results for this event
        for team_id, points in teams_in_event:
            if points >= win_threshold:
                team_standings[team_id]["team_wins"] += 1
            elif points >= tie_threshold:
                team_standings[team_id]["team_ties"] += 1
            else:
                team_standings[team_id]["team_losses"] += 1
            team_standings[team_id]["team_matches_played"] += 1
    
    # Sort by points (descending), then by wins
    sorted_standings = sorted(
        [(team_id, stats) for team_id, stats in team_standings.items()],
        key=lambda x: (x[1]["points"], x[1]["wins"]),
        reverse=True
    )
    
    return sorted_standings

@app.route("/teams")
def teams_redirect():
    """Redirect to latest season"""
    teams, team_assignments = load_team_data()
    seasons = sorted({ta["season"] for ta in team_assignments})
    if not seasons:
        abort(404)
    return redirect(url_for('show_teams_overview', season_id=seasons[-1]))

@app.route("/teams/standings/season/<int:season_id>")
def show_teams_overview(season_id=None):
    """Show team standings overview"""
    teams, team_assignments = load_team_data()
    
    # Get available seasons
    seasons = sorted({ta["season"] for ta in team_assignments})
    if not seasons:
        abort(404)
    
    if season_id not in seasons:
        abort(404)
    
    standings = calculate_team_standings(season_id)
    
    return render_template("teams_overview.html", 
                         standings=standings,
                         selected_season=season_id,
                         seasons=seasons)

@app.route("/teams/team/<int:team_id>/season/<int:season_id>")
def team_detail(team_id, season_id):
    """Show detailed team page with roster and match history"""
    teams, team_assignments = load_team_data()
    league_data = load_csv_cached()
    
    if team_id not in teams:
        abort(404)
    
    # Get available seasons for this team
    team_seasons = sorted({ta["season"] for ta in team_assignments if ta["team_id"] == team_id})
    if not team_seasons:
        abort(404)
    
    if season_id not in team_seasons:
        abort(404)
    
    team_info = teams[team_id]
    
    # Get team roster for the season
    roster_assignments = [ta for ta in team_assignments if ta["team_id"] == team_id and ta["season"] == season_id]
    
    # Get unique players
    roster_player_ids = {ta["player_id"] for ta in roster_assignments}
    
    # Get player names from league data - INCLUDING opponents from other teams
    player_names = {}
    for ld in league_data:
        player_names[ld["player_id"]] = ld["player_name"]
    
    roster = [{"player_id": pid, "player_name": player_names.get(pid, f"Player {pid}")} 
              for pid in roster_player_ids]
    
    # Get team's match history
    matches = []
    for assignment in roster_assignments:
        player_id = assignment["player_id"]
        event = assignment["event"]
        opponent_player_id = assignment["opponent_player_id"]
        team_opponent_id = assignment.get("team_opponent")
        
        # Determine opponent info
        if opponent_player_id == "Avg":
            opponent_info = "Society Average"
            opponent_score = None  # We'll calculate society average later if needed
        else:
            # Get opponent's name from league data
            opponent_name = player_names.get(opponent_player_id, f"Player {opponent_player_id}")
            if team_opponent_id:
                # Get opponent team name
                opponent_team_name = "Unknown Team"
                for tid, tinfo in teams.items():
                    if tid == team_opponent_id:
                        opponent_team_name = tinfo["team_name"]
                        break
                opponent_info = f"{opponent_name} ({opponent_team_name})"
            else:
                opponent_info = opponent_name
            
            # Get opponent's score for this event
            opponent_score = None
            for ld in league_data:
                if ld["player_id"] == opponent_player_id and ld["event_id"] == event:
                    opponent_score = ld["score"]
                    break
        
        # Get player's score for this event
        player_score = None
        for ld in league_data:
            if ld["player_id"] == player_id and ld["event_id"] == event:
                player_score = ld["score"]
                break
        
        # Calculate match result
        result = "DNP"
        if player_score is not None:
            if opponent_player_id == "Avg":
                # Calculate society average for this event (rounded down)
                event_scores = [ld["score"] for ld in league_data if ld["event_id"] == event and ld["score"] is not None]
                if event_scores:
                    society_avg = int(sum(event_scores) / len(event_scores))  # Rounded down to nearest integer
                    opponent_score = society_avg
                    # In golf, LOWER score is BETTER
                    if player_score < society_avg:
                        result = "Win"
                    elif player_score > society_avg:
                        result = "Loss"
                    else:
                        result = "Tie"
                else:
                    result = "vs Society Avg"
                    opponent_score = None
            elif opponent_score is not None:
                # In golf, LOWER score is BETTER
                if player_score < opponent_score:
                    result = "Win"
                elif player_score > opponent_score:
                    result = "Loss"
                else:
                    result = "Tie"
            else:
                result = "Win"  # Opponent DNP, player played = player wins
        else:
            # Player DNP
            if opponent_player_id == "Avg":
                # Calculate society average for this event (rounded down)
                event_scores = [ld["score"] for ld in league_data if ld["event_id"] == event and ld["score"] is not None]
                if event_scores:
                    opponent_score = int(sum(event_scores) / len(event_scores))  # Rounded down to nearest integer
                else:
                    opponent_score = None
                result = "Loss"  # DNP vs Society Avg = Loss
            elif opponent_score is not None:
                result = "Loss"  # Opponent played, player DNP = player loses
            else:
                result = "DNP"  # Both players DNP = DNP
        
        matches.append({
            "event": event,
            "player_id": player_id,
            "player_name": player_names.get(player_id, f"Player {player_id}"),
            "opponent": opponent_info,
            "player_score": player_score,
            "opponent_score": opponent_score,
            "result": result
        })
    
    # Calculate team record
    standings = calculate_team_standings(season_id)
    team_record = None
    for team_id_standing, stats in standings:
        if team_id_standing == team_id:
            team_record = stats
            break
    
    return render_template("team_detail.html",
                         team=team_info,
                         roster=roster,
                         matches=matches,
                         team_record=team_record,
                         selected_season=season_id,
                         seasons=team_seasons)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
