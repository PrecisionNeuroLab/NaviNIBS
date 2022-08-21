import json
import jsbeautifier


def beautifyJSON(o: str) -> str:
    """
    Python JSON module doesn't make it simple to do prettier formatting with most entries being indented but keeping
     things like simple, short arrays on single lines. So use jsbeautifier to process after dumping.
    See https://stackoverflow.com/questions/21866774/pretty-print-json-dumps
    Note that this is not particularly efficient or lightweight of a dependency, but perhaps better than hacky
     subclassing of JSONEncoder as in http://stackoverflow.com/a/17684652
    """
    opts = jsbeautifier.default_options()
    opts.indent_size = 2
    return jsbeautifier.beautify(o, opts)


def jsonPrettyDumps(o) -> str:
    return beautifyJSON(json.dumps(o))
