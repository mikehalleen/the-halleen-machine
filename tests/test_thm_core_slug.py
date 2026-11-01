import pytest

from thm_core import find_tokens, rename_bin_handles, rewrite_tokens, slugify
from thm_core.slug import renumber_handles, handle_base
from thm_core_fixtures import add_bin, add_media, add_take, bind, make_project


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Rail Dawn 2", "rail-dawn-2"),
        ("  Mara  ", "mara"),
        ("Rail Dawn 2!", "rail-dawn-2"),
        ("Café du Nord", "caf-du-nord"),
        ("--dots.and.spaces--", "dots-and-spaces"),
        ("", "bin"),
        ("!!!", "bin"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_handle_base_strips_only_digit_suffix():
    assert handle_base("mara.2") == "mara"
    assert handle_base("mara") == "mara"
    assert handle_base("rail-dawn-2") == "rail-dawn-2"


def test_handles_number_against_the_live_set():
    """Numbers describe what is bound now, not everything the bin has ever had. Dropping a
    reference frees its number for the next one, and `renumber_handles` closes the gap so a lone
    reference never sits there wearing a .2 (`unbind_reference` calls it)."""
    p = make_project()
    mara = add_bin(p, "Mara")
    m1, m2, m3 = (add_media(p, mara) for _ in range(3))
    s = add_bin(p, "Image 1")
    r1 = bind(p, s, m1)
    r2 = bind(p, s, m2)
    assert (r1.handle, r2.handle) == ("mara", "mara.2")
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara beside .mara.2"
    bin_.references = [r for r in bin_.references if r.id != r1.id]
    assert renumber_handles(bin_, mara) == {"mara.2": "mara"}
    assert [r.handle for r in bin_.references] == ["mara"]
    assert bin_.prompt == ".mara beside .mara"
    r3 = bind(p, s, m3)
    assert r3.handle == "mara.2"


def test_assign_handle_takes_the_lowest_free_number():
    """Safe on a set nothing renumbered: the free slot is reused rather than colliding."""
    p = make_project()
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1")
    r1 = bind(p, s, add_media(p, mara))
    bind(p, s, add_media(p, mara))
    bin_ = p.bins_by_id[s]
    bin_.references = [r for r in bin_.references if r.id != r1.id]  # leaves mara.2 alone
    assert bind(p, s, add_media(p, mara)).handle == "mara"


def test_same_media_gets_independent_handles_per_bin():
    p = make_project()
    mara = add_bin(p, "Mara")
    m = add_media(p, mara)
    s1, s2 = add_bin(p, "Image 1"), add_bin(p, "Image 2")
    bind(p, s1, m)
    bind(p, s1, add_media(p, mara))
    assert bind(p, s2, m).handle == "mara"
    assert [r.handle for r in p.bins_by_id[s1].references] == ["mara", "mara.2"]


def test_self_reference_uses_the_bins_own_name():
    """A bin may reference its own media: the handle is its own slug, and a rename rewrites
    its own prompt like anyone else's."""
    p = make_project()
    ed = add_bin(p, "Ed")
    t = add_take(p, ed)
    assert bind(p, ed, t).handle == "ed"
    p.bins_by_id[ed].prompt = ".ed again, wider"
    assert rename_bin_handles(p, ed, "Eddie") == 1
    assert p.bins_by_id[ed].references[0].handle == "eddie" and p.bins_by_id[ed].prompt == ".eddie again, wider"


def test_collision_appends_short_id():
    p = make_project()
    a = add_bin(p, "Mara")
    b = add_bin(p, "Mara")
    s = add_bin(p, "Image 1")
    assert bind(p, s, add_media(p, a)).handle == "mara"
    second = bind(p, s, add_media(p, b)).handle
    assert second == f"mara-{b[-4:]}"
    assert bind(p, s, add_media(p, b)).handle == f"mara-{b[-4:]}.2"


def test_collision_is_invisible_when_bins_are_referenced_from_different_bins():
    p = make_project()
    a = add_bin(p, "Mara")
    b = add_bin(p, "Mara")
    assert bind(p, add_bin(p, "Image 1"), add_media(p, a)).handle == "mara"
    assert bind(p, add_bin(p, "Image 2"), add_media(p, b)).handle == "mara"


def test_rename_rewrites_handles_and_prompts_in_every_bin():
    p = make_project()
    mara = add_bin(p, "Mara")
    other = add_bin(p, "Rail Dawn")
    s1, s2, s3 = add_bin(p, "Image 1"), add_bin(p, "Image 2"), add_bin(p, "Image 3")
    bind(p, s1, add_media(p, mara))
    bind(p, s1, add_media(p, mara))
    p.bins_by_id[s1].prompt = ".mara looks at .mara.2 at .rail-dawn. End .mara."
    p.bins_by_id[s1].negative = "no .mara here"
    bind(p, s1, add_media(p, other))
    bind(p, s2, add_media(p, mara))
    p.bins_by_id[s2].prompt = ".mara alone"
    bind(p, s3, add_media(p, other))
    p.bins_by_id[s3].prompt = ".rail-dawn only"

    assert rename_bin_handles(p, mara, "Mara Vane") == 2
    assert [r.handle for r in p.bins_by_id[s1].references] == ["mara-vane", "mara-vane.2", "rail-dawn"]
    assert p.bins_by_id[s1].prompt == ".mara-vane looks at .mara-vane.2 at .rail-dawn. End .mara-vane."
    assert p.bins_by_id[s1].negative == "no .mara-vane here"
    assert p.bins_by_id[s2].prompt == ".mara-vane alone"
    assert p.bins_by_id[s3].prompt == ".rail-dawn only"
    assert p.bins_by_id[mara].name == "Mara Vane"


def test_rename_into_collision_suffixes_only_the_colliding_bin():
    p = make_project()
    a = add_bin(p, "Anna")
    b = add_bin(p, "Mara")
    s1, s2 = add_bin(p, "Image 1"), add_bin(p, "Image 2")
    bind(p, s1, add_media(p, b))
    bind(p, s1, add_media(p, a))
    p.bins_by_id[s1].prompt = ".anna at .mara"
    bind(p, s2, add_media(p, a))
    p.bins_by_id[s2].prompt = ".anna alone"
    rename_bin_handles(p, a, "Mara")
    assert [r.handle for r in p.bins_by_id[s1].references] == ["mara", f"mara-{a[-4:]}"]
    assert p.bins_by_id[s1].prompt == f".mara-{a[-4:]} at .mara"
    assert p.bins_by_id[s2].references[0].handle == "mara"
    assert p.bins_by_id[s2].prompt == ".mara alone"


def test_swap_renames_do_not_double_replace():
    p = make_project()
    a = add_bin(p, "A")
    b = add_bin(p, "B")
    s = add_bin(p, "Image 1")
    bind(p, s, add_media(p, a))
    bind(p, s, add_media(p, b))
    p.bins_by_id[s].prompt = ".a then .b"
    rename_bin_handles(p, a, "Tmp")
    rename_bin_handles(p, b, "A")
    rename_bin_handles(p, a, "B")
    assert p.bins_by_id[s].prompt == ".b then .a"
    assert [r.handle for r in p.bins_by_id[s].references] == ["b", "a"]


def test_reorder_leaves_handles_and_prompt_alone():
    p = make_project()
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1")
    bind(p, s, add_media(p, mara))
    bind(p, s, add_media(p, mara))
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara and .mara.2"
    bin_.references.reverse()
    assert [r.handle for r in bin_.references] == ["mara.2", "mara"]
    assert bin_.prompt == ".mara and .mara.2"


@pytest.mark.parametrize(
    "text,expected",
    [
        (".mara stands.", ["mara"]),
        ("see .mara.", ["mara"]),
        ("see .mara.2.", ["mara.2"]),
        ("end.next", []),
        ("..mara", []),
        (".mara.2b", ["mara"]),
        (".rail-dawn, then .mara", ["rail-dawn", "mara"]),
        ("(.mara)", ["mara"]),
        ("file.png", []),
        (".Mara", []),
    ],
)
def test_find_tokens_boundaries(text, expected):
    assert [h for _, _, h in find_tokens(text)] == expected


def test_rewrite_tokens_modes():
    assert rewrite_tokens(".mara at .rail-dawn.", {"mara": "mara-vane"}) == ".mara-vane at .rail-dawn."
    assert rewrite_tokens(".mara at .rail-dawn.", {"mara": "<Picture 1>", "rail-dawn": "<Picture 2>"}, keep_dot=False) == "<Picture 1> at <Picture 2>."
    assert rewrite_tokens("", {"a": "b"}) == ""
