from datetime import UTC, date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from fantasy_helper.domain.evaluations import EvaluationPoint, SubjectType
from fantasy_helper.domain.league import (
    LeagueFormat,
    LeagueRules,
    LeagueSettings,
    LineupMode,
    Platform,
    RosterSlot,
    ScoringBonus,
    ScoringRules,
    ScoringType,
)
from fantasy_helper.domain.player import Position
from fantasy_helper.domain.recommendations import (
    CallInputs,
    CallOutcome,
    CallType,
    Claim,
    MissCause,
    MissCauseSource,
    OutcomeWindow,
    Producer,
    Recommendation,
)
from fantasy_helper.domain.reports import Card, CardKind, ReportDocument, ReportKind
from fantasy_helper.domain.stats import Stat
from fantasy_helper.domain.takes import Direction, Horizon, Take
from fantasy_helper.domain.trades import Trade, TradeSide
from fantasy_helper.domain.users import EspnCredentials, User

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def make_take(**overrides: object) -> Take:
    fields: dict[str, object] = {
        "user_id": uuid4(),
        "player_id": uuid4(),
        "created_at": NOW,
        "direction": Direction.BEAT,
        "horizon": Horizon.DAYS_14,
        "conviction": 4,
        "text": "Grimes is a real rotation player",
    }
    return Take.model_validate(fields | overrides)


def make_recommendation(**overrides: object) -> Recommendation:
    fields: dict[str, object] = {
        "created_at": NOW,
        "league_id": uuid4(),
        "user_id": uuid4(),
        "platform": Platform.ESPN,
        "format": LeagueFormat.POINTS,
        "call_type": CallType.ADD_DROP,
        "claim": Claim(text="Add Grimes, drop the 13th man"),
        "expected_value": 18.0,
        "confidence": 0.7,
        "baseline_call": Claim(text="Add the top free agent by projection"),
        "inputs": CallInputs(projection_snapshot_ids=(uuid4(),), settings_version=1),
        "producer": Producer(agent_setup="baseline", models=("m",), prompt_version="p1"),
        "outcome_window": OutcomeWindow(start=NOW, end=NOW + timedelta(days=7)),
        "first_tip_utc": NOW + timedelta(hours=7),
    }
    return Recommendation.model_validate(fields | overrides)


def make_rules(**overrides: object) -> LeagueRules:
    fields: dict[str, object] = {
        "team_count": 10,
        "scoring": ScoringRules(
            scoring_type=ScoringType.POINTS, weights={Stat.POINTS: 1, Stat.ASSISTS: 4}
        ),
        "lineup_mode": LineupMode.DAILY,
        "roster_slots": (
            RosterSlot(slot="G", count=1, eligible_positions=(Position.PG, Position.SG)),
        ),
    }
    return LeagueRules.model_validate(fields | overrides)


def make_settings(**overrides: object) -> LeagueSettings:
    return LeagueSettings(
        league_id=uuid4(), version=1, effective_from=NOW, rules=make_rules(**overrides)
    )


class TestTimeAndImmutability:
    def test_naive_datetimes_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timezone-aware"):
            make_take(created_at=datetime(2026, 11, 2, 12, 0))  # noqa: DTZ001

    def test_aware_datetimes_are_normalized_to_utc(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        take = make_take(created_at=datetime(2026, 11, 2, 7, 0, tzinfo=eastern))
        assert take.created_at == NOW
        assert take.created_at.tzinfo == UTC

    def test_models_are_frozen(self) -> None:
        take = make_take()
        with pytest.raises(ValidationError):
            take.conviction = 1  # type: ignore[misc]

    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_take(mood="bullish")


class TestRegistry:
    def test_call_logged_before_tip_off_is_accepted(self) -> None:
        assert make_recommendation().first_tip_utc > NOW

    def test_call_logged_at_or_after_tip_off_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="before tip-off"):
            make_recommendation(first_tip_utc=NOW)

    def test_outcome_window_must_end_after_it_starts(self) -> None:
        with pytest.raises(ValidationError):
            OutcomeWindow(start=NOW, end=NOW)

    def test_confidence_is_a_probability(self) -> None:
        with pytest.raises(ValidationError):
            make_recommendation(confidence=1.2)

    def test_outcome_lift_and_miss_cause_rules(self) -> None:
        outcome = CallOutcome(
            recommendation_id=uuid4(),
            graded_at=NOW,
            followed=True,
            metric_value=12.0,
            baseline_value=20.0,
            miss_cause=MissCause.VARIANCE,
            miss_cause_source=MissCauseSource.RULE,
        )
        assert outcome.lift == -8.0
        assert not MissCause.VARIANCE.fixable
        assert MissCause.DATA_ERROR.fixable
        with pytest.raises(ValidationError, match="together"):
            CallOutcome(
                recommendation_id=uuid4(),
                graded_at=NOW,
                followed=None,
                metric_value=0,
                baseline_value=0,
                miss_cause=MissCause.VARIANCE,
            )


class TestTakes:
    def test_window_end_follows_the_horizon(self) -> None:
        assert make_take().window_end == NOW + timedelta(days=14)

    @pytest.mark.parametrize("conviction", [0, 6])
    def test_conviction_is_one_to_five(self, conviction: int) -> None:
        with pytest.raises(ValidationError):
            make_take(conviction=conviction)

    def test_direction_sign(self) -> None:
        assert Direction.BEAT.sign == 1
        assert Direction.TRAIL.sign == -1


class TestLeagueSettings:
    def test_hash_ignores_version_and_date(self) -> None:
        rules = make_rules()
        a = LeagueSettings(league_id=uuid4(), version=1, effective_from=NOW, rules=rules)
        b = LeagueSettings(
            league_id=a.league_id, version=2, effective_from=NOW + timedelta(days=3), rules=rules
        )
        assert a.content_hash == b.content_hash

    def test_hash_changes_when_scoring_changes(self) -> None:
        a = make_settings()
        b = make_settings(
            scoring=ScoringRules(
                scoring_type=ScoringType.POINTS, weights={Stat.POINTS: 1, Stat.ASSISTS: 1}
            )
        )
        assert a.content_hash != b.content_hash

    def test_hash_ignores_position_order(self) -> None:
        a = make_settings()
        b = make_settings(
            roster_slots=(
                RosterSlot(slot="G", count=1, eligible_positions=(Position.SG, Position.PG)),
            )
        )
        assert a.content_hash == b.content_hash

    def test_format(self) -> None:
        assert make_settings().format is LeagueFormat.POINTS
        assert make_settings(lineup_mode=LineupMode.LOCK_IN).format is LeagueFormat.LOCK_IN

    def test_points_league_needs_weights(self) -> None:
        with pytest.raises(ValidationError, match="weights"):
            ScoringRules(scoring_type=ScoringType.POINTS)


class TestTrades:
    def test_a_team_appears_once(self) -> None:
        side = TradeSide(team_external_id="3", players_received=(uuid4(),))
        with pytest.raises(ValidationError, match="each team"):
            Trade(league_id=uuid4(), executed_at=NOW, sides=(side, side))

    def test_a_player_goes_to_one_team(self) -> None:
        player = uuid4()
        with pytest.raises(ValidationError, match="two teams"):
            Trade(
                league_id=uuid4(),
                executed_at=NOW,
                sides=(
                    TradeSide(team_external_id="1", players_received=(player,)),
                    TradeSide(team_external_id="2", players_received=(player,)),
                ),
            )


def test_evaluation_point_totals() -> None:
    point = EvaluationPoint(
        subject_type=SubjectType.TRADE,
        subject_id=uuid4(),
        as_of=date(2026, 11, 9),
        realized=40.0,
        projected_remaining=55.0,
        baseline_expected_total=110.0,
    )
    assert point.expected_total == 95.0
    assert point.lift == -15.0


def test_report_card_ids_are_unique() -> None:
    card = Card(id="move-1", kind=CardKind.MOVE, title="Add Grimes", body="…")
    with pytest.raises(ValidationError, match="unique"):
        ReportDocument(
            user_id=uuid4(),
            league_id=uuid4(),
            kind=ReportKind.DAILY,
            for_date=date(2026, 11, 2),
            generated_at=NOW,
            cards=(card, card),
        )


def test_user_time_zone_must_exist() -> None:
    with pytest.raises(ValidationError, match="time zone"):
        User(email="adam@example.com", display_name="Adam", timezone="Mars/Olympus_Mons")


def test_espn_cookies_are_hidden_in_repr() -> None:
    creds = EspnCredentials(espn_s2=SecretStr("s2-cookie-value"), swid=SecretStr("{SWID}"))
    assert "s2-cookie-value" not in repr(creds)
    assert "{SWID}" not in repr(creds)


def test_named_slots_know_their_positions() -> None:
    util = RosterSlot.named("UTIL", 3)
    assert util.starting
    assert len(util.eligible_positions) == 5
    assert not RosterSlot.named("BENCH", 4).starting
    assert RosterSlot.named("G", 1).eligible_positions == (Position.PG, Position.SG)
    with pytest.raises(ValueError, match="unknown roster slot"):
        RosterSlot.named("BE", 4)


def test_bonuses_change_the_rules_hash() -> None:
    plain = make_rules()
    with_bonus = make_rules(
        scoring=ScoringRules(
            scoring_type=ScoringType.POINTS,
            weights={Stat.POINTS: 1, Stat.ASSISTS: 4},
            bonuses=(ScoringBonus(stat=Stat.POINTS, threshold=40, points=2),),
        )
    )
    assert plain.content_hash != with_bonus.content_hash
