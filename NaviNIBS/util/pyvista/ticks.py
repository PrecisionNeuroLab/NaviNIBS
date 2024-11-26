import numpy as np
import math


def getNiceTicksForRange(minVal: float, maxVal: float, approxNumTicks: int = 5) -> np.ndarray:
    return _getNiceTicksForRangeV2(minVal, maxVal, approxNumTicks)


def _getNiceTicksForRangeV1(minVal: float, maxVal: float, approxNumTicks: int = 5) -> np.ndarray:
    """
    Get nice ticks for a range of values. 
    """
    boundsWidth = maxVal - minVal
    numTicks = approxNumTicks
    tickOrderMagnitude = np.floor(np.log10(boundsWidth / numTicks))
    tickMultiple = 10 ** tickOrderMagnitude
    ticksMin = np.floor(minVal / tickMultiple) * tickMultiple
    ticksMax = np.ceil(maxVal / tickMultiple) * tickMultiple

    numTickMultiples = round((ticksMin - ticksMax) / tickMultiple)
    if numTickMultiples % numTicks != 0:
        # if ticks are not on nice intervals, adjust bounds to make them so

        numToAdd = numTicks - (numTickMultiples % numTicks)

        penalty_iFAdd = 0
        ticksMin_ifAdd = ticksMin
        ticksMax_ifAdd = ticksMax
        for iA in range(numToAdd):
            penalty_low = abs(ticksMin_ifAdd - tickMultiple - minVal)
            penalty_high = abs(maxVal - ticksMax_ifAdd - tickMultiple)
            if penalty_low < penalty_high:
                ticksMin_ifAdd -= tickMultiple
                penalty_iFAdd += penalty_low
            else:
                ticksMax_ifAdd += tickMultiple
                penalty_iFAdd += penalty_high

        numToRemove = numTickMultiples % numTicks

        penalty_iFRemove = 0
        ticksMin_ifRemove = ticksMin
        ticksMax_ifRemove = ticksMax
        for iR in range(numToRemove):
            penalty_low = abs(minVal - ticksMin_ifRemove + tickMultiple)
            penalty_high = abs(ticksMax_ifRemove + tickMultiple - maxVal)
            if penalty_low < penalty_high:
                ticksMin_ifRemove += tickMultiple
                penalty_iFRemove += penalty_low
            else:
                ticksMax_ifRemove -= tickMultiple
                penalty_iFRemove += penalty_high

        if penalty_iFAdd < 5 * penalty_iFRemove:
            ticksMin = ticksMin_ifAdd
            ticksMax = ticksMax_ifAdd
        else:
            ticksMin = ticksMin_ifRemove
            ticksMax = ticksMax_ifRemove

    return np.linspace(ticksMin, ticksMax, numTicks)


def _getNiceTicksForRangeV2(minVal: float, maxVal: float, approxNumTicks: int | None = None) -> np.ndarray:
    """
    Adapted from https://stackoverflow.com/a/73313693/2388228
    """
    retpoints = []
    data_range = maxVal - minVal
    lower_bound = minVal - data_range / 8
    upper_bound = maxVal + data_range / 8
    view_range = upper_bound - lower_bound
    num = lower_bound
    n = math.floor(math.log10(view_range) - 1)
    interval = 10 ** n
    num_ticks = 1
    while num <= upper_bound:
        num += interval
        num_ticks += 1
        if num_ticks > approxNumTicks * 1.5:
            if interval == 10 ** n:
                interval = 2 * 10 ** n
            elif interval == 2 * 10 ** n:
                interval = 4 * 10 ** n
            elif interval == 4 * 10 ** n:
                interval = 5 * 10 ** n
            else:
                n += 1
                interval = 10 ** n
            num = lower_bound
            num_ticks = 1
    if view_range >= 10:
        copy_interval = interval
    else:
        if interval == 10 ** n:
            copy_interval = 1
        elif interval == 2 * 10 ** n:
            copy_interval = 2
        elif interval == 4 * 10 ** n:
            copy_interval = 4
        else:
            copy_interval = 5
    first_val = 0
    prev_val = 0
    times = 0
    temp_log = math.log10(interval)
    if math.isclose(lower_bound, 0):
        first_val = 0
    elif lower_bound < 0:
        if upper_bound < -2 * interval:
            if n < 0:
                copy_ub = round(upper_bound * 10 ** (abs(temp_log) + 1))
                times = copy_ub // round(interval * 10 ** (abs(temp_log) + 1)) + 2
            else:
                times = upper_bound // round(interval) + 2
        while first_val >= lower_bound:
            prev_val = first_val
            first_val = times * copy_interval
            if n < 0:
                first_val *= (10 ** n)
            times -= 1
        first_val = prev_val
        times += 3
    else:
        if lower_bound > 2 * interval:
            if n < 0:
                copy_ub = round(lower_bound * 10 ** (abs(temp_log) + 1))
                times = copy_ub // round(interval * 10 ** (abs(temp_log) + 1)) - 2
            else:
                times = lower_bound // round(interval) - 2
        while first_val < lower_bound:
            first_val = times * copy_interval
            if n < 0:
                first_val *= (10 ** n)
            times += 1
    if n < 0:
        retpoints.append(first_val)
    else:
        retpoints.append(round(first_val))
    val = first_val
    times = 1
    while val <= upper_bound:
        val = first_val + times * interval
        if n < 0:
            retpoints.append(val)
        else:
            retpoints.append(round(val))
        times += 1
    retpoints.pop()
    return np.asarray(retpoints)