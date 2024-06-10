from qtpy import QtWidgets


def setStyleSheetForInstanceOnly(instance: QtWidgets.QWidget, styleSheet: str,
                                 selectorPrefix: str = '',
                                 selectorSuffix: str = ''):
    """
    Set style sheet for instance only, not for its children.

    Adapted from https://stackoverflow.com/a/70705685/2388228
    """
    objectName = instance.objectName()
    if len(objectName) == 0:
        objectName = str(id(instance))
        instance.setObjectName(objectName)
    instance.setStyleSheet(f'{selectorPrefix}#{objectName}{selectorSuffix} {{{styleSheet}}}')
