import attrs
import typing as tp


def attrsAsDict(obj,
                eqs: tp.Optional[tp.Dict[str, tp.Callable[[tp.Any, tp.Any], bool]]] = None,
                exclude: tp.Optional[tp.Iterable[str]] = None) -> tp.Dict[str, tp.Any]:
    """
    Similar to attrs.asdict(), but strips underscore from private attributes, doesn't include items with unchanged defaults, etc.

    Specify eqs to provide custom equality test callables, e.g.
     dict(field1=array_equalish, field2=nested_array_equalish) for handling np.ndarrays and dicts with ndarray values, respectively
    """

    def filterAttrs(attrib: attrs.Attribute, val: tp.Any,
                    eqs: tp.Optional[tp.Dict[str, tp.Callable[[tp.Any, tp.Any], bool]]] = None,
                    exclude: tp.Optional[tp.Iterable[str]] = None):
        if not attrib.init:
            # don't include non-init attribs
            return False

        if exclude is not None and (attrib.name in exclude or ('_' + attrib.name) in exclude):
            # don't include excluded attribs
            return False

        if True:
            # don't include values that still equal default value

            # noinspection PyTypeChecker
            if isinstance(attrib.default, attrs.Factory):
                if attrib.default.takes_self:
                    raise NotImplementedError()  # TODO:
                else:
                    default = attrib.default.factory()
            else:
                default = attrib.default

            if attrib.eq:
                if eqs is not None:
                    if attrib.name not in eqs and attrib.name.startswith('_'):
                        attrib_name_eqs = attrib.name[1:]
                    else:
                        attrib_name_eqs = attrib.name
                else:
                    attrib_name_eqs = None
                if attrib_name_eqs is not None and attrib_name_eqs in eqs:
                    eq = eqs[attrib_name_eqs](val, default)
                elif attrib.eq:
                    eq = default == val
                else:
                    eq = default is val
                if eq:
                    return False

        return True

    d = attrs.asdict(obj, filter=lambda attrib, val, eqs=eqs, exclude=exclude: filterAttrs(
        attrib, val, eqs=eqs, exclude=exclude))

    # remove underscores before private attributes
    d = {(key[1:] if key.startswith('_') else key): val for key, val in d.items()}

    return d





