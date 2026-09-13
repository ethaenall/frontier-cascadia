from dataclasses import replace
import asyncio
import uuid

import httpx
import pytest
from threshold.calls import CallConfig, CallManager


def event(mode="LIVE"):
    return {"event_id": str(uuid.uuid4()), "source_mode": mode, "type": "motion_near_entrance"}


def real_config(**kw):
    return replace(CallConfig(enabled=True, dry_run=False,
        account_sid="AC"+"a"*32, auth_token="fake-secret",
        from_number="+12025550101", to_number="+12025550102",
        confirmed_own_number="+12025550102",
        real_call_confirmation="I_CONFIRM_ONE_CALL_TO_MY_OWN_VERIFIED_NUMBER"), **kw)


def mock_provider(request):
    if request.method == "GET":
        return httpx.Response(200, json={"outgoing_caller_ids": [{"phone_number": "+12025550102"}]})
    return httpx.Response(201, json={"sid": "CA"+"b"*32, "status": "queued"})


@pytest.mark.asyncio
async def test_disabled_no_files_or_network(tmp_path):
    manager = CallManager(CallConfig(), tmp_path/"calls.sqlite")
    assert (await manager.handle_event(event()))["status"] == "disabled"
    assert not (tmp_path/"calls.sqlite").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["REPLAY", "TEST", "bogus"])
async def test_nonlive_blocked_by_default(tmp_path, mode):
    manager = CallManager(real_config(), tmp_path/"calls.sqlite")
    assert (await manager.handle_event(event(mode)))["status"] == "blocked_mode"
    assert not (tmp_path/"calls.sqlite").exists()


@pytest.mark.asyncio
async def test_dry_run_never_network_and_dedup_survives_restart(tmp_path):
    def forbidden(request):
        pytest.fail("Dry run attempted network")
    conf = CallConfig(enabled=True, allow_nonlive=True)
    path = tmp_path/"calls.sqlite"
    manager = CallManager(conf, path, transport=httpx.MockTransport(forbidden))
    alarm = event("TEST")
    assert (await manager.handle_event(alarm))["status"] == "dry_run"
    restarted = CallManager(conf, path, transport=httpx.MockTransport(forbidden))
    assert (await restarted.handle_event(alarm))["status"] == "deduplicated"
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"real_call_confirmation":""}, {"confirmed_own_number":"+12025550999"},
    {"to_number":"911", "confirmed_own_number":"911"}, {"auth_token":""},
])
async def test_explicit_owner_and_credentials_gates(tmp_path, change):
    def forbidden(request):
        pytest.fail("Unconfirmed configuration attempted network")
    manager = CallManager(real_config(**change), tmp_path/"calls.sqlite", transport=httpx.MockTransport(forbidden))
    assert (await manager.handle_event(event()))["status"] == "failed"


@pytest.mark.asyncio
async def test_exactly_one_mock_call_for_concurrent_duplicate(tmp_path):
    requests = []
    def provider(req):
        requests.append(req)
        return mock_provider(req)
    manager = CallManager(real_config(), tmp_path/"calls.sqlite", transport=httpx.MockTransport(provider))
    alarm = event()
    outcomes = await asyncio.gather(manager.handle_event(alarm), manager.handle_event(alarm))
    assert sorted(x["status"] for x in outcomes) == ["deduplicated", "sent"]
    assert [x.method for x in requests] == ["GET", "POST"]
    assert b"Twiml=" in requests[1].content
    assert all("fake-secret" not in str(x) and "+12025550102" not in str(x) for x in outcomes)


@pytest.mark.asyncio
async def test_provider_verification_prevents_unverified_destination(tmp_path):
    observed = []
    def provider(req):
        observed.append(req.method)
        return httpx.Response(200, json={"outgoing_caller_ids": []})
    manager = CallManager(real_config(), tmp_path/"calls.sqlite", transport=httpx.MockTransport(provider))
    assert (await manager.handle_event(event()))["status"] == "failed"
    assert observed == ["GET"]


@pytest.mark.asyncio
async def test_uncertain_post_never_retried(tmp_path):
    observed = []
    def provider(req):
        observed.append(req.method)
        if req.method == "POST":
            raise httpx.ReadTimeout("sensitive provider body fake-secret", request=req)
        return mock_provider(req)
    alarm = event()
    manager = CallManager(real_config(), tmp_path/"calls.sqlite", transport=httpx.MockTransport(provider))
    outcome = await manager.handle_event(alarm)
    assert outcome["status"] == "failed"
    assert "fake-secret" not in str(outcome)
    assert (await manager.handle_event(alarm))["status"] == "deduplicated"
    assert observed == ["GET", "POST"]


@pytest.mark.asyncio
async def test_failed_ledger_blocks_send(tmp_path):
    manager = CallManager(real_config(), tmp_path, transport=httpx.MockTransport(mock_provider))
    assert (await manager.handle_event(event()))["status"] == "failed"
