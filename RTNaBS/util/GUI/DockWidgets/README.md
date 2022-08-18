
## Building PyKDDockWidgetsQt6
- Clone KDDockWidgets repo from [custom fork](https://github.com/chriscline/kddockwidgets)
- Edit default Config parameters (since current version doesn't allow dynamic Config changes via Python binding):
  - Edit Config.h->Flags->Flag_Default to:

           Flag_Default = Flag_AeroSnapWithClientDecos
              | Flag_AllowReorderTabs
              | Flag_DontUseUtilityFloatingWindows
              | Flag_NativeTitleBar
              | Flag_TitleBarHasMaximizeButton
              | Flag_TitleBarHasMinimizeButton
              | Flag_AllowSwitchingTabsViaMenu ///< The defaults
- Install dependencies 

        pip3 install --index-url=https://download.qt.io/official_releases/QtForPython/ --trusted-host download.qt.io shiboken6 pyside6 shiboken6_generator

- Set up cmake
  - Install cmake-GUI 
  - Install ninja and add to path
  - Install visual studio
  - Install llvm (maybe necessary?)
  - Open VS Native Tools Command Prompt; run commands below from within this.
  - Launch cmake-gui from VS Native Tools prompt
  - Install Qt
    - Make sure pyside6 version matches Qt version (e.g. 6.2.4)
  - Configure
  - Set environment variable `CMAKE_PREFIX_PATH=C:\Qt\6.2.4\msvc2019_64`
  - Enable KDDDockWidgets_PYTHON_BINDINGS
  - Enable KDDockWidgetsQt6
  - Generate
- (From within VS Native Tools Command Prompt):
  - cd to build dir
  - run `ninja`
  - run `ninja install`

