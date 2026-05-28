import warnings

import pytest

from cmd_audit import PhraseMatchShortcutWarning


def pytest_configure(config):
    warnings.filterwarnings("ignore", category=PhraseMatchShortcutWarning)


def pytest_collection_modifyitems(config, items):
    marker = pytest.mark.filterwarnings(
        "ignore::cmd_audit.core.PhraseMatchShortcutWarning"
    )
    for item in items:
        item.add_marker(marker)


@pytest.fixture
def legacy_phrase_match_path():
    with warnings.catch_warnings():
        warnings.simplefilter("always", PhraseMatchShortcutWarning)
        yield
