"""Absolute reference paths become ComfyUI-annotated references before submit, for the video
loaders that validate like ``LoadImage`` as well as ``LoadImage`` itself."""

from scripts import workflow_controls as wc


def node(class_type, inputs, title=""):
    return {"_meta": {"title": title}, "class_type": class_type, "inputs": dict(inputs)}


def test_video_loaders_under_the_output_tree_get_the_output_annotation(tmp_path):
    out = tmp_path / "output"
    (out / "proj" / "s_1").mkdir(parents=True)
    clip = out / "proj" / "s_1" / "s_1_00001_.mp4"
    clip.write_bytes(b"x")
    still = out / "proj" / "it_1" / "it_1_00001_.png"
    still.parent.mkdir(parents=True)
    still.write_bytes(b"x")
    wf = {
        "1": node("LoadImage", {"image": str(still)}, "THM-ImageReference"),
        "2": node("VHS_LoadVideo", {"video": str(clip), "force_rate": 0}, "THM-VideoReference"),
        "3": node("LoadVideo", {"file": str(clip)}, "THM-VideoReference"),
        "4": node("VHS_LoadVideoPath", {"video": str(clip)}, "THM-VideoReference"),  # free path: untouched
        "5": node("VHS_LoadVideo", {"video": "already/there.mp4 [input]"}),
    }
    n = wc.annotate_comfy_image_inputs(wf, output_root=str(out), api_base="")
    assert n == 3
    assert wf["1"]["inputs"]["image"] == "proj/it_1/it_1_00001_.png [output]"
    assert wf["2"]["inputs"]["video"] == "proj/s_1/s_1_00001_.mp4 [output]"
    assert wf["3"]["inputs"]["file"] == "proj/s_1/s_1_00001_.mp4 [output]"
    assert wf["4"]["inputs"]["video"] == str(clip)
    assert wf["5"]["inputs"]["video"] == "already/there.mp4 [input]"


def test_video_outside_every_root_is_left_when_no_upload_target(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    elsewhere = tmp_path / "imports" / "clip.mp4"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes(b"x")
    wf = {"2": node("VHS_LoadVideo", {"video": str(elsewhere)})}
    assert wc.annotate_comfy_image_inputs(wf, output_root=str(out), api_base="") == 0
    assert wf["2"]["inputs"]["video"] == str(elsewhere)
