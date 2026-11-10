import httpx
import pytest
from helpers import EV1, EV2, ingest, item

pytestmark = pytest.mark.anyio


async def test_reads_batch_with_service_token_for_ingest() -> None:
    evidence, calls = ingest()
    hasil = await evidence.fetch([EV2, EV1, EV1, "EV-hb01-999999"])
    assert {i["evidence_id"] for i in hasil["items"]} == {EV1, EV2}
    assert hasil["not_found"] == ["EV-hb01-999999"]
    assert calls == [
        {"path": "/svc/v1/evidence:batch", "auth": "Bearer palsu-svc:ingest", "ids": [EV1, EV2, "EV-hb01-999999"]}
    ]


async def test_more_than_500_ids_are_split_into_batches() -> None:
    ids = [f"EV-hb01-{n:06d}" for n in range(1, 702)]
    evidence, calls = ingest([item(i, "2026-11-10T02:00:00Z") for i in ids])
    hasil = await evidence.fetch(ids)
    assert [len(c["ids"]) for c in calls] == [500, 201]
    assert len(hasil["items"]) == 701


async def test_ingest_error_is_raised() -> None:
    evidence, _ = ingest(status=503)
    with pytest.raises(httpx.HTTPStatusError):
        await evidence.fetch([EV1])
