from pathlib import Path

from thm_core import TokenProfile, assemble, derive_token_profile, load_token_profile, resolve_references, workflow_capacity
from thm_core.workflow_capabilities import load_workflow_dict
from thm_core_fixtures import (
    PARTNER_WORKFLOW,
    TOKEN_SIDECAR,
    add_bin,
    add_media,
    bind,
    image_workflow,
    make_project,
    ref_stack_workflow,
    sidecar_home,
    video_ref_workflow,
    video_workflow,
    workflows_dir,
)

REPO_WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"


def _world(tmp_path, *, sidecar=True):
    p = make_project()
    it = add_bin(p, "Mara")
    loc = add_bin(p, "Rail Dawn")
    s = add_bin(p, "Image 1", "img.json")
    wdir = workflows_dir(tmp_path, {"img.json": image_workflow(3), "vid.json": video_workflow()}, {"img.thm.toml": TOKEN_SIDECAR} if sidecar else None)
    return p, it, loc, s, wdir


def test_ordinals_count_per_media_type(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    r1 = bind(p, s, add_media(p, it, type="image"))
    r2 = bind(p, s, add_media(p, loc, type="video"))
    r3 = bind(p, s, add_media(p, loc, type="image"))
    resolved = resolve_references(p, p.bins_by_id[s], isfile=lambda _: True)
    assert [(r.handle, r.media_type, r.ordinal) for r in resolved] == [("mara", "image", 1), ("rail-dawn", "video", 1), ("rail-dawn.2", "image", 2)]
    assert [r.reference.id for r in resolved] == [r1.id, r2.id, r3.id]


def test_missing_file_has_no_ordinal_and_next_takes_its_rank(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    bind(p, s, add_media(p, it, path="C:/gone.png"))
    bind(p, s, add_media(p, loc, path="C:/here.png"))
    resolved = resolve_references(p, p.bins_by_id[s], isfile=lambda path: path == "C:/here.png")
    assert (resolved[0].exists, resolved[0].ordinal, resolved[0].note) == (False, None, "missing file")
    assert (resolved[1].exists, resolved[1].ordinal) == (True, 1)
    out = assemble(p, p.bins_by_id[s], resolved, workflows_dir=wdir)
    assert [w.kind for w in out.warnings if w.kind == "missing_file"] == ["missing_file"]


def test_sidecar_profile_rewrites_prompt_per_design_example(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    bind(p, s, add_media(p, it))
    bind(p, s, add_media(p, loc))
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara stands at .rail-dawn as the light breaks"
    bin_.negative = "blurry"
    resolved = resolve_references(p, bin_, isfile=lambda _: True)
    out = assemble(p, bin_, resolved, workflows_dir=wdir, capacity=workflow_capacity("img.json", wdir))
    assert out.prompt_typed == bin_.prompt
    assert out.prompt_submitted == "<Picture 1> stands at <Picture 2> as the light breaks"
    assert out.negative_submitted == "blurry"
    assert [(r.handle, r.ordinal, r.slot) for r in out.references] == [("mara", 1, "10"), ("rail-dawn", 2, "11")]
    assert out.paths_by_slot == {"10": p.media_by_id[bin_.references[0].media].path, "11": p.media_by_id[bin_.references[1].media].path}
    assert out.warnings == []


def test_video_tokens_and_prelude_from_sidecar(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    (sidecar_home(wdir) / "img.thm.toml").write_text(TOKEN_SIDECAR + '\n[prelude]\nline = "image{n}: {item} ({type})"\n', encoding="utf-8")
    bind(p, s, add_media(p, it))
    bind(p, s, add_media(p, loc, type="video"))
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara walks like .rail-dawn"
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir)
    assert out.prompt_submitted == "<Picture 1> walks like <Video 1>"
    assert out.prelude == ["image1: Mara (image)", "image1: Rail Dawn (video)"]


def test_no_sidecar_uses_bin_names_and_skips_unused_warning(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path, sidecar=False)
    bind(p, s, add_media(p, it))
    bind(p, s, add_media(p, loc))
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara at dawn."
    profile = load_token_profile("img.json", wdir)
    assert not profile.found and not profile.addresses_by_token
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir)
    assert out.prompt_submitted == "Mara at dawn."
    assert out.warnings == []


def test_unknown_token_warning_and_sentence_end_period(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    bind(p, s, add_media(p, it))
    bin_ = p.bins_by_id[s]
    bin_.prompt = "see .mara. then .bob arrives"
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir)
    assert [(w.kind, w.handle) for w in out.warnings] == [("unknown_token", "bob")]
    assert out.prompt_submitted == "see <Picture 1>. then .bob arrives"


def test_unused_handle_warning_present_and_absent(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    bind(p, s, add_media(p, it))
    bind(p, s, add_media(p, loc))
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara alone"
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir)
    assert [(w.kind, w.handle) for w in out.warnings] == [("unused_handle", "rail-dawn")]
    bin_.negative = "not like .rail-dawn"
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir)
    assert out.warnings == []
    assert out.negative_submitted == "not like <Picture 2>"


def test_capacity_from_real_workflow_and_sidecar_clip(tmp_path):
    cap = workflow_capacity(str(REPO_WORKFLOWS / "pixa-four-image_vague.json"), REPO_WORKFLOWS)
    assert cap.found and cap.image_slots == 4 and len(cap.slot_node_ids) == 4 and cap.video_slots == 0
    wdir = workflows_dir(tmp_path, {"img.json": image_workflow(4)}, {"img.thm.toml": '[limits]\nimage_slots = 2\n'})
    clipped = workflow_capacity("img.json", wdir)
    assert clipped.image_slots == 2 and len(clipped.slot_node_ids) == 4 and not clipped.addresses_by_token
    assert not workflow_capacity("nope.json", wdir).found


def test_video_workflow_capacity_and_start_end_slots(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    cap = workflow_capacity("vid.json", wdir)
    assert (cap.image_slots, cap.supports_start_frame, cap.supports_end_frame, cap.slot_node_ids) == (2, True, True, [])
    assert cap.kind == "video" and workflow_capacity("img.json", wdir).kind == "image"
    v = add_bin(p, "Video 1", "vid.json", length_s=5)
    bind(p, v, add_media(p, it, path="C:/a.png"))
    bind(p, v, add_media(p, loc, path="C:/b.png"))
    out = assemble(p, p.bins_by_id[v], resolve_references(p, p.bins_by_id[v], isfile=lambda _: True), workflows_dir=wdir, capacity=cap)
    assert out.paths_by_slot == {"start": "C:/a.png", "end": "C:/b.png"}
    assert [r.slot for r in out.references] == ["start", "end"]


def test_reorder_changes_ordinals_and_submitted_prompt_only(tmp_path):
    p, it, loc, s, wdir = _world(tmp_path)
    bind(p, s, add_media(p, it))
    bind(p, s, add_media(p, loc))
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara then .rail-dawn"
    first = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir)
    bin_.references.reverse()
    second = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir)
    assert first.prompt_submitted == "<Picture 1> then <Picture 2>"
    assert second.prompt_submitted == "<Picture 2> then <Picture 1>"
    assert first.prompt_typed == second.prompt_typed == bin_.prompt
    assert [r.handle for r in bin_.references] == ["rail-dawn", "mara"]


# -- derived token profiles (phase 2: sidecar is an override) ---------------


def test_derive_image_notation_from_reference_latent_chain(tmp_path):
    profile = derive_token_profile(load_workflow_dict(REPO_WORKFLOWS / "pixa-four-image_vague.json"))
    assert (profile.image, profile.video, profile.found, profile.sidecar) == ("image{n}", None, True, False)
    assert workflow_capacity(str(REPO_WORKFLOWS / "pixa-four-image_vague.json"), REPO_WORKFLOWS).addresses_by_token

    p = make_project()
    it = add_bin(p, "Mara")
    loc = add_bin(p, "Rail Dawn")
    wdir = workflows_dir(tmp_path, {"stack.json": ref_stack_workflow(4)})
    s = add_bin(p, "Image 1", "stack.json")
    bind(p, s, add_media(p, it))
    bind(p, s, add_media(p, loc))
    bin_ = p.bins_by_id[s]
    bin_.prompt = ".mara then .rail-dawn"
    cap = workflow_capacity("stack.json", wdir)
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir, capacity=cap)
    assert out.prompt_submitted == "image1 then image2"
    assert [r.slot for r in out.references] == ["10", "11"]
    assert out.warnings == []
    bin_.prompt = ".mara alone"
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir, capacity=cap)
    assert [(w.kind, w.handle) for w in out.warnings] == [("unused_handle", "rail-dawn")]


def test_derive_api_notation_from_partner_class():
    assert derive_token_profile(PARTNER_WORKFLOW) == TokenProfile(image="<Picture {n}>", video="<Video {n}>", found=True)
    shipped = derive_token_profile(load_workflow_dict(REPO_WORKFLOWS / "THM-video_minimax_h3_r2v.json"))
    assert (shipped.image, shipped.video) == ("<Picture {n}>", "<Video {n}>")
    cap = workflow_capacity(str(REPO_WORKFLOWS / "THM-video_minimax_h3_r2v.json"), REPO_WORKFLOWS)
    assert (cap.image_slots, cap.slot_node_ids, cap.video_slots, cap.addresses_by_token) == (2, [], 0, True)


def test_derive_positional_when_no_chain_and_no_partner(tmp_path):
    assert derive_token_profile(image_workflow(2)) == TokenProfile()
    wdir = workflows_dir(tmp_path, {"img.json": image_workflow(2)})
    assert load_token_profile("img.json", wdir) == TokenProfile()
    assert load_token_profile("missing.json", wdir) == TokenProfile()


def test_sidecar_overrides_derived_fields_only_where_set(tmp_path):
    wdir = workflows_dir(
        tmp_path,
        {"stack.json": ref_stack_workflow(4)},
        {"stack.thm.toml": '[tokens]\nimage = "<Picture {n}>"\n\n[limits]\nimage_slots = 2\n'},
    )
    profile = load_token_profile("stack.json", wdir)
    assert (profile.image, profile.video, profile.image_slots, profile.sidecar, profile.found) == ("<Picture {n}>", None, 2, True, True)
    cap = workflow_capacity("stack.json", wdir)
    assert cap.image_slots == 2 and len(cap.slot_node_ids) == 4 and cap.addresses_by_token

    (sidecar_home(wdir) / "stack.thm.toml").write_text("[limits]\nimage_slots = 3\n", encoding="utf-8")
    profile = load_token_profile("stack.json", wdir)
    assert (profile.image, profile.image_slots, profile.sidecar) == ("image{n}", 3, True)
    assert workflow_capacity("stack.json", wdir).image_slots == 3


def test_video_reference_slots_flow_into_capacity_and_paths(tmp_path):
    wdir = workflows_dir(tmp_path, {"vidref.json": video_ref_workflow(2)})
    cap = workflow_capacity("vidref.json", wdir)
    assert (cap.video_slots, cap.video_slot_node_ids, cap.image_slots, cap.slot_node_ids) == (2, ["40", "41"], 2, [])

    p = make_project()
    it = add_bin(p, "Mara")
    loc = add_bin(p, "Rail Dawn")
    v = add_bin(p, "Video 1", "vidref.json", length_s=5)
    bind(p, v, add_media(p, it, path="C:/a.png"))
    bind(p, v, add_media(p, loc, type="video", path="C:/v.mp4"))
    bind(p, v, add_media(p, loc, path="C:/b.png"))
    bin_ = p.bins_by_id[v]
    out = assemble(p, bin_, resolve_references(p, bin_, isfile=lambda _: True), workflows_dir=wdir, capacity=cap)
    assert [r.slot for r in out.references] == ["start", "40", "end"]
    assert out.paths_by_slot == {"start": "C:/a.png", "40": "C:/v.mp4", "end": "C:/b.png"}

    (sidecar_home(wdir, video=True) / "vidref.thm.toml").write_text("[limits]\nvideo_slots = 1\n", encoding="utf-8")
    clipped = workflow_capacity("vidref.json", wdir)
    assert clipped.video_slots == 1 and clipped.video_slot_node_ids == ["40", "41"]
