setlocal
set PYTHONPATH=.\
viztracer^
 --min_duration 0.2ms^
 --ignore_c_function^
 --tracer_entries 10000000^
 --log_async^
 --output_file C:\Data\viztracer_result.json^
 .\NaviNIBS\Navigator\GUI\NavigatorGUI.py
endlocal