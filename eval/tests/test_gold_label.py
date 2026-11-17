"""The labelling page of a customer's kit: the empty labelling by file names, the sheets it shows, the saved file."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import gold_label
import pytest
from PIL import Image
from synth.truth import KitTruth

NAMES = [
    "ШСМГ.01.030 СБ Мост.jpg",
    "ШСМГ.01.030 СП Мост(1).jpg",
    "ШСМГ.01.030 СП Мост(2).jpg",
    "ШСМГ.01.062 Втулка.jpg",
]


@pytest.fixture
def arm(tmp_path):
    masked = tmp_path / "masked" / "arm-9"
    masked.mkdir(parents=True)
    sheets = []
    for i, name in enumerate(NAMES):
        written = None if i == 3 else name.replace(".jpg", ".png")
        if written:
            Image.new("L", (420, 297), 255).save(masked / written)
        sheets.append({"file": name, "page": 1, "written": written})
    (masked / "manifest.json").write_text(json.dumps({"sheets": sheets}), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"arm-9": dict.fromkeys(NAMES, "0" * 64)}), encoding="utf-8")
    return tmp_path


def test_files_of_one_name_numbered_in_brackets_are_sheets_of_one_document_and_the_code_gives_the_kind():
    truth = gold_label.template([*NAMES, "ШСМГ.01.070 СП Тяга.jpg", "ШСМГ.01.070 СП Тяга(1).jpg"])
    kinds = {name: (doc.kind, doc.sheets, doc.sheet_files) for name, doc in truth.documents.items()}
    assert kinds == {
        "ШСМГ.01.030 СБ Мост.jpg": ("assembly_drawing", 1, {}),
        "ШСМГ.01.030 СП Мост(1).jpg": (
            "specification",
            2,
            {"ШСМГ.01.030 СП Мост(1).jpg": 1, "ШСМГ.01.030 СП Мост(2).jpg": 2},
        ),
        "ШСМГ.01.062 Втулка.jpg": ("part_drawing", 1, {}),
        "ШСМГ.01.070 СП Тяга.jpg": ("specification", 1, {}),
        "ШСМГ.01.070 СП Тяга(1).jpg": ("specification", 1, {}),
    }
    assert all(doc.designation == "" and doc.origin == "raster" for doc in truth.documents.values())


def test_the_page_shows_masked_sheets_only_and_saves_a_checked_labelling_keeping_the_last_one(arm):
    kit = gold_label.Kit(arm, "arm-9", "a")
    state = kit.state()
    assert [s["file"] for s in state["sheets"]] == NAMES[:3] and state["unmasked"] == [NAMES[3]]
    assert not state["saved"] and state["problems"] == []
    truth = state["truth"]
    truth["documents"]["ШСМГ.01.062 Втулка.jpg"]["parameters"] = [
        {"category": "roughness", "name": "Ra 3,2", "it_grade": 7}
    ]
    assert kit.save(truth) == ["ШСМГ.01.062 Втулка.jpg: квалитет IT7 у параметра roughness «Ra 3,2»"]
    truth["root"] = "ШСМГ.01.030"
    kit.save(truth)
    assert KitTruth.read(arm / "gold" / "arm-9.a.json").root == "ШСМГ.01.030"
    assert KitTruth.read(arm / "gold" / "arm-9.a.bak").root == ""
    truth["documents"]["Чужой.jpg"] = truth["documents"].pop("ШСМГ.01.062 Втулка.jpg")
    assert kit.save(truth)[0] == "Чужой.jpg — нет в манифесте комплекта"


def test_the_server_gives_the_kit_and_its_sheets_and_refuses_a_labelling_out_of_the_format(arm):
    server = ThreadingHTTPServer(("127.0.0.1", 0), gold_label.handler(gold_label.Kit(arm, "arm-9", "a")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        state = json.load(urllib.request.urlopen(f"{url}/kit"))
        assert state["kit"] == "arm-9" and "roughness" in state["categories"]
        assert urllib.request.urlopen(f"{url}/sheet/0").headers["Content-Type"] == "image/png"
        with pytest.raises(urllib.error.HTTPError) as missing:
            urllib.request.urlopen(f"{url}/sheet/9")
        assert missing.value.code == 404
        bad = urllib.request.Request(f"{url}/save", data=json.dumps({"root": 1}).encode(), method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(bad)
        assert refused.value.code == 422 and not (arm / "gold" / "arm-9.a.json").exists()
    finally:
        server.shutdown()
