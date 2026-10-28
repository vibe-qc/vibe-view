"""Send instanced atom and bond glyphs to vtk.js as instances, not triangles.

A large structure draws its atoms and bonds with ``vtkGlyph3DMapper``
(:func:`vibeview.renderers.structure.instanced_glyph_actor`): one prototype
sphere or cylinder, plus one point per atom or half-bond carrying its colour,
scale and direction. VTK draws that with instanced rendering, and so does
vtk.js, whose ``vtkGlyph3DMapper`` the browser bundle already registers.
trame-vtk ships no serializer for the mapper, so without one the actors
would be dropped from the browser scene. :func:`install_glyph_mapper_serializer`
registers one that sends the two inputs (the instance points with every
array they hold, and the prototype) and the mapper's glyph settings. The
wire payload is then the instance arrays, about 30 bytes an atom, instead of
a merged mesh of every sphere's triangles, and the GPU instances the
prototype on the client (roadmap: Carbon nanotubes, large-system rendering).
A large actor also carries a coarse prototype for level of detail while the
camera moves (:mod:`vibeview.app.glyph_lod`).
"""

from __future__ import annotations

from typing import Any

from vibeview.app.glyph_lod import LOD_INSTANCES, coarse_prototype

_MARK = "_vibeview_glyph_mapper"

#: The VTK class names a glyph mapper is serialised under.
GLYPH_MAPPER_CLASSES = ("vtkGlyph3DMapper", "vtkOpenGLGlyph3DMapper")


def glyph_mapper_serializer(parent: Any, mapper: Any, mapper_id: str, context: Any,
                            depth: int) -> dict[str, Any] | None:
    """trame-vtk serializer for a ``vtkGlyph3DMapper`` (points + prototype)."""
    from trame_vtk.modules.vtk.serializers import registry
    from trame_vtk.modules.vtk.serializers.cache import cache_properties
    from trame_vtk.modules.vtk.serializers.utils import reference_id, wrap_id

    from vibeview.renderers.structure import (
        GLYPH_DIRECTION_ARRAY,
        GLYPH_RGB_ARRAY,
        GLYPH_SCALE_ARRAY,
    )

    polydata = registry.SERIALIZERS.get("vtkPolyData")
    if polydata is None:
        return None
    mapper.Update()
    points = mapper.GetInputDataObject(0, 0)
    source = mapper.GetInputDataObject(1, 0)
    if points is None or source is None:
        return None
    dependencies = []
    calls = []
    inputs: list[tuple[Any, Any]] = [(points, "*"), (source, ["Normals"])]
    if points.GetNumberOfPoints() >= LOD_INSTANCES:
        # Port 2 is vibe-view's: the coarse prototype the page script draws
        # while the camera moves (vibeview.app.glyph_lod).
        inputs.append((coarse_prototype(bool(mapper.GetOrient())), ["Normals"]))
    for port, (data, fields) in enumerate(inputs):
        data_id = f"{mapper_id}-glyph-{port}"
        instance = polydata(mapper, data, data_id, context, depth + 1, requested_fields=fields)
        if not instance:
            return None
        dependencies.append(instance)
        # vtk.js refuses setInputData past the mapper's two declared ports;
        # the coarse prototype is appended as a third.
        if port < 2:
            calls.append(["setInputData", [wrap_id(data_id), port]])
        else:
            calls.append(["addInputData", [wrap_id(data_id)]])
    return {
        "parent": reference_id(parent),
        "id": mapper_id,
        # The browser bundle registers the mapper under its generic name.
        "type": "vtkGlyph3DMapper",
        "properties": cache_properties(
            mapper_id,
            context,
            {
                "orient": 1 if mapper.GetOrient() else 0,
                "orientationMode": mapper.GetOrientationMode(),
                "orientationArray": GLYPH_DIRECTION_ARRAY if mapper.GetOrient() else None,
                "scaling": 1 if mapper.GetScaling() else 0,
                "scaleMode": mapper.GetScaleMode(),
                "scaleFactor": mapper.GetScaleFactor(),
                "scaleArray": GLYPH_SCALE_ARRAY,
                "scalarVisibility": mapper.GetScalarVisibility(),
                "scalarMode": mapper.GetScalarMode(),
                "colorMode": mapper.GetColorMode(),
                "arrayAccessMode": 1,
                "colorByArrayName": GLYPH_RGB_ARRAY,
                "useLookupTableScalarRange": 0,
                "scalarRange": mapper.GetScalarRange(),
                "interpolateScalarsBeforeMapping": 0,
                "resolveCoincidentTopology": mapper.GetResolveCoincidentTopology(),
            },
        ),
        "calls": calls,
        "dependencies": dependencies,
    }


setattr(glyph_mapper_serializer, _MARK, True)


def install_glyph_mapper_serializer(**_kwargs: Any) -> bool:
    """Register :func:`glyph_mapper_serializer`; idempotent.

    A no-op returning False until trame-vtk has filled its registry, which
    happens when the local-rendering protocol starts, so it is called before
    every push like the label-array serializer.
    """
    try:
        from trame_vtk.modules.vtk.serializers import registry
    except Exception:  # noqa: BLE001 — no viewer extra, nothing to register
        return False
    if "vtkPolyData" not in registry.SERIALIZERS:
        return False
    for name in GLYPH_MAPPER_CLASSES:
        if not getattr(registry.SERIALIZERS.get(name), _MARK, False):
            registry.register_instance_serializer(name, glyph_mapper_serializer)
    return True
