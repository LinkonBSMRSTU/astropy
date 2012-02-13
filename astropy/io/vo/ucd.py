"""
This file contains routines to verify the correctness of UCD strings.
"""

from __future__ import with_statement, absolute_import

#STDLIB
import re
import sys

#LOCAL
from ... import config

__all__ = ['parse_ucd', 'check_ucd']


class UCDWords:
    """
    A class to manage the list of acceptable UCD words.  Works by
    reading in a data file exactly as provided by IVOA.  This file
    resides in data/ucd1p-words.txt.
    """
    def __init__(self):
        self._primary = set()
        self._secondary = set()
        self._descriptions = {}
        self._capitalization = {}

        with config.get_data_fileobj("data/ucd1p-words.txt") as fd:
            for line in fd.readlines():
                type, name, descr = [
                    x.strip().decode('ascii') for x in line.split(b'|')]
                name_lower = name.lower()
                if type in u'QPEV':
                    self._primary.add(name_lower)
                if type in u'QSEV':
                    self._secondary.add(name_lower)
                self._descriptions[name_lower] = descr
                self._capitalization[name_lower] = name

    _singleton = None
    @classmethod
    def get(cls):
        if cls._singleton is None:
            cls._singleton = cls()
        return cls._singleton

    def is_primary(self, name):
        """
        Returns True if *name* is a valid primary name.
        """
        return name.lower() in self._primary

    def is_secondary(self, name):
        """
        Returns True if *name* is a valid secondary name.
        """
        return name.lower() in self._secondary

    def get_description(self, name):
        """
        Returns the official English description of the given UCD
        *name*.
        """
        return self._descriptions[name.lower()]

    def normalize_capitalization(self, name):
        """
        Returns the standard capitalization form of the given name.
        """
        return self._capitalization[name.lower()]

_ucd_singleton = None


def parse_ucd(ucd, check_controlled_vocabulary=False, has_colon=False):
    """
    Parse the UCD into its component parts.

    Parameters
    ----------
    ucd : str
        The UCD string

    check_controlled_vocabulary : bool, optional
        If `True`, then each word in the UCD will be verified against
        the UCD1+ controlled vocabulary, (as required by the VOTable
        specification version 1.2), otherwise not.

    has_colon : bool, optional
        If `True`, the UCD may contain a colon (as defined in earlier
        versions of the standard).

    Returns
    -------
    parts : list
        The result is a list of tuples of the form:

            (*namespace*, *word*)

        If no namespace was explicitly specified, *namespace* will be
        returned as ``'ivoa'`` (i.e., the default namespace).

    Raises
    ------
    ValueError : *ucd* is invalid
    """
    ucd_words = UCDWords.get()

    if has_colon:
        m = re.search(u'[^A-Za-z0-9_.:;\-]', ucd)
    else:
        m = re.search(u'[^A-Za-z0-9_.;\-]', ucd)
    if m is not None:
        raise ValueError(
            "UCD has invalid character {0!r} in {1!r}".format(
                m.group(0), ucd))

    word_component_re = u'[A-Za-z0-9][A-Za-z0-9\-_]*'
    word_re = u'{0}(\.{0})*'.format(word_component_re)

    parts = ucd.split(u';')
    words = []
    for i, word in enumerate(parts):
        colon_count = word.count(u':')
        if colon_count == 1:
            ns, word = word.split(u':', 1)
            if not re.match(word_component_re, ns):
                raise ValueError("Invalid namespace {0!r}".format(ns))
            ns = ns.lower()
        elif colon_count > 1:
            raise ValueError("Too many colons in {0!r}".format(word))
        else:
            ns = u'ivoa'

        if not re.match(word_re, word):
            raise ValueError("Invalid word {0!r}".format(word))

        if ns == u'ivoa' and check_controlled_vocabulary:
            if i == 0:
                if not ucd_words.is_primary(word):
                    if ucd_words.is_secondary(word):
                        raise ValueError(
                            "Secondary word {0!r} is not valid as a primary "
                            "word" % word)
                    else:
                        raise ValueError("Unknown word '%s'" % word)
            else:
                if not ucd_words.is_secondary(word):
                    if ucd_words.is_primary(word):
                        raise ValueError(
                            "Primary word '%s' is not valid as a secondary "
                            "word" % word)
                    else:
                        raise ValueError("Unknown word '%s'" % word)

        try:
            normalized_word = ucd_words.normalize_capitalization(word)
        except KeyError:
            normalized_word = word
        words.append((ns, normalized_word))

    return words


def check_ucd(ucd, check_controlled_vocabulary=False, has_colon=False):
    """
    Returns False if *ucd* is not a valid `unified content
    descriptor`_.

    Parameters
    ----------
    ucd : str
        The UCD string

    check_controlled_vocabulary : bool, optional
        If `True`, then each word in the UCD will be verified against
        the UCD1+ controlled vocabulary, (as required by the VOTable
        specification version 1.2), otherwise not.

    Returns
    -------
    valid : bool
    """
    if ucd is None:
        return True

    try:
        parse_ucd(ucd,
                  check_controlled_vocabulary=check_controlled_vocabulary,
                  has_colon=has_colon)
    except ValueError as e:
        return False
    return True
