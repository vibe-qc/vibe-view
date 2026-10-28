"""Consumer checks use exact QC-produced values, never replacement graph weights."""

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from vibeview.chemical_space import ChemicalSpace
from vibeview.chemical_space_panel import load, select

FIXTURES = Path(__file__).parent / "fixtures" / "chemical_space"


def dataset():
    return json.loads((FIXTURES / "producer.json").read_text())


def test_channel_scores_match_exact_producer():
    space = ChemicalSpace.load(dataset())
    expected = json.loads((FIXTURES / "expected.json").read_text())["pair"]
    for key, value in expected.items():
        assert space.pair(0, 1)[key] == pytest.approx(value, abs=1e-14)
    assert space.neighbours(0, 2) == [1, 2]
    assert space.points.shape == (3, 2)
    assert space.nearest_point(*space.pixels()[2]) == 2
    assert space.image("index").startswith("data:image/png;base64,")


@pytest.mark.parametrize(
    "change", ["geometry", "variant", "channels", "parameters", "duplicate", "size"]
)
def test_malformed_or_mismatched_payload_refused(change):
    data = dataset()
    r = data["records"][1]
    if change == "geometry":
        r["coordinates_angstrom"][1][2] += 0.1
    if change == "variant":
        r["fingerprint"]["variant"] = "toney2026"
    if change == "channels":
        r["fingerprint"]["channels"][0][0] = float("nan")
    if change == "parameters":
        r["fingerprint"]["parameters"]["table_sha256"] = "bad"
    if change == "duplicate":
        r["id"] = data["records"][0]["id"]
    if change == "size":
        r["fingerprint"]["size_component"] = 2
    with pytest.raises(ValueError):
        ChemicalSpace.load(data)


class State(SimpleNamespace):
    def flush(self):
        pass


def test_5000_records_async_event_loop_and_selection(tmp_path):
    data = dataset()
    original = data["records"]
    data["records"] = [dict(copy.deepcopy(original[i % 3]), id=f"frame-{i}") for i in range(5000)]
    path = tmp_path / "trajectory-fingerprints.json"
    path.write_text(json.dumps(data))
    state = State(
        chemspace_path=str(path),
        chemspace_variant="quantile-scale",
        chemspace_busy=False,
        chemspace_k=10,
    )
    calls = []
    viewer = SimpleNamespace(
        server=SimpleNamespace(state=state),
        files=SimpleNamespace(load_file_from_bytes=lambda *args: calls.append(args)),
    )

    async def exercise():
        task = asyncio.create_task(load(viewer))
        ticks = 0
        while not task.done():
            await asyncio.sleep(0.001)
            ticks += 1
        await task
        return ticks

    assert asyncio.run(exercise()) > 2
    assert state.chemspace_count == 5000
    assert not state.chemspace_busy
    select(viewer, 4999)
    assert state.chemspace_label == "frame-4999"
    import base64

    xyz = base64.b64decode(calls[-1][0]).decode()
    assert "frame-4999" in xyz and "F " in xyz
    assert len(state.chemspace_neighbours) == 10
    assert len(state.chemspace_pair) == 5


def test_neighbour_limits_and_single_point_refused():
    space = ChemicalSpace.load(dataset())
    for k in (0, 101):
        with pytest.raises(ValueError):
            space.neighbours(0, k)
    with pytest.raises(ValueError):
        space.pair(-1, 1)
    data = dataset()
    data["records"] = data["records"][:1]
    with pytest.raises(ValueError):
        ChemicalSpace.load(data)
