"""
The pipeline around the network, on synthetic plans with exact labels: what is
rasterized for training comes back out of post-processing, the evaluator
scores it, labels survive a trip through COCO, and the server speaks the API
the backend calls.
"""

import json
import time

import numpy as np
import pytest
import torch
from fastapi.testclient import TestClient

from takeoff_model.coco import export, import_
from takeoff_model.config import load_config
from takeoff_model.evaluate import evaluate
from takeoff_model.labels import rasterize, read_labels
from takeoff_model.net import build
from takeoff_model.predict import Predictor, name_detections
from takeoff_model.pages import Span
from takeoff_model.serve import create_app
from takeoff_model.synth import make_plan
from takeoff_model.vectorize import vectorize

CFG = load_config()
CLASSES = CFG["classes"]


@pytest.fixture(scope="module")
def plan():
    image, labels = make_plan(10)  # has rooms, walls, doors, windows, columns and a stair
    return image, labels


def _one_hot(mask: np.ndarray) -> np.ndarray:
    return np.eye(len(CLASSES), dtype=np.float16)[mask]


def test_rasterized_labels_come_back_out_of_post_processing(plan, tmp_path):
    image, labels = plan
    page = labels["pages"][0]
    mask = rasterize(page, image.shape[:2], CLASSES)
    mm_px = page["scale"]["denominator"] * 25.4 / 300
    detections = vectorize(_one_hot(mask), CLASSES, mm_px, True, CFG["vectorize"], 0)

    predicted = {**labels, "pages": [{**page, "detections": detections}]}
    (tmp_path / "truth.json").write_text(json.dumps(labels))
    (tmp_path / "pred.json").write_text(json.dumps(predicted))
    totals = evaluate([(tmp_path / "truth.json", tmp_path / "pred.json")], CLASSES)

    for name in ("space", "door", "window", "column", "stair"):
        matched, n_truth, n_pred = totals["objects"][name]
        assert n_truth and matched == n_truth == n_pred, name
    for name in ("space", "wall", "door", "window"):
        inter, union = totals["pixels"][name]
        assert inter / union > 0.9, name
    truth_m, pred_m = totals["wall_length_m"]
    assert abs(pred_m / truth_m - 1) < 0.1
    rooms = [d for d in detections if d["class_name"] == "space"]
    assert all(d["area_m2"] > 1 for d in rooms) and all(d["coordinate_space"] == "page_pixels" for d in detections)


def test_openings_are_gaps_in_walls_and_rooms_what_walls_enclose():
    import cv2

    index = {name: i for i, name in enumerate(CLASSES)}
    mask = np.zeros((200, 300), np.uint8)
    mask[60:140, 60:240] = index["space"]
    for x0, y0, x1, y1 in ((50, 50, 249, 59), (50, 140, 249, 149), (50, 50, 59, 149), (240, 50, 249, 149)):
        mask[y0:y1 + 1, x0:x1 + 1] = index["wall"]
    mask[90:110, 50:60] = index["space"]  # a 500 mm gap in the 250 mm left wall ...
    swing = np.zeros_like(mask)
    cv2.ellipse(swing, (60, 90), (20, 20), 0, 0, 90, 1, -1)  # ... that the network only sees the swing of
    mask[swing.astype(bool)] = index["door"]
    settings, mm_per_px = CFG["vectorize"], 25

    found = vectorize(_one_hot(mask), CLASSES, mm_per_px, True, settings, 0)
    doors = [d for d in found if d["class_name"] == "door"]
    rooms = [d for d in found if d["class_name"] == "space"]
    assert len(doors) == 1 and doors[0]["bbox"] == [50, 90, 59, 109]  # in the wall, not on the swing
    assert len(rooms) == 1 and rooms[0]["bbox"][0] == 60 and rooms[0]["bbox"][2] == 239

    mask[50:60, 80:200] = index["space"]  # a 3 m gap nothing explains: the room is open to the outside
    found = vectorize(_one_hot(mask), CLASSES, mm_per_px, True, settings, 0)
    assert not [d for d in found if d["class_name"] == "space"]


def test_names_come_from_the_text_on_the_page():
    room = {"class_name": "space", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]], "bbox": [0, 0, 100, 100]}
    door = {"class_name": "door", "polygon": [[200, 0], [220, 0], [220, 10], [200, 10]], "bbox": [200, 0, 220, 10]}
    spans = [Span("KITCHEN", 30, 40, 70, 50, 12), Span("3600", 30, 60, 50, 66, 14), Span("D2", 205, 15, 215, 25, 8)]
    name_detections([room, door], spans)
    assert room["name"] == "KITCHEN" and door["name"] == "D2"


def test_labels_survive_a_round_trip_through_coco(plan, tmp_path):
    import cv2

    image, labels = plan
    root = tmp_path / "data"
    (root / "sources").mkdir(parents=True)
    (root / "labels").mkdir()
    cv2.imwrite(str(root / "sources" / "plan.png"), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    (root / "labels" / "plan.json").write_text(json.dumps(labels))

    export(root, tmp_path / "coco", CLASSES, CFG["render"], unlabelled_only=False)
    import_(tmp_path / "coco" / "annotations.json", tmp_path / "back")

    before, after = read_labels(root / "labels" / "plan.json")[0], read_labels(tmp_path / "back" / "plan.json")[0]
    assert [d["class_name"] for d in after["detections"]] == [d["class_name"] for d in before["detections"]]
    # Exported at the training resolution: the same place on the page, whatever the image size.
    size = lambda page: [page["image_size"]["width"], page["image_size"]["height"]]
    assert np.allclose(np.divide(after["detections"][3]["polygon"], size(after)),
                       np.divide(before["detections"][3]["polygon"], size(before)), atol=1e-3)


def test_predictor_writes_a_takeoff_page(plan, tmp_path, monkeypatch):
    import cv2

    image, _ = plan
    path = tmp_path / "plan.png"
    cv2.imwrite(str(path), cv2.cvtColor(image[:900, :1200], cv2.COLOR_RGB2BGR))
    cfg = {**CFG, "model": {**CFG["model"], "encoder": "resnet18", "encoder_weights": None},
           "predict": {**CFG["predict"], "tile": 512, "overlap": 128}}
    checkpoint = tmp_path / "tiny.pt"
    torch.save({"state_dict": build(cfg, len(CLASSES), pretrained=False).state_dict(), "config": cfg, "classes": CLASSES, "epoch": 0},
               checkpoint)

    result = Predictor(str(checkpoint), "cpu").predict(path, None, 100)
    page = result["pages"][0]
    assert result["status"] == "succeeded" and result["model"]["encoder"] == "resnet18"
    assert page["page_index"] == 0 and page["scale"]["source"] == "user" and page["scale"]["meters_per_pixel"] > 0
    assert page["image_size"]["width"] > 0 and isinstance(page["detections"], list)


class StubPredictor:
    def predict(self, path, pages, denominator, document_id):
        assert path.exists()
        return {"id": document_id, "status": "succeeded", "pages": [{"page_index": 0, "image_size": {"width": 10, "height": 10},
                                                                      "scale": {"meters_per_pixel": 0}, "detections": []}]}


def test_server_speaks_the_takeoff_api_the_backend_calls():
    client = TestClient(create_app(StubPredictor(), api_key="secret"))
    auth = {"Authorization": "Bearer secret"}
    assert client.post("/v1.1/takeoffs", files={"file": ("p.pdf", b"%PDF-1.4")}).status_code == 401

    created = client.post("/v1.1/takeoffs", headers={**auth, "Idempotency-Key": "job:1"}, files={"file": ("page-1.pdf", b"%PDF-1.4")})
    assert created.status_code == 202 and created.json()["status"] in ("queued", "running", "succeeded")
    takeoff_id = created.json()["id"]
    again = client.post("/v1.1/takeoffs", headers={**auth, "Idempotency-Key": "job:1"}, files={"file": ("page-1.pdf", b"%PDF-1.4")})
    assert again.json()["id"] == takeoff_id

    for _ in range(50):
        if client.get(f"/v1.1/takeoffs/{takeoff_id}", headers=auth).json()["status"] == "succeeded":
            break
        time.sleep(0.05)
    result = client.get(f"/v1.1/takeoffs/{takeoff_id}/results", headers=auth).json()
    assert result["pages"][0]["page_index"] == 0
    missing = client.get("/v1.1/takeoffs/tk_nope", headers=auth)
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
