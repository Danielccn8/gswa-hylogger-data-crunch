from __future__ import annotations

from types import SimpleNamespace

import pytest

from hylogger import processed
from hylogger.errors import RemoteDataError


class FakeSession:
    def __init__(self, responses: dict[str, str]):
        self.responses = responses

    def get(self, url, params, timeout):
        return SimpleNamespace(text=self.responses[params["logid"]], raise_for_status=lambda: None)


def test_logs_are_fetched_one_by_one_and_joined(app_config, monkeypatch):
    responses = {
        "a": "StartDepth,EndDepth,Min1_uTSAS\n1.0,1.0,Muscovite\n2.0,2.0,null\n",
        "b": "StartDepth,EndDepth,Wt1_uTSAS\n1.0,1.0,0.75\n2.0,2.0,null\n",
    }
    monkeypatch.setattr("hylogger.catalog.http_session", lambda _retries: FakeSession(responses))
    text = processed.download_scalars(app_config, ["a", "b"])
    assert text.splitlines() == [
        "StartDepth,EndDepth,Min1_uTSAS,Wt1_uTSAS",
        "1.0,1.0,Muscovite,0.75",
        "2.0,2.0,null,null",
    ]


def test_misaligned_depth_rows_are_rejected(app_config, monkeypatch):
    responses = {
        "a": "StartDepth,EndDepth,Min1_uTSAS\n1.0,1.0,Muscovite\n",
        "b": "StartDepth,EndDepth,Wt1_uTSAS\n1.5,1.5,0.75\n",
    }
    monkeypatch.setattr("hylogger.catalog.http_session", lambda _retries: FakeSession(responses))
    with pytest.raises(RemoteDataError, match="differ"):
        processed.download_scalars(app_config, ["a", "b"])
