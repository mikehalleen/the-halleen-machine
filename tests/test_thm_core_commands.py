import pytest

from thm_core import CommandContext, CommandRefused, execute
from thm_core.models import Lora
from thm_core.commands import (
    AddMediaToBin,
    BindReference,
    ConnectSession,
    CreateBin,
    DeleteBin,
    DeleteMedia,
    DuplicateBin,
    EndSession,
    Generate,
    MarkTake,
    PostMessage,
    RebindReference,
    RecallGeneration,
    RemoveMediaFromBin,
    ReorderReferences,
    SetBinField,
    SetProjectSettings,
    StartSession,
    UnbindReference,
)
from thm_core_fixtures import (
    AGENT,
    IMAGE_SIDECAR,
    USER,
    add_bin,
    add_media,
    add_take,
    bind,
    image_workflow,
    live_session,
    make_env,
    make_project,
    make_store,
    video_ref_workflow,
    video_workflow,
    waiting_session,
)


@pytest.fixture
def world(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path, {"img.json": image_workflow(2), "vid.json": video_workflow(), "noref.json": image_workflow(0)}, {"img.thm.toml": IMAGE_SIDECAR})
    return store, p, env


def refused(code, fn):
    with pytest.raises(CommandRefused) as exc:
        fn()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}: {exc.value.detail}"
    return exc.value


# -- bins --------------------------------------------------------------------


def test_create_bin_names_the_lowest_free_number_and_logs_change(world):
    store, p, env = world
    r1 = execute(store, p, CreateBin(workflow="vid.json"), USER, env=env)
    r2 = execute(store, p, CreateBin(workflow="img.json"), USER, env=env)
    r3 = execute(store, p, CreateBin(workflow="vid.json", length_s=8), USER, env=env)
    bins = list(p.bins_by_id.values())
    assert [b.name for b in bins] == ["bin-1", "bin-2", "bin-3"]
    assert bins[0].length_s == 5.0 and bins[2].length_s == 8.0 and bins[1].length_s is None
    assert bins[0].created == "2026-09-06T10:00:00Z"
    assert r1.change.description == "added bin-1"
    assert r1.change.target.type == "project"
    assert "bins_by_id" in r1.change.after and r1.change.actor == "user" and r1.change.decision is None
    assert list(store.load(p.slug).bins_by_id) == list(p.bins_by_id)
    assert [c.description for c in store.read_changes(p).values()] == [r1.change.description, r2.change.description, r3.change.description]
    # a named bin, and a deleted number comes back around
    execute(store, p, DeleteBin(bin=bins[1].id), USER, env=env)
    c = CreateBin(bin_name=" Ed ")
    execute(store, p, c, USER, env=env)
    assert p.bins_by_id[c._created].name == "Ed"
    c = CreateBin()
    execute(store, p, c, USER, env=env)
    assert p.bins_by_id[c._created].name == "bin-2"


def test_create_bin_workflow_defaults_and_refusals(world):
    store, p, env = world
    refused("unknown_workflow", lambda: execute(store, p, CreateBin(workflow="nope.json"), USER, env=env))
    refused("bad_value", lambda: execute(store, p, CreateBin(bin_name="  "), USER, env=env))
    refused("bad_value", lambda: execute(store, p, CreateBin(workflow="vid.json", length_s=0), USER, env=env))
    # no image default yet: a bin with no workflow is fine (uploads need none)
    c = CreateBin()
    execute(store, p, c, USER, env=env)
    assert p.bins_by_id[c._created].workflow == ""
    execute(store, p, SetProjectSettings(image_workflow="img.json", video_workflow="vid.json"), USER, env=env)
    c = CreateBin()
    execute(store, p, c, USER, env=env)
    assert p.bins_by_id[c._created].workflow == "img.json"
    c = CreateBin(workflow="vid.json")  # an explicit workflow still wins
    execute(store, p, c, USER, env=env)
    assert p.bins_by_id[c._created].workflow == "vid.json" and p.bins_by_id[c._created].length_s == 5.0


def test_duplicate_bin_copies_the_recipe_and_optionally_the_gallery(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    m = add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    bind(p, s, m)
    t = add_take(p, s)
    src = p.bins_by_id[s]
    src.prompt, src.selected, src.tags = ".mara at dawn", t, ["hero"]
    r = execute(store, p, DuplicateBin(bin=s), USER, env=env)
    assert r.change.description == "duplicated Image 1 as Image 1-copy"
    copy = next(b for b in p.bins_by_id.values() if b.name == "Image 1-copy")
    assert copy.id.startswith("b_") and copy.prompt == ".mara at dawn" and copy.tags == ["hero"]
    assert copy.media == [] and copy.selected is None and copy.counters == {}
    assert [(x.media, x.bin, x.handle) for x in copy.references] == [(m, mara, "mara")]
    assert copy.references[0].id != src.references[0].id
    r = execute(store, p, DuplicateBin(bin=s, with_media=True), USER, env=env)
    assert r.change.description == "duplicated Image 1 as Image 1-copy-2"
    full = next(b for b in p.bins_by_id.values() if b.name == "Image 1-copy-2")
    assert full.media == [t] and full.selected == t and full.counters == {"take": 1}
    refused("unknown_id", lambda: execute(store, p, DuplicateBin(bin="b_nope"), USER, env=env))


def test_set_length_logs_before_after_and_description(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    r = execute(store, p, SetBinField(bin=s, field="length_s", value=8), USER, env=env)
    assert r.change.before == {"length_s": 5.0}
    assert r.change.after == {"length_s": 8.0}
    assert r.change.description == "Video 1 - length 5s to 8s"
    assert r.change.target.id == s


@pytest.mark.parametrize(
    "code,make",
    [
        ("unknown_id", lambda p, s, m: SetBinField(bin="b_nope", field="prompt", value="x")),
        ("wrong_kind", lambda p, s, m: SetBinField(bin=s, field="length_s", value=5)),
        ("bad_value", lambda p, s, m: SetBinField(bin=s, field="prompt", value=5)),
        ("bad_value", lambda p, s, m: SetBinField(bin=s, field="name", value="  ")),
        ("bad_value", lambda p, s, m: SetBinField(bin=s, field="loras", value=[{"strength": 1}])),
        ("bad_value", lambda p, s, m: SetBinField(bin=s, field="loras", value=[{"name": "x", "strength": "heavy"}])),
        ("bad_value", lambda p, s, m: SetBinField(bin=s, field="tags", value="hero")),
        ("not_in_bin", lambda p, s, m: SetBinField(bin=s, field="selected", value=m)),
        ("unknown_workflow", lambda p, s, m: SetBinField(bin=s, field="workflow", value="nope.json")),
        ("unknown_id", lambda p, s, m: BindReference(bin=s, media="m_nope")),
        ("unknown_id", lambda p, s, m: UnbindReference(bin=s, reference="r_nope")),
        ("bad_order", lambda p, s, m: ReorderReferences(bin=s, order=["r_x"])),
        ("not_a_take", lambda p, s, m: MarkTake(media=m, status="pass")),
        ("unknown_id", lambda p, s, m: AddMediaToBin(bin="b_nope", media=m)),
        ("already_in_bin", lambda p, s, m: AddMediaToBin(bin=p.media_by_id[m].origin.bin, media=m)),
        ("not_in_bin", lambda p, s, m: RemoveMediaFromBin(bin=s, media=m)),
        ("bad_value", lambda p, s, m: PostMessage(text="  ")),
        ("unknown_id", lambda p, s, m: PostMessage(text="hi", attachments=["m_nope"])),
        ("wrong_actor", lambda p, s, m: PostMessage(text="hi", kind="agent")),
        ("no_session", lambda p, s, m: EndSession()),
    ],
)
def test_refusals(world, code, make):
    store, p, env = world
    mara = add_bin(p, "Mara")
    m = add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    refused(code, lambda: execute(store, p, make(p, s, m), USER, env=env))
    assert store.read_changes(p) == {}


def test_bind_capacity_and_type_rules(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, BindReference(bin=s, media=add_media(p, mara)), USER, env=env)
    r = execute(store, p, BindReference(bin=s, media=add_media(p, mara)), USER, env=env)
    assert r.change.description == "Image 1 - bound .mara.2"
    refused("too_many_references", lambda: execute(store, p, BindReference(bin=s, media=add_media(p, mara)), USER, env=env))
    refused("media_type_unsupported", lambda: execute(store, p, BindReference(bin=s, media=add_media(p, mara, type="video")), USER, env=env))
    s2 = add_bin(p, "Image 2", "noref.json")
    refused("media_type_unsupported", lambda: execute(store, p, BindReference(bin=s2, media=add_media(p, mara)), USER, env=env))
    # a bin with no workflow has no capacity to bind into
    s3 = add_bin(p, "Empty", "")
    refused("unknown_workflow", lambda: execute(store, p, BindReference(bin=s3, media=add_media(p, mara)), USER, env=env))


def test_bind_a_take_from_another_bin_and_from_the_bin_itself(world):
    store, p, env = world
    ed = add_bin(p, "Ed", "img.json")
    t = add_take(p, ed)
    band = add_bin(p, "Band", "img.json")
    r = execute(store, p, BindReference(bin=band, media=t), USER, env=env)
    assert r.change.description == "Band - bound .ed"
    assert p.bins_by_id[band].references[0].bin == ed
    # the bin's own take: the handle is its own name
    r = execute(store, p, BindReference(bin=ed, media=t), USER, env=env)
    assert r.change.description == "Ed - bound .ed" and p.bins_by_id[ed].references[0].bin == ed


def test_bind_refuses_media_no_bin_holds(world):
    """A media whose bin was deleted has no name to mint a handle from."""
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    orphan = add_media(p, None, origin={"type": "generation", "generation": "g_1", "bin": "s_gone"})
    refused("no_bin", lambda: execute(store, p, BindReference(bin=s, media=orphan), USER, env=env))
    # adding it to a bin makes it referenceable again, under that bin's name
    execute(store, p, AddMediaToBin(bin=s, media=orphan), USER, env=env)
    s2 = add_bin(p, "Image 2", "img.json")
    r = execute(store, p, BindReference(bin=s2, media=orphan), USER, env=env)
    assert r.change.description == "Image 2 - bound .image-1"


def test_bind_video_reference_accepted_when_workflow_has_video_slots(tmp_path):
    store = make_store(tmp_path)
    p = make_project(store)
    env = make_env(tmp_path, {"vidref.json": video_ref_workflow(2), "img.json": image_workflow(2)})
    loc = add_bin(p, "Rail Dawn")
    s = add_bin(p, "Video 1", "vidref.json", length_s=5)
    execute(store, p, BindReference(bin=s, media=add_media(p, loc, type="video")), USER, env=env)
    r = execute(store, p, BindReference(bin=s, media=add_media(p, loc, type="video")), USER, env=env)
    assert r.change.description == "Video 1 - bound .rail-dawn.2"
    refused("too_many_references", lambda: execute(store, p, BindReference(bin=s, media=add_media(p, loc, type="video")), USER, env=env))
    s2 = add_bin(p, "Image 1", "img.json")
    refused("media_type_unsupported", lambda: execute(store, p, BindReference(bin=s2, media=add_media(p, loc, type="video")), USER, env=env))


def test_video_bin_takes_start_and_end_images(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    execute(store, p, BindReference(bin=s, media=add_media(p, mara)), USER, env=env)
    execute(store, p, BindReference(bin=s, media=add_media(p, mara)), USER, env=env)
    refused("too_many_references", lambda: execute(store, p, BindReference(bin=s, media=add_media(p, mara)), USER, env=env))


def test_unbind_and_reorder(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1", "img.json")
    r1 = bind(p, s, add_media(p, mara))
    r2 = bind(p, s, add_media(p, mara))
    r = execute(store, p, ReorderReferences(bin=s, order=[r2.id, r1.id]), USER, env=env)
    assert [x.id for x in p.bins_by_id[s].references] == [r2.id, r1.id]
    assert r.change.after["references"][0]["handle"] == "mara.2"
    r = execute(store, p, UnbindReference(bin=s, reference=r1.id), USER, env=env)
    assert r.change.description == "Image 1 - unbound .mara"
    # the survivor closes the gap rather than sitting there as a lone .2
    assert [x.handle for x in p.bins_by_id[s].references] == ["mara"]
    assert p.media_by_id[r1.media] is not None


def test_rebind_reference_keeps_handle_swaps_media(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    m1 = add_media(p, mara)
    m2 = add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, BindReference(bin=s, media=m1), USER, env=env)
    ref = p.bins_by_id[s].references[0]
    ref_id, handle_before = ref.id, ref.handle
    r = execute(store, p, RebindReference(bin=s, reference=ref_id, media=m2), USER, env=env)
    ref = p.bins_by_id[s].references[0]
    assert ref.media == m2 and ref.handle == handle_before
    assert r.change.target.type == "bin" and r.change.target.id == s
    refused("unknown_id", lambda: execute(store, p, RebindReference(bin=s, reference="r_nope", media=m2), USER, env=env))
    refused("unknown_id", lambda: execute(store, p, RebindReference(bin=s, reference=ref_id, media="m_nope"), USER, env=env))
    # rebinding onto another bin's media keeps the handle but records the new bin
    ed = add_bin(p, "Ed", "img.json")
    t = add_take(p, ed)
    execute(store, p, RebindReference(bin=s, reference=ref_id, media=t), USER, env=env)
    ref = p.bins_by_id[s].references[0]
    assert ref.media == t and ref.handle == handle_before and ref.bin == ed
    orphan = add_media(p, None, origin={"type": "generation", "generation": "g_1", "bin": "s_gone"})
    refused("no_bin", lambda: execute(store, p, RebindReference(bin=s, reference=ref_id, media=orphan), USER, env=env))


def test_generate_refuses_a_bin_with_no_workflow(world):
    store, p, env = world
    s = add_bin(p, "Empty", "")
    refused("unknown_workflow", lambda: execute(store, p, Generate(bin=s), USER, env=env))
    refused("unknown_id", lambda: execute(store, p, Generate(bin="b_nope"), USER, env=env))


def test_generate_stamps_the_workflow_kind(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    v = add_bin(p, "Video 1", "vid.json", length_s=5)
    assert execute(store, p, Generate(bin=s), USER, env=env).generation.kind == "image"
    g = execute(store, p, Generate(bin=v), USER, env=env).generation
    assert g.kind == "video" and g.params["length_s"] == 5.0 and g.bin == v


def test_generate_with_hold_logs_a_held_snapshot(world):
    """Save for later: the generation is resolved and logged now with status held, the change
    says so, and edits to the bin afterwards never reach the record."""
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, SetBinField(bin=s, field="prompt", value="a cat"), USER, env=env)
    r = execute(store, p, Generate(bin=s, hold=True), USER, env=env)
    g = r.generation
    assert g.status == "held" and g.prompt_submitted == "a cat"
    assert r.change.generation == g.id and "saved for later" in r.change.description
    assert store.read_generation(p, g.id).status == "held"
    execute(store, p, SetBinField(bin=s, field="prompt", value="a dog"), USER, env=env)
    assert store.read_generation(p, g.id).prompt_submitted == "a cat"
    assert execute(store, p, Generate(bin=s), USER, env=env).generation.status == "queued"


def test_loras_ride_the_submitted_prompt_as_tags(world):
    """The typed prompt never carries LoRAs; the assembler puts them in front at submit and the
    generation records them, so a recall gets them back."""
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    r = execute(store, p, SetBinField(bin=s, field="name", value=" Opening "), USER, env=env)
    assert p.bins_by_id[s].name == "Opening" and r.change.description == "Image 1 renamed to Opening"
    assert r.change.target.type == "project"
    execute(store, p, SetBinField(bin=s, field="name", value="Image 1"), USER, env=env)
    execute(store, p, SetBinField(bin=s, field="prompt", value="a cat"), USER, env=env)
    r = execute(
        store, p,
        SetBinField(bin=s, field="loras", value=[{"name": "style.safetensors", "strength": 0.7}, {"name": "", "strength": 1}, {"name": "pair.safetensors", "strength": 1, "strength_b": 0.5}]),
        USER, env=env,
    )
    assert r.change.description == "Image 1 - loras style.safetensors, pair.safetensors"
    assert [l.name for l in p.bins_by_id[s].loras] == ["style.safetensors", "pair.safetensors"]
    g = execute(store, p, Generate(bin=s), USER, env=env).generation
    assert g.prompt_typed == "a cat"
    assert g.prompt_submitted == "<lora:style.safetensors:0.7> <lora:pair.safetensors:1:0.5> a cat"
    assert [(l.name, l.strength, l.strength_b) for l in g.loras] == [("style.safetensors", 0.7, None), ("pair.safetensors", 1.0, 0.5)]

    execute(store, p, SetBinField(bin=s, field="loras", value=[]), USER, env=env)
    assert execute(store, p, Generate(bin=s), USER, env=env).generation.prompt_submitted == "a cat"
    execute(store, p, RecallGeneration(bin=s, prompt="again", loras=[Lora(name="style.safetensors", strength=0.3)]), USER, env=env)
    assert [(l.name, l.strength) for l in p.bins_by_id[s].loras] == [("style.safetensors", 0.3)]


def test_typed_lora_tags_are_lifted_out_of_the_prompt(world):
    """A tag typed into the prompt, in either notation, lands on the loras field and leaves the text;
    a typed value for a name already on the field replaces that entry."""
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, SetBinField(bin=s, field="loras", value=[{"name": "style.safetensors", "strength": 0.7}]), USER, env=env)
    r = execute(store, p, SetBinField(bin=s, field="prompt", value="<lora:style.safetensors:0.2> a cat\n__lora:old.safetensors:1:0.5__ at dusk"), USER, env=env)
    bin_ = p.bins_by_id[s]
    assert bin_.prompt == "a cat\nat dusk"
    assert [(l.name, l.strength, l.strength_b) for l in bin_.loras] == [("style.safetensors", 0.2, None), ("old.safetensors", 1.0, 0.5)]
    assert r.change.after["prompt"] == "a cat\nat dusk" and len(r.change.after["loras"]) == 2
    execute(store, p, RecallGeneration(bin=s, prompt="<lora:x.safetensors:0.5> again"), USER, env=env)
    assert bin_.prompt == "again" and [l.name for l in bin_.loras] == ["x.safetensors"]


def test_recall_restores_references_with_their_handles(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    m1, m2 = add_media(p, mara), add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    r = execute(
        store, p,
        RecallGeneration(bin=s, from_media="m_take", workflow="img.json", prompt=".mara.2 first", references=[{"media": m2, "handle": "mara.2"}, {"media": m1, "handle": "mara"}]),
        USER, env=env,
    )
    assert r.change.description == "Image 1 - recalled the settings that made m_take"
    assert [(x.media, x.bin, x.handle) for x in p.bins_by_id[s].references] == [(m2, mara, "mara.2"), (m1, mara, "mara")]
    refused("unknown_workflow", lambda: execute(store, p, RecallGeneration(bin=s, workflow="nope.json"), USER, env=env))
    orphan = add_media(p, None, origin={"type": "generation", "generation": "g_1", "bin": "s_gone"})
    refused("no_bin", lambda: execute(store, p, RecallGeneration(bin=s, references=[{"media": orphan, "handle": "x"}]), USER, env=env))


def test_delete_bin_keeps_media_something_else_still_holds(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    other = add_bin(p, "Video 1", "vid.json", length_s=5)
    orphan = add_take(p, s)
    starred = add_take(p, s)
    bound = add_take(p, s)
    mara = add_bin(p, "Mara")
    execute(store, p, AddMediaToBin(bin=mara, media=starred), USER, env=env)
    assert p.bins_by_id[mara].selected == starred  # the newest arrival leads
    execute(store, p, BindReference(bin=other, media=bound), USER, env=env)
    execute(store, p, BindReference(bin=s, media=add_media(p, mara)), USER, env=env)  # its own reference goes with it
    r = execute(store, p, DeleteBin(bin=s), USER, env=env)
    assert r.change.description == "deleted Image 1 (1 media)" and r.change.target.type == "project"
    assert s not in p.bins_by_id
    assert orphan not in p.media_by_id
    assert starred in p.media_by_id and starred in p.bins_by_id[mara].media
    assert bound in p.media_by_id and [ref.media for ref in p.bins_by_id[other].references] == [bound]
    # the surviving take's origin still names the gone bin; it is findable through the gallery holding it
    assert p.media_by_id[starred].origin.bin == s
    refused("unknown_id", lambda: execute(store, p, DeleteBin(bin=s), USER, env=env))


def test_mark_take_and_selected(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    t = add_take(p, s)
    r = execute(store, p, MarkTake(media=t, status="fail", note="hands"), USER, env=env)
    assert p.media_by_id[t].qc.status == "fail" and p.media_by_id[t].qc.note == "hands" and p.media_by_id[t].qc.by == "user"
    assert r.change.description == "Image 1 take 1 - marked fail"
    r = execute(store, p, SetBinField(bin=s, field="selected", value=t), USER, env=env)
    assert r.change.description == "Image 1 - selected take 1"
    assert p.bins_by_id[s].media == [t]
    u = add_media(p, s)
    r = execute(store, p, SetBinField(bin=s, field="selected", value=u), USER, env=env)
    assert r.change.description == f"Image 1 - selected {u}"
    r = execute(store, p, SetBinField(bin=s, field="selected", value=None), USER, env=env)
    assert r.change.description == "Image 1 - selected none"
    # a take marked in the bin that now holds it, after its own bin went
    execute(store, p, AddMediaToBin(bin=add_bin(p, "Keep"), media=t), USER, env=env)
    execute(store, p, DeleteBin(bin=s), USER, env=env)
    r = execute(store, p, MarkTake(media=t, status="pass"), USER, env=env)
    assert r.change.description == "Keep media - marked pass"


def test_rename_rewrites_referencing_bins_in_one_change(world):
    store, p, env = world
    c = CreateBin(bin_name="Mara")
    execute(store, p, c, USER, env=env)
    mara = c._created
    m = add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, BindReference(bin=s, media=m), USER, env=env)
    p.bins_by_id[s].prompt = ".mara waits"
    r = execute(store, p, SetBinField(bin=mara, field="name", value="Mara Vane"), USER, env=env)
    assert r.change.description == "Mara renamed to Mara Vane (1 bin references it)"
    assert p.bins_by_id[s].prompt == ".mara-vane waits"
    assert r.change.target.type == "project" and "bins_by_id" in r.change.after
    execute(store, p, SetBinField(bin=mara, field="description", value="lead singer"), USER, env=env)
    r = execute(store, p, SetBinField(bin=mara, field="tags", value=[" Hero ", "", "cast", "hero"]), USER, env=env)
    assert p.bins_by_id[mara].tags == ["hero", "cast"] and r.change.description == "Mara Vane - tags hero, cast"
    c = CreateBin(bin_name="Stage", tags=["Location", "location", " "])
    execute(store, p, c, USER, env=env)
    assert p.bins_by_id[c._created].tags == ["location"]


def test_delete_media_cascades_everywhere(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    m1 = add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, BindReference(bin=s, media=m1), USER, env=env)
    t1 = add_take(p, s)
    t2 = add_take(p, s)
    p.bins_by_id[s].selected = t2

    r = execute(store, p, DeleteMedia(media=t2), USER, env=env)
    bin_ = p.bins_by_id[s]
    assert bin_.media == [t1] and bin_.selected == t1
    assert r.change.target.type == "project" and r.change.description == "deleted Image 1 take 2 from the library"

    execute(store, p, DeleteMedia(media=t1), USER, env=env)
    bin_ = p.bins_by_id[s]
    assert bin_.media == [] and bin_.selected is None

    execute(store, p, DeleteMedia(media=m1), USER, env=env)
    assert p.bins_by_id[s].references == []
    assert m1 not in p.bins_by_id[mara].media
    assert m1 not in p.media_by_id
    refused("unknown_id", lambda: execute(store, p, DeleteMedia(media="m_nope"), USER, env=env))


def test_delete_bin_removes_its_own_media(world):
    store, p, env = world
    mara = add_bin(p, "Mara")
    m1 = add_media(p, mara)
    m2 = add_media(p, mara)
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, BindReference(bin=s, media=m1), USER, env=env)
    # m1 is referenced, so it survives; m2 goes
    r = execute(store, p, DeleteBin(bin=mara), USER, env=env)
    assert mara not in p.bins_by_id
    assert m1 in p.media_by_id and m2 not in p.media_by_id
    assert [x.media for x in p.bins_by_id[s].references] == [m1]
    assert r.change.description == "deleted Mara (1 media)"


def test_delete_bin_drops_what_only_it_kept_alive(world):
    """A bin's own references do not keep its media, and a media that survived one deletion
    on the strength of another bin's reference goes when that bin is deleted too: nothing
    lingers in the library that no bin can reach."""
    store, p, env = world
    mara = add_bin(p, "Mara")
    m1 = add_media(p, mara)
    m2 = add_media(p, mara)
    execute(store, p, BindReference(bin=mara, media=m1), USER, env=env)  # references its own take
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, BindReference(bin=s, media=m2), USER, env=env)
    r = execute(store, p, DeleteBin(bin=mara), USER, env=env)
    assert m1 not in p.media_by_id and m2 in p.media_by_id
    assert r.change.description == "deleted Mara (1 media)"
    r = execute(store, p, DeleteBin(bin=s), USER, env=env)
    assert m2 not in p.media_by_id and p.media_by_id == {}
    assert r.change.description == "deleted Image 1 (1 media)"


def test_project_level_changes_record_only_the_entries_they_touched(world):
    """A delete used to carry the whole library on both sides; the log grew with the square of
    the project. Now a project-level diff lists the entries that moved and nothing else."""
    store, p, env = world
    b1 = add_bin(p, "Mara")
    b2 = add_bin(p, "Mara too")
    b3 = add_bin(p, "Untouched")
    m = add_media(p, b1)
    keep = add_media(p, b1)
    add_media(p, b3)
    p.bins_by_id[b2].media.append(m)
    p.bins_by_id[b1].selected = m

    ch = execute(store, p, DeleteMedia(media=m), USER, env=env).change
    assert set(ch.before) == {"media_by_id", "bins_by_id"} and set(ch.after) == {"media_by_id", "bins_by_id"}
    assert list(ch.before["media_by_id"]) == [m] and ch.after["media_by_id"] == {}
    assert set(ch.before["bins_by_id"]) == {b1, b2} and set(ch.after["bins_by_id"]) == {b1, b2}
    assert ch.before["bins_by_id"][b1]["media"] == [m, keep] and ch.after["bins_by_id"][b1]["media"] == [keep]
    assert ch.after["bins_by_id"][b1]["selected"] == keep

    ch = execute(store, p, DeleteBin(bin=b1), USER, env=env).change
    assert list(ch.before["bins_by_id"]) == [b1] and ch.after["bins_by_id"] == {}
    assert list(ch.before["media_by_id"]) == [keep] and ch.after["media_by_id"] == {}

    c = CreateBin()
    ch = execute(store, p, c, USER, env=env).change
    assert ch.before == {"bins_by_id": {}} and list(ch.after["bins_by_id"]) == [c._created]


def test_diff_nests_one_level_only():
    from thm_core.commands import _diff

    before = {"bins_by_id": {"b": {"media": ["m1", "m2"], "name": "x"}, "c": {"name": "c"}}, "name": "old", "counters": {"take": 1}}
    after = {"bins_by_id": {"b": {"media": ["m1"], "name": "x"}, "c": {"name": "c"}}, "name": "new", "counters": {"take": 1}}
    b, a = _diff(before, after)
    assert b == {"bins_by_id": {"b": {"media": ["m1", "m2"], "name": "x"}}, "name": "old"}
    assert a == {"bins_by_id": {"b": {"media": ["m1"], "name": "x"}}, "name": "new"}
    assert _diff({"x": {"a": 1}}, {"x": None}) == ({"x": {"a": 1}}, {"x": None})


# -- session gate ------------------------------------------------------------


def test_agent_needs_live_session(world):
    store, p, env = world
    s = add_bin(p, "Image 1", "img.json")
    cmd = SetBinField(bin=s, field="prompt", value="x", card=True)
    refused("no_session", lambda: execute(store, p, cmd, AGENT, env=env))
    waiting_session(p)
    refused("no_session", lambda: execute(store, p, cmd, AGENT, env=env))
    p.agent_session.status = "live"
    r = execute(store, p, cmd, AGENT, env=env)
    assert r.change.actor == "agent" and r.change.decision.status == "pending" and r.change.session == "as_test"
    assert [m.kind for m in r.messages] == ["change"]


def test_connect_session_rules(world):
    store, p, env = world
    refused("no_session", lambda: execute(store, p, ConnectSession(harness="claude-code"), AGENT, env=env))
    waiting_session(p)
    refused("wrong_actor", lambda: execute(store, p, ConnectSession(harness="claude-code"), USER, env=env))
    r = execute(store, p, ConnectSession(harness="claude-code"), AGENT, env=env)
    assert p.agent_session.status == "live" and p.agent_session.harness == "claude-code"
    assert r.change.target.type == "session" and r.change.before == {"status": "waiting", "harness": None, "connected": None}
    assert [m.kind for m in r.messages] == ["system"]
    refused("no_session", lambda: execute(store, p, ConnectSession(harness="again"), AGENT, env=env))


def test_remove_media_from_bin_keeps_the_record(world):
    store, p, env = world
    refs = add_bin(p, "session-refs", "")
    m = add_media(p, refs)
    p.bins_by_id[refs].selected = m
    r = execute(store, p, RemoveMediaFromBin(bin=refs, media=m), USER, env=env)
    assert p.bins_by_id[refs].media == [] and p.bins_by_id[refs].selected is None and m in p.media_by_id
    assert r.change.description == "removed media from session-refs" and r.change.before["media"] == [m]
    refused("not_in_bin", lambda: execute(store, p, RemoveMediaFromBin(bin=refs, media=m), USER, env=env))
    refused("unknown_id", lambda: execute(store, p, RemoveMediaFromBin(bin="b_nope", media=m), USER, env=env))


def test_start_session_posts_notes_with_refs_and_refuses_duplicates(world):
    store, p, env = world
    refused("wrong_actor", lambda: execute(store, p, StartSession(notes="go"), AGENT, env=env))
    refused("unknown_id", lambda: execute(store, p, StartSession(notes="go", refs_bin="b_nope"), USER, env=env))
    refs = add_bin(p, "session-refs", "")
    a = add_media(p, refs)
    b = add_media(p, refs)
    r = execute(store, p, StartSession(notes="finish Video 2 at 8s", refs_bin=refs), USER, env=env)
    assert p.agent_session.status == "waiting" and r.change.description == "session started"
    assert [(m.kind, m.text) for m in r.messages] == [
        ("user", "finish Video 2 at 8s"),
        ("system", "notes sent with 2 reference files, waiting for an agent to connect"),
    ]
    assert r.messages[0].attachments == [a, b]
    # the inbox carries the attachments to whichever harness connects
    msgs, _ = store.read_inbox(p, 0)
    assert msgs[-2].attachments == [a, b]
    refused("session_active", lambda: execute(store, p, StartSession(), USER, env=env))


def test_start_session_without_notes_posts_only_a_system_line(world):
    store, p, env = world
    r = execute(store, p, StartSession(), USER, env=env)
    assert [(m.kind, m.text) for m in r.messages] == [("system", "session started, waiting for an agent to connect")]
    execute(store, p, EndSession(), USER, env=env)
    r = execute(store, p, StartSession(notes="  "), USER, env=env)
    assert [m.kind for m in r.messages] == ["system"]
    # every message of the second session carries the new id
    msgs = store.read_messages(p)
    assert msgs[0].session != msgs[-1].session == p.agent_session.id


# -- project settings -----------------------------------------------------------


def test_set_project_settings_merges_and_describes(world):
    store, p, env = world
    r = execute(store, p, SetProjectSettings(width=720, height=1280, image_workflow="img.json", video_workflow="vid.json"), USER, env=env)
    d = p.defaults
    assert (d.width, d.height, d.image_workflow, d.video_workflow, d.video_length_s) == (720, 1280, "img.json", "vid.json", 5.0)
    assert d.configured
    assert r.change.description == "project settings - 720x1280 - image img - video vid"
    assert r.change.target.type == "project" and set(r.change.after) == {"defaults"}
    # only the given fields move; the rest survive the merge (the patch replaces the defaults object)
    r = execute(store, p, SetProjectSettings(image_prompt="  35mm grain ", video_negative="jitter"), USER, env=env)
    d = p.defaults
    assert (d.image_prompt, d.video_negative, d.width, d.image_workflow) == ("35mm grain", "jitter", 720, "img.json")
    assert r.change.description == "project settings - global prompts 2"
    r = execute(store, p, SetProjectSettings(project_name="Rail Dawn"), USER, env=env)
    assert p.name == "Rail Dawn" and p.slug == "rail-dawn-promo" and r.change.description == "project settings - renamed to Rail Dawn"
    r = execute(store, p, SetProjectSettings(project_name="Rail Dawn 2", video_length_s=8), USER, env=env)
    assert r.change.description == "project settings - renamed to Rail Dawn 2 - video length 8s"
    assert store.load(p.slug).defaults.image_prompt == "35mm grain"


def test_set_project_settings_refusals(world):
    store, p, env = world
    refused("bad_value", lambda: execute(store, p, SetProjectSettings(), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetProjectSettings(project_name="  "), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetProjectSettings(width=720), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetProjectSettings(width=0, height=10), USER, env=env))
    refused("bad_value", lambda: execute(store, p, SetProjectSettings(video_length_s=0), USER, env=env))
    refused("unknown_workflow", lambda: execute(store, p, SetProjectSettings(image_workflow="nope.json"), USER, env=env))


def test_agent_settings_change_rejects_back_to_before(world):
    store, p, env = world
    live_session(p)
    execute(store, p, SetProjectSettings(width=1024, height=1024, image_prompt="old"), USER, env=env)
    r = execute(store, p, SetProjectSettings(project_name="Agent Name", width=720, height=1280, image_prompt="new", card=True), AGENT, env=env)
    assert r.change.decision.status == "pending" and p.name == "Agent Name" and p.defaults.width == 720
    from thm_core import decide

    decide(store, p, r.change.id, "reject", "keep mine", USER, env=env)
    assert p.name == "Rail Dawn Promo" and (p.defaults.width, p.defaults.height, p.defaults.image_prompt) == (1024, 1024, "old")


def test_end_session_from_either_actor_then_agent_refused(world):
    store, p, env = world
    live_session(p)
    s = add_bin(p, "Image 1", "img.json")
    execute(store, p, EndSession(), AGENT, env=env)
    assert p.agent_session.status == "ended" and p.agent_session.ended
    refused("no_session", lambda: execute(store, p, SetBinField(bin=s, field="prompt", value="x"), AGENT, env=env))
    execute(store, p, StartSession(), USER, env=env)
    execute(store, p, EndSession(), USER, env=env)
    assert p.agent_session.status == "ended"


# -- inbox gate --------------------------------------------------------------


def test_generate_inbox_gate(world):
    store, p, env = world
    live_session(p)
    mara = add_bin(p, "Mara")
    s = add_bin(p, "Image 1", "img.json")
    bind(p, s, add_media(p, mara))
    p.bins_by_id[s].prompt = ".mara at dawn"
    refused("inbox_cursor_required", lambda: execute(store, p, Generate(bin=s), AGENT, env=env))
    execute(store, p, PostMessage(text="warmer please"), USER, env=env)
    exc = refused("inbox_behind", lambda: execute(store, p, Generate(bin=s), CommandContext(actor="agent", inbox_cursor=0), env=env))
    assert [m["text"] for m in exc.extra["messages"]] == ["warmer please"] and exc.extra["cursor"] == 1
    r = execute(store, p, Generate(bin=s), CommandContext(actor="agent", inbox_cursor=1), env=env)
    g = r.generation
    assert g.status == "queued" and g.prompt_typed == ".mara at dawn" and g.prompt_submitted == "<Picture 1> at dawn"
    assert g.references[0].handle == "mara" and g.references[0].ordinal == 1 and g.references[0].slot == "10"
    assert g.paths_by_slot == {"10": p.media_by_id[g.references[0].media].path}
    assert r.change.generation == g.id
    assert store.read_generations(p)[0].id == g.id
    # the user does not need a cursor
    execute(store, p, Generate(bin=s), USER, env=env)


def test_agent_own_messages_do_not_block_generate(world):
    store, p, env = world
    live_session(p)
    s = add_bin(p, "Image 1", "img.json")
    cursor = store.message_cursor(p)
    execute(store, p, PostMessage(text="working", kind="agent"), AGENT, env=env)
    execute(store, p, SetBinField(bin=s, field="prompt", value="x"), AGENT, env=env)  # adds a change message
    execute(store, p, Generate(bin=s), CommandContext(actor="agent", inbox_cursor=cursor), env=env)


# -- feed messages -----------------------------------------------------------


def test_user_change_during_live_session_posts_override_note(world):
    store, p, env = world
    s = add_bin(p, "Video 1", "vid.json", length_s=5)
    r = execute(store, p, SetBinField(bin=s, field="length_s", value=8), USER, env=env)
    assert r.messages == []
    live_session(p)
    r = execute(store, p, SetBinField(bin=s, field="length_s", value=3), USER, env=env)
    assert [m.kind for m in r.messages] == ["system"]
    assert r.messages[0].text == "you: Video 1 - length 8s to 3s" and r.messages[0].change == r.change.id
    assert r.messages[0].session == "as_test"


def test_post_message_without_session_is_kept_for_the_next_inbox_read(world):
    store, p, env = world
    r = execute(store, p, PostMessage(text="hands wrong on this one"), USER, env=env)
    assert r.change is None and r.messages[0].session is None
    msgs, _ = store.read_inbox(p, 0)
    assert [m.text for m in msgs] == ["hands wrong on this one"]
    assert store.read_changes(p) == {}


def test_unseen_clears_when_the_media_leaves(world):
    """The new-take marker follows the media out: removing it from the gallery or deleting it
    everywhere both drop it, so the explorer never counts something that is not there."""
    store, p, env = world
    mara = add_bin(p, "Mara")
    m1 = add_media(p, mara)
    m2 = add_media(p, mara)
    p.bins_by_id[mara].unseen = [m1, m2]
    execute(store, p, RemoveMediaFromBin(bin=mara, media=m1), USER, env=env)
    assert p.bins_by_id[mara].unseen == [m2]
    execute(store, p, DeleteMedia(media=m2), USER, env=env)
    assert p.bins_by_id[mara].unseen == []
