from hylogger.manifest import Manifest


def test_manifest_upserts_without_losing_existing_hash(app_config) -> None:
    manifest = Manifest(app_config.manifest_path)
    manifest.record(
        artifact_key="raw:TEST",
        hole_id="TEST",
        kind="raw",
        status="complete",
        size_bytes=12,
        sha256="abc",
    )
    manifest.record(
        artifact_key="raw:TEST",
        hole_id="TEST",
        kind="raw",
        status="complete",
        metadata={"verified": True},
    )

    record = manifest.get("raw:TEST")
    assert record["size_bytes"] == 12
    assert record["sha256"] == "abc"
    assert record["metadata"] == {"verified": True}
