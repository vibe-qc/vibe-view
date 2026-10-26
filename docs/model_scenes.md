# Multi-model scene API

`vibeview.model_scene.ModelScene` maintains several structures in a common
Cartesian coordinate system. Each model has a unique string identifier,
visibility, colour and representation. Model imports are detached copies;
changing a caller's structure does not change the scene.

The example below reads the first structure from two QVF archives. Use
`reader.read_structure("section-id")` when an archive contains several
structures and you need a particular one.

```python
import numpy as np
from vibeview import QVFReader
from vibeview.model_scene import ModelScene

scene = ModelScene()
with QVFReader("reference.qvf") as reader:
    scene.add("reference", reader.read_structure())
with QVFReader("mobile.qvf") as reader:
    scene.add("mobile", reader.read_structure())
scene.appearance("mobile", colour="#e89c28", style="wireframe")
scene.transform("mobile", np.eye(3), np.array([1., 0., 0.]))
scene.select('model reference and not element H')
scene.name_group("reference heavy atoms")
```

Selection expressions use the [shared grammar](selection_language.md). In a
scene, `index` refers to the one-based flattened atom list in model insertion
order. Returned atom references are `(model_identifier, zero_based_local_index)`.
These references remain stable when another model is moved or removed. Selections
include hidden models; visibility changes drawing, not membership. Radius queries
use the current transformed coordinates, without periodic images. Removing a model
prunes its references from the current selection and named groups.

`transform(model, rotation, translation)` requires a proper orthonormal rotation.
It transforms atoms and lattice vectors consistently, retaining integer bond
images; translation applies to atoms. Reflections, scaling and nonfinite inputs
are refused. `align_model(model, mobile_indices, target_references)` fits explicit
ordered atom correspondences using proper Kabsch alignment, transforms the entire
mobile model and returns RMSD. Correspondences must be unique and equally sized.
No atom mapping is inferred; collinear or very small selections may leave the
rotation underdetermined even when RMSD is zero.

## Save and replay

`scene.to_dict()` is a versioned JSON-compatible scene snapshot. Restore it with
`ModelScene.from_dict(snapshot)`. `scene.script()` produces a JSON event log;
`ModelScene.replay(script)` reconstructs the scene, including model appearance,
rigid transforms, selection and named groups. Structures and metadata are embedded
in the import event so replay does not reopen a potentially changed source file.
A loaded scene starts its log with an embedded restore event. Logs contain data,
not executable Python; unknown events and versions are refused.

```python
import json
from pathlib import Path

Path("scene.json").write_text(
    json.dumps(scene.to_dict(), allow_nan=False), encoding="utf-8"
)
Path("scene-script.json").write_text(scene.script(), encoding="utf-8")
restored = ModelScene.replay(Path("scene-script.json").read_text(encoding="utf-8"))
```

Use scene methods for edits that must be replayable. Direct mutation of the
public model objects is not journalled. The model scene is a working structure
representation, not a replacement for a complete QVF archive: wavefunctions,
volumes and calculation records remain in their original files. Save those files
alongside the scene when they are needed for subsequent analysis.

The browser adapter below provides command controls and rendering. The desktop
[Python console](desktop.md#python-console) exposes the same scene API. These
submodule interfaces are version-specific; pin the viewer version for scripts
that use them, as described in the [SDK contract](python_api.md).

## Command interpreter

`vibeview.scene_commands.SceneCommands` shares one interpreter between SDK and
viewer adapters. `execute(line)` returns a JSON-compatible result; successful
commands enter `history`. Invalid commands do not alter scene state or enter
history. Selection evaluation for measurements does not replace the active
selection. Names and paths containing spaces, and each measurement/alignment
selection expression, must be quoted. The `select` command takes the remainder
of the line as the selection expression, preserving its own quotes.

```text
open reference "reference structure.xyz"
open mobile mobile.xyz
select model reference and not element H
group "reference heavy atoms"
colour mobile orange
style mobile wireframe
hide mobile
show mobile
move mobile 1 2 3
rotate mobile 0 0 1 90
align mobile "model mobile" "model reference"
measure centroid "model reference"
measure axes "model reference"
measure plane "group 'reference heavy atoms'"
measure rmsd "model reference" "model mobile"
measure rmsd-fit "model reference" "model mobile"
measure plane-angle "index 1-3" "index 5-7"
measure point-plane "index 4" "index 1-3"
save scene.json
save-script scene-script.json
capture figure.png
remove mobile
```

`move` supplies an angstrom translation. `rotate` supplies a nonzero axis and an
angle in degrees, about the scene origin. Use translations before/after a rotation
to rotate about another point. A model colour paints atoms, the bonds of the
bond-only `sticks_only` and `wireframe` styles, and cartoons; ball-and-stick bonds
keep their usual colour. `colour MODEL none` restores default element
colours. Supported styles are `ball_and_stick`, `space_filling`, `sticks_only`,
`wireframe` and `cartoon` (the renderer's usual non-biomolecular fallback applies).

Alignment and paired measurements order each selected set by model identifier
then local atom index. Verify that this order expresses the intended chemical
correspondence; use the lower-level `align_model` API for another explicit order.
The mobile alignment selection must belong entirely to the named mobile model.
The first point-plane selection must contain exactly one atom. Measurement
results include units; principal-axis variances have squared length units.

An adapter supplies `load_structure(path)`, `write_text(path, text)` and
`capture(path)` callbacks. Without the relevant callback, file and capture
commands refuse explicitly. The interpreter itself executes no Python and opens
no files. `save` writes the scene snapshot, and `save-script` writes the embedded
data event log described above; replay reconstructs state without repeating file
writes or captures. `history` retains the successful command text for display.
In the browser, the command box clears after a successful command (pick it again
from the history list) and keeps a failed one for correction.

## Browser working scene

The **Working model scene** card can copy the current open structure into a named
model, preserving connectivity. Open another file normally, then add it under a
different name. The **Show working scene** switch temporarily hides the original
structure actors; switching it off restores their prior visibility. Volumes and
experimental maps remain separate. Model edits operate on the copied working
scene and do not overwrite the original QVF structure.

Enter commands in **Command / history** and press Enter or **Run command**. Pick
a previous command from the same field to edit and repeat it. `open` accepts a
server-side path, like the viewer's standard open-path control. Alternatively,
use the normal browser file picker and **Add current structure**. Selected atoms
are highlighted in gold in atom representations. Cartoon colour is per model;
atom selections remain available for measurements regardless of representation.

`save` and `save-script` download JSON. They also include the current server
camera and successful command history. Paste either JSON file into the restore
field and choose **Restore scene / replay script** to rebuild the working models
and camera. `capture` downloads a PNG from the same server actors, using the
camera synchronized by the viewer. Filenames designate browser downloads;
these commands do not write arbitrary server paths. A moved periodic model keeps
its unit-cell drawing at the transformed cell origin as well as rotating its
lattice vectors.

The model scene is independent of the standard single-structure editor and its
undo stack. Use scene commands for these models; disable the working scene to
return to the single-file editor. Browser event/render acceptance is tracked
separately from server-side controller and pixel replay tests.
