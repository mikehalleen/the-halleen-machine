import pytest

from thm_core import find_tokens, rename_bin_handles, rewrite_tokens, slugify
from thm_core.slug import handle_base, handle_ext, media_ext, renumber_handles
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
    assert handle_base("mara.png.2") == "mara.png"
    assert handle_base("mara.png") == "mara.png"
    assert handle_base("rail-dawn-2.png") == "rail-dawn-2.png"
    assert handle_ext("mara.png.2") == "png"
    assert handle_ext("mara-ab12.mp4") == "mp4"


@pytest.mark.parametrize(
    "path,expected",
    [
        ("C:/x/a.JPG", "jpg"),
        ("a.jpeg", "jpeg"),
        ("noext", "file"),
        ("a.3gp", "file"),
        ("", "file"),
    ],
)
def test_media_ext(path, expected):
    assert media_ext(path) == expected


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
    assert (r1.handle, r2.handle) == ("mara.png", "mara.png.2")
    bin_ = p.bins_by_id[s]
    bin_.prompt = "@mara.png beside @mara.png.2"
    bin_.references = [r for r in bin_.references if r.id != r1.id]
    assert renumber_handles(bin_, mara) == {"mara.png.2": "mara.png"}
    assert [r.handle for r in bin_.references] == ["mara.png"]
    assert bin_.prompt == "@mara.png beside @mara.png"
    r3 = bind(p, s, m3)
    assert r3.handle == "mara.png.2"


def test_still_and_clip_from_one_bin_are_separate_series():
    """A png and an mp4 from the same bin read `earth.png` and `earth.mp4`, no counter; a second
    png counts only against the pngs, and unbinding renumbers only the series it left."""
    p = make_project()
    earth = add_bin(p, "Earth")
    still = add_media(p, earth)
    clip = add_media(p, earth, type="video")
    still2 = add_media(p, earth)
    s = add_bin(p, "Image 1")
    r_png = bind(p, s, still)
    r_mp4 = bind(p, s, clip)
    r_png2 = bind(p, s, still2)
    assert (r_png.handle, r_mp4.handle, r_png2.handle) == ("earth.png", "earth.mp4", "earth.png.2")
    bin_ = p.bins_by_id[s]
    bin_.prompt = "@earth.png then @earth.mp4 then @earth.png.2"
    bin_.references = [r for r in bin_.references if r.id != r_png.id]
    assert renumber_handles(bin_, earth) == {"earth.png.2": "earth.png"}
    assert [r.handle for r in bin_.references] == ["earth.mp4", "earth.png"]
    assert bin_.prompt == "@earth.png then @earth.mp4 then @earth.png"


def test_assign_handle_takes_the_lowest_free_number():
    """Safe on a set nothing renumbered: the free slot is reused rather than colliding."""
    p = make_project()
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1")
    r1 = bind(p, s, add_media(p, mara))
    bind(p, s, add_media(p, mara))
    bin_ = p.bins_by_id[s]
    bin_.references = [r for r in bin_.references if r.id != r1.id]  # leaves mara.png.2 alone
    assert bind(p, s, add_media(p, mara)).handle == "mara.png"


def test_same_media_gets_independent_handles_per_bin():
    p = make_project()
    mara = add_bin(p, "Mara")
    m = add_media(p, mara)
    s1, s2 = add_bin(p, "Image 1"), add_bin(p, "Image 2")
    bind(p, s1, m)
    bind(p, s1, add_media(p, mara))
    assert bind(p, s2, m).handle == "mara.png"
    assert [r.handle for r in p.bins_by_id[s1].references] == ["mara.png", "mara.png.2"]


def test_self_reference_uses_the_bins_own_name():
    """A bin may reference its own media: the handle is its own slug, and a rename rewrites
    its own prompt like anyone else's."""
    p = make_project()
    ed = add_bin(p, "Ed")
    t = add_take(p, ed)
    assert bind(p, ed, t).handle == "ed.png"
    p.bins_by_id[ed].prompt = "@ed.png again, wider"
    assert rename_bin_handles(p, ed, "Eddie") == 1
    assert p.bins_by_id[ed].references[0].handle == "eddie.png" and p.bins_by_id[ed].prompt == "@eddie.png again, wider"


def test_collision_appends_short_id():
    p = make_project()
    a = add_bin(p, "Mara")
    b = add_bin(p, "Mara")
    s = add_bin(p, "Image 1")
    assert bind(p, s, add_media(p, a)).handle == "mara.png"
    second = bind(p, s, add_media(p, b)).handle
    assert second == f"mara-{b[-4:]}.png"
    assert bind(p, s, add_media(p, b)).handle == f"mara-{b[-4:]}.png.2"


def test_collision_is_invisible_when_bins_are_referenced_from_different_bins():
    p = make_project()
    a = add_bin(p, "Mara")
    b = add_bin(p, "Mara")
    assert bind(p, add_bin(p, "Image 1"), add_media(p, a)).handle == "mara.png"
    assert bind(p, add_bin(p, "Image 2"), add_media(p, b)).handle == "mara.png"


def test_rename_rewrites_handles_and_prompts_in_every_bin():
    p = make_project()
    mara = add_bin(p, "Mara")
    other = add_bin(p, "Rail Dawn")
    s1, s2, s3 = add_bin(p, "Image 1"), add_bin(p, "Image 2"), add_bin(p, "Image 3")
    bind(p, s1, add_media(p, mara))
    bind(p, s1, add_media(p, mara))
    p.bins_by_id[s1].prompt = "@mara.png looks at @mara.png.2 at @rail-dawn.png. End @mara.png."
    p.bins_by_id[s1].negative = "no @mara.png here"
    bind(p, s1, add_media(p, other))
    bind(p, s2, add_media(p, mara))
    p.bins_by_id[s2].prompt = "@mara.png alone"
    bind(p, s3, add_media(p, other))
    p.bins_by_id[s3].prompt = "@rail-dawn.png only"

    assert rename_bin_handles(p, mara, "Mara Vane") == 2
    assert [r.handle for r in p.bins_by_id[s1].references] == ["mara-vane.png", "mara-vane.png.2", "rail-dawn.png"]
    assert p.bins_by_id[s1].prompt == "@mara-vane.png looks at @mara-vane.png.2 at @rail-dawn.png. End @mara-vane.png."
    assert p.bins_by_id[s1].negative == "no @mara-vane.png here"
    assert p.bins_by_id[s2].prompt == "@mara-vane.png alone"
    assert p.bins_by_id[s3].prompt == "@rail-dawn.png only"
    assert p.bins_by_id[mara].name == "Mara Vane"


def test_rename_into_collision_suffixes_only_the_colliding_bin():
    p = make_project()
    a = add_bin(p, "Anna")
    b = add_bin(p, "Mara")
    s1, s2 = add_bin(p, "Image 1"), add_bin(p, "Image 2")
    bind(p, s1, add_media(p, b))
    bind(p, s1, add_media(p, a))
    p.bins_by_id[s1].prompt = "@anna.png at @mara.png"
    bind(p, s2, add_media(p, a))
    p.bins_by_id[s2].prompt = "@anna.png alone"
    rename_bin_handles(p, a, "Mara")
    assert [r.handle for r in p.bins_by_id[s1].references] == ["mara.png", f"mara-{a[-4:]}.png"]
    assert p.bins_by_id[s1].prompt == f"@mara-{a[-4:]}.png at @mara.png"
    assert p.bins_by_id[s2].references[0].handle == "mara.png"
    assert p.bins_by_id[s2].prompt == "@mara.png alone"


def test_swap_renames_do_not_double_replace():
    p = make_project()
    a = add_bin(p, "A")
    b = add_bin(p, "B")
    s = add_bin(p, "Image 1")
    bind(p, s, add_media(p, a))
    bind(p, s, add_media(p, b))
    p.bins_by_id[s].prompt = "@a.png then @b.png"
    rename_bin_handles(p, a, "Tmp")
    rename_bin_handles(p, b, "A")
    rename_bin_handles(p, a, "B")
    assert p.bins_by_id[s].prompt == "@b.png then @a.png"
    assert [r.handle for r in p.bins_by_id[s].references] == ["b.png", "a.png"]


def test_reorder_leaves_handles_and_prompt_alone():
    p = make_project()
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1")
    bind(p, s, add_media(p, mara))
    bind(p, s, add_media(p, mara))
    bin_ = p.bins_by_id[s]
    bin_.prompt = "@mara.png and @mara.png.2"
    bin_.references.reverse()
    assert [r.handle for r in bin_.references] == ["mara.png.2", "mara.png"]
    assert bin_.prompt == "@mara.png and @mara.png.2"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("@mara.png stands.", ["mara.png"]),
        ("see @mara.png.", ["mara.png"]),
        ("@mara.png.", ["mara.png"]),
        ("@mara.png.2.", ["mara.png.2"]),
        ("see @mara.png.2.", ["mara.png.2"]),
        ("@mara.png.2b", ["mara.png"]),
        ("@mara", []),
        ("end.next", []),
        ("@@mara.png", []),
        ("a@mara.png", []),
        ("me@mara.png", []),
        ("@Mara.png", []),
        ("(@mara.png)", ["mara.png"]),
        ("@rail-dawn.jpg, then @mara.mp4", ["rail-dawn.jpg", "mara.mp4"]),
        ("file.png", []),
        ("@mara.3gp", []),
    ],
)
def test_find_tokens_boundaries(text, expected):
    assert [h for _, _, h in find_tokens(text)] == expected


def test_rewrite_tokens_modes():
    assert rewrite_tokens("@mara.png at @rail-dawn.png.", {"mara.png": "mara-vane.png"}) == "@mara-vane.png at @rail-dawn.png."
    assert rewrite_tokens("@mara.png at @rail-dawn.mp4.", {"mara.png": "<Picture 1>", "rail-dawn.mp4": "<Video 1>"}, keep_prefix=False) == "<Picture 1> at <Video 1>."
    assert rewrite_tokens("", {"a.png": "b.png"}) == ""
