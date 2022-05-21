import typing as tp
import sys
import traceback
import logging


def exceptionToStr(e: Exception) -> str:

    # from https://stackoverflow.com/a/49613561
    ex_type, ex_value, ex_traceback = sys.exc_info()

    # Extract unformatter stack traces as tuples
    trace_back = traceback.extract_tb(ex_traceback)

    # Format stacktrace
    stack_trace = ''

    eStr = ''

    for trace in trace_back:
        stack_trace += "File : %s , Line : %d, Func.Name : %s, Message : %s\n" % (trace[0], trace[1], trace[2], trace[3])

    eStr += "Exception type : %s\n" % ex_type.__name__
    eStr += "Exception message : %s\n" % ex_value
    eStr += "Stack trace : %s\n" % stack_trace

    return eStr


def makeStrUnique(baseStr: str, existingStrs: tp.List[str], delimiter: str = '_') -> str:
    count = 1
    uniqueStr = baseStr

    if delimiter in baseStr:
        try:
            prevNum = int(baseStr[baseStr.rindex(delimiter)+1:])
        except ValueError as e:
            pass
        else:
            baseStr = baseStr[:baseStr.rindex(delimiter)]
            count = prevNum

    while uniqueStr in existingStrs:
        count += 1
        uniqueStr = '{}_{}'.format(baseStr, count)

    return uniqueStr




