from __future__ import absolute_import

import sys

# argparse is missing on Python 2.6 and 3.1
major, minor = sys.version_info[:2]
if ((major == 2 and minor <= 6) or
    (major == 3 and minor <= 1)):
    from ._argparse import *
else:
    from argparse import *
