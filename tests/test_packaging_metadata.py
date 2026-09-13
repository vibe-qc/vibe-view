"""Guard the viewer's package contract in the repository that owns it."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


@pytest.fixture(scope="module")
def project_metadata():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as stream:
        return tomllib.load(stream)["project"]


def _dependency_names(requirements):
    return {canonicalize_name(Requirement(item).name) for item in requirements}


@pytest.mark.parametrize(
    ("field", "expected"),
    [("name", "vibeview"), ("requires-python", ">=3.11")],
)
def test_distribution_metadata(project_metadata, field, expected):
    assert project_metadata[field] == expected


def test_distribution_license_is_mpl_2(project_metadata):
    license_metadata = project_metadata["license"]
    if isinstance(license_metadata, dict):
        license_metadata = license_metadata["text"]
    assert license_metadata == "MPL-2.0"


def test_cli_entry_point_targets_viewer_main(project_metadata):
    assert project_metadata["scripts"]["vibe-view"] == "vibeview.cli:main"


def test_pyvista_is_a_core_dependency(project_metadata):
    assert "pyvista" in _dependency_names(project_metadata["dependencies"])


@pytest.mark.parametrize(
    "package", ["trame", "trame-vtk", "trame-vuetify", "uvicorn"]
)
def test_browser_stack_stays_out_of_core(project_metadata, package):
    assert package not in _dependency_names(project_metadata["dependencies"])


@pytest.mark.parametrize(
    "package", ["trame", "trame-vtk", "trame-vuetify", "uvicorn"]
)
def test_viewer_extra_declares_browser_stack(project_metadata, package):
    viewer = project_metadata["optional-dependencies"]["viewer"]
    assert package in _dependency_names(viewer)
