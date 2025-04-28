import logging
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s.%(msecs)03d  %(process)6d %(filename)20s %(lineno)4d %(levelname)5s: %(message)s',
                    datefmt='%H:%M:%S')

__version__ = '0.2.1'
