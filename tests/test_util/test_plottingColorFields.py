import logging
import numpy as np
import pyvista as pv
import pytest

from NaviNIBS.Navigator.GUI import headMeshDefaultKwargs
from NaviNIBS.util.pyvista.plotting import findColorArrayName

logger = logging.getLogger(__name__)


@pytest.fixture
def mesh() -> pv.PolyData:
    surf = pv.Sphere(theta_resolution=20, phi_resolution=20)
    surf.point_data.clear()
    surf.cell_data.clear()
    return surf


def test_noColorArrayFoundOnPlainMesh(mesh):
    assert findColorArrayName(mesh) is None


def test_normalsAddedByRenderingAreNotTreatedAsColors(mesh):
    """
    Rendering with headMeshDefaultKwargs (smooth_shading without split_sharp_edges) makes pyvista
    add a Normals array to the mesh *in place*. Rendering that array as direct RGB paints the mesh
    in rainbow colors, so it must not be mistaken for a color array on any later render.
    """
    plotter = pv.Plotter(off_screen=True)
    plotter.add_mesh(mesh, color='#d9a5b2', **headMeshDefaultKwargs)

    assert 'Normals' in mesh.array_names, 'expected pyvista to have added Normals in place'
    assert findColorArrayName(mesh) is None


def test_vectorFieldIsNotTreatedAsColors(mesh):
    """
    A head mesh loaded from a SimNIBS simulation .msh can carry 3-component E/J fields.
    """
    mesh['E'] = np.random.rand(mesh.n_points, 3).astype(np.float32)
    assert findColorArrayName(mesh) is None


@pytest.mark.parametrize('arrayName', ['RGB', 'rgba', 'colors', 'ROIs_combined_rgba'])
def test_namedColorArraysAreFound(mesh, arrayName):
    mesh[arrayName] = np.random.rand(mesh.n_points, 4).astype(np.float32)
    assert findColorArrayName(mesh) == arrayName


def test_uint8ArrayIsFound(mesh):
    """
    Meshes imported with embedded vertex colors (e.g. tool/coil .ply files) should still render
    in their own colors.
    """
    mesh['SomeImportedColors'] = np.random.randint(0, 255, (mesh.n_points, 3), dtype=np.uint8)
    assert findColorArrayName(mesh) == 'SomeImportedColors'


def test_colorArrayIsFoundAlongsideNormals(mesh):
    mesh['Normals'] = np.random.rand(mesh.n_points, 3).astype(np.float32)
    mesh['RGBA'] = np.random.randint(0, 255, (mesh.n_points, 4), dtype=np.uint8)
    assert findColorArrayName(mesh) == 'RGBA'
