"""Teams and players named in a claim; never a guess (design section 4.2.3)."""

import unicodedata

import pytest
from helpers import DL, generated, match_context

from pundit.agents.analyst_rules import analyze_text
from pundit.agents.context import player_aliases, team_aliases
from pundit.agents.contracts import MatchContext, PlayerRef
from pundit.agents.entities import Mention, find_entities
from pundit.agents.language import Text

HAR, KES = "harbor_lions", "kestrel_athletic"


def mentions(text: str, ctx: MatchContext | None = None) -> list[Mention]:
    return find_entities(Text.of(text), ctx or match_context())


def one(text: str) -> Mention:
    found = mentions(text)
    assert len(found) == 1, found
    return found[0]


@pytest.mark.parametrize(
    ("text", "team"),
    [
        ("Harbor", HAR),
        ("HAR", HAR),
        ("har", HAR),
        ("Harbor Lions", HAR),
        ("Harbor Lions FC", HAR),
        ("Lions", HAR),
        ("Đội nhà", HAR),
        ("đội chủ nhà", HAR),
        ("the home side", HAR),
        ("Kestrel", KES),
        ("KES", KES),
        ("Kestrel Athletic", KES),
        ("đội khách", KES),
        ("the visitors", KES),
    ],
)
def test_teams(text: str, team: str) -> None:
    mention = one(text)
    assert (mention.kind, mention.team) == ("team", team)


@pytest.mark.parametrize("text", ["hai đội", "cả hai đội", "both teams", "the two sides"])
def test_both_teams(text: str) -> None:
    assert one(text).kind == "both_teams"


@pytest.mark.parametrize(
    ("text", "player"),
    [
        ("Calder", "harbor_lions-09"),
        ("T. Calder", "harbor_lions-09"),
        ("Tobias Calder", "harbor_lions-09"),
        ("Gia Bảo", "harbor_lions-06"),
        ("gia bao", "harbor_lions-06"),
        ("PHẠM GIA BẢO", "harbor_lions-06"),
        (unicodedata.normalize("NFD", "Gia Bảo"), "harbor_lions-06"),
        ("Minh Khôi", "kestrel_athletic-04"),
        ("Mensah-Adu", "harbor_lions-05"),
        ("Ribas", "kestrel_athletic-09"),
    ],
)
def test_players(text: str, player: str) -> None:
    mention = one(text)
    assert (mention.kind, mention.player, mention.team) == ("player", player, player[:-3])


def test_single_vietnamese_syllables_are_not_names() -> None:
    assert mentions("Bảo ghi bàn") == []
    assert mentions("HAR có bao nhiêu đường chuyền") == [mentions("HAR")[0]]


def test_aliases() -> None:
    meta = generated(*DL).meta
    assert team_aliases(meta.team_info(HAR)) == (
        "Harbor Lions FC",
        "HAR",
        "Harbor Lions",
        "Harbor",
        "Lions",
    )
    assert player_aliases(meta.players["harbor_lions-09"]) == (
        "Tobias Calder",
        "T. Calder",
        "Calder",
    )
    # A Vietnamese name: no single syllable ("Bảo" would fold into "bao nhiêu")
    assert player_aliases(meta.players["harbor_lions-06"]) == ("Phạm Gia Bảo", "Gia Bảo")


def test_generic_club_words_are_not_aliases() -> None:
    meta = generated("comeback_var", 7).meta
    iron = team_aliases(meta.team_info("ironvale_united"))
    assert "United" not in iron and "Ironvale" in iron and "IRO" in iron


def with_second_calder(ctx: MatchContext) -> MatchContext:
    players = list(ctx.players)
    index = next(i for i, p in enumerate(players) if p.id == "kestrel_athletic-11")
    old = players[index]
    players[index] = PlayerRef(
        id=old.id,
        team=old.team,
        shirt=old.shirt,
        name="Jordan Calder",
        short_name="J. Calder",
        aliases=("Jordan Calder", "J. Calder", "Calder"),
    )
    return ctx.model_copy(update={"players": tuple(players)})


def test_a_shared_surname_is_ambiguous() -> None:
    ctx = with_second_calder(match_context())
    (mention,) = mentions("Calder", ctx)
    assert mention.ambiguous and set(mention.candidates) == {
        "harbor_lions-09",
        "kestrel_athletic-11",
    }
    assert analyze_text("Calder ghi bàn trong hiệp 2", ctx).unsupported_reason == "ambiguous_entity"
    full_name = analyze_text("Tobias Calder ghi bàn trong hiệp 2", ctx)
    assert full_name.assertions and full_name.assertions[0].subject.player == "harbor_lions-09"


@pytest.mark.parametrize("claim", ["Smith ghi bàn trong hiệp 2", "Harbor and Smith scored twice"])
def test_unknown_names(claim: str) -> None:
    assert analyze_text(claim, match_context()).unsupported_reason == "unknown_entity"
