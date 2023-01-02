import pyvista as pv
import os

center = (0, 0, 0)
diameter = 5

sphere = pv.Sphere(radius=diameter/2, center=center)

sphere.save(os.path.join('..', 'data', 'tools', f'Sphere_{diameter}mm.stl'))
