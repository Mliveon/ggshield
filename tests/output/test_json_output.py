from collections import namedtuple

import pytest

from ggshield.output import JSONHandler
from ggshield.output.json.schemas import ScanCollectionSchema
from ggshield.scan import Commit
from ggshield.scan.scannable import ScanCollection
from ggshield.utils import Filemode
from tests.conftest import (
    _MULTIPLE_SECRETS,
    _NO_SECRET,
    _ONE_LINE_AND_MULTILINE_PATCH,
    _SIMPLE_SECRET,
    my_vcr,
)


ExpectedScan = namedtuple("expectedScan", "exit_code matches first_match want")

_EXPECT_NO_SECRET = {
    "content": "@@ -0,0 +1 @@\n+this is a patch without secret\n",
    "filename": "test.txt",
    "filemode": Filemode.NEW,
}


@pytest.mark.parametrize(
    "name,input_patch,expected",
    [
        ("multiple_secrets", _MULTIPLE_SECRETS, 1),
        ("simple_secret", _SIMPLE_SECRET, 1),
        ("_ONE_LINE_AND_MULTILINE_PATCH", _ONE_LINE_AND_MULTILINE_PATCH, 1),
        ("no_secret", _NO_SECRET, 0),
    ],
    ids=[
        "_MULTIPLE_SECRETS",
        "_SIMPLE_SECRET",
        "_ONE_LINE_AND_MULTILINE_PATCH",
        "_NO_SECRET",
    ],
)
def test_json_output(client, name, input_patch, expected, snapshot):
    c = Commit()
    c._patch = input_patch
    handler = JSONHandler(verbose=True, show_secrets=False)

    with my_vcr.use_cassette(name):
        results = c.scan(
            client=client, matches_ignore={}, all_policies=True, verbose=False
        )

        flat_results, exit_code = handler.process_scan(
            scan=ScanCollection("path", results), top=True
        )

        assert exit_code == expected
        json_flat_results = ScanCollectionSchema().dumps(flat_results)
        snapshot.assert_match(ScanCollectionSchema().loads(json_flat_results))
