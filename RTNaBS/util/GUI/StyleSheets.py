from qtpy import QtWidgets


def setStyleSheetForInstanceOnly(instance: QtWidgets.QWidget, styleSheet: str):
    """
    Set style sheet for instance only, not for its children.

    Adapted from https://stackoverflow.com/a/70705685/2388228
    """
    print('test')
    objectName = instance.objectName()
    if len(objectName) == 0:
        objectName = str(id(instance))
        instance.setObjectName(objectName)
    print(objectName)
    instance.setStyleSheet(f'#{objectName} {{{styleSheet}}}')
