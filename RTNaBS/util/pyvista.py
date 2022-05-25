import numpy as np
import pyvista as pv


Actor = pv._vtk.vtkActor


def setActorUserTransform(actor: Actor, transf: np.ndarray):
    t = pv._vtk.vtkTransform()
    t.SetMatrix(pv.vtkmatrix_from_array(transf))
    actor.SetUserTransform(t)