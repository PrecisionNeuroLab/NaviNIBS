import attrs
import numpy as np
import typing as tp

from RTNaBS.Navigator.GUI.CollectionModels import CollectionTableModel, K, logger
from RTNaBS.Navigator.Model.SubjectRegistration import HeadPoint, HeadPoints
from RTNaBS.util.Transforms import applyTransform


@attrs.define(slots=False)
class HeadPointsTableModel(CollectionTableModel[int, HeadPoints, HeadPoint]):
    _collection: HeadPoints = attrs.field(init=False)
    # TODO: re-implement with HeadPoints as a custom class instead of plain ndarray to allow indexing in expected form, etc.

    def __attrs_post_init__(self):
        self._collection = self._session.subjectRegistration.sampledHeadPoints

        self._columns = [
            'distFromSkin',
            'XYZ'
        ]
        self._derivedColumns = dict(
            distFromSkin=self._getDistFromSkinForIndex,
            XYZ=self._getXYZForIndex
        )
        self._columnLabels = dict(
            distFromSkin='Dist from skin (mm)',
            XYZ='XYZ (tracker rel.)'
        )

        self._collection.sigHeadpointsAboutToChange.connect(self._onCollectionAboutToChange)
        self._collection.sigHeadpointsChanged.connect(self._onCollectionChanged)

        super().__attrs_post_init__()

    def _getDistFromSkinForIndex(self, index: int) -> str:
        pt = self._collection[index]
        if self._session.subjectRegistration.trackerToMRITransf is None:
            return ''
        pt_MRISpace = applyTransform(self._session.subjectRegistration.trackerToMRITransf, pt)
        closestPtIndex = self._session.headModel.skinSurf.find_closest_point(pt_MRISpace)
        closestPt = self._session.headModel.skinSurf.points[closestPtIndex, :]
        dist = np.linalg.norm(closestPt - pt_MRISpace)
        # TODO: improvie dist estimation by allowing for nearest point to be between mesh vertices
        return '%.2f' % dist

    def _getXYZForIndex(self, index: int) -> str:
        pt = self._collection[index]
        ptStr = ','.join(['%.1f' % val for val in pt])
        return ptStr