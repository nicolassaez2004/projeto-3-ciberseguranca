from datetime import timedelta

import base64

import pytest
from bson import ObjectId

from app.extensions import mongo
from app.services.coding import deadline_passed, validated_config
from app.utils import now
from conftest import unlock


def start(client, identities, challenge, mode="submit", **overrides):
    payload = {"mode": mode, "language": "python", "source_code": "print(0)", **overrides}
    return client.post(f"/api/challenges/{challenge}/code-jobs", headers=identities["talent"]["headers"], json=payload)


def finish(client, identities, job):
    unlock()
    return client.get(f"/api/code-jobs/{job['_id']}", headers=identities["talent"]["headers"])


def legacy_run(app, identities, assessment, stdin="example"):
    config = mongo.db.coding_configs.find_one({"challenge_id": ObjectId(assessment)})
    tokens = app.extensions["judge0_client"].create_batch("python", "print(0)", [{"stdin": stdin, "expected_output": None}], config)
    job = {
        "challenge_id": ObjectId(assessment), "talent_id": identities["talent"]["id"],
        "mode": "run", "language": "python", "source_code": "print(0)", "max_points": 100,
        "total": 1, "status": "processing", "active": True, "created_at": now(), "provider_tokens": tokens,
    }
    job["_id"] = mongo.db.coding_jobs.insert_one(job).inserted_id
    return job


def test_private_tests_never_appear_in_candidate_responses(client, identities, assessment):
    detail = client.get(f"/api/challenges/{assessment}/coding", headers=identities["talent"]["headers"])
    assert detail.status_code == 200
    assert "tests" not in detail.json["config"]
    assert client.get(f"/api/challenges/{assessment}/coding/config", headers=identities["talent"]["headers"]).status_code == 403
    job = start(client, identities, assessment).json["job"]
    result = finish(client, identities, job)
    text = result.get_data(as_text=True)
    assert result.json["job"]["score"] == 100
    for secret in ["PRIVATE-OUTPUT", "PRIVATE-STDERR", "provider_tokens", "source_code", "expected_output"]:
        assert secret not in text
    dashboard = client.get("/api/dashboard", headers=identities["talent"]["headers"])
    assert "expected_output" not in dashboard.get_data(as_text=True)


def test_best_score_is_idempotent_and_never_added_twice(app, client, identities, assessment):
    first = finish(client, identities, start(client, identities, assessment).json["job"])
    assert first.json["best_score"] == 100
    for _ in range(3):
        assert finish(client, identities, first.json["job"]).json["best_score"] == 100
    unlock()
    app.extensions["judge0_client"].statuses = [4, 3, 3, 3, 3]
    second = finish(client, identities, start(client, identities, assessment).json["job"])
    assert second.json["job"]["score"] == 80
    assert second.json["best_score"] == 100
    assert mongo.db.coding_scores.count_documents({}) == 1
    assert mongo.db.participations.find_one({"talent_id": identities["talent"]["id"]})["status"] == "joined"


def test_existing_run_can_finish_without_awarding_points(app, client, identities, assessment):
    job = legacy_run(app, identities, assessment)
    assert app.extensions["judge0_client"].created[0]["tests"] == [{"stdin": "example", "expected_output": None}]
    result = finish(client, identities, job)
    assert "PRIVATE-OUTPUT" in result.json["job"]["output"]["stdout"]
    assert "score" not in result.json["job"]
    assert mongo.db.coding_scores.count_documents({}) == 0


@pytest.mark.parametrize("field,status", [("stdout", 3), ("stderr", 11), ("compile_output", 6)])
def test_run_returns_wrapped_judge0_output_in_each_channel(app, client, identities, assessment, field, status):
    app.extensions["judge0_client"].statuses = [status]
    created = legacy_run(app, identities, assessment, stdin="FAIL 192.0.2.10\n")
    output = "Saída de diagnóstico\n192.0.2.10\n" * 4
    row = next(iter(app.extensions["judge0_client"].rows.values()))
    row.update(stdout=None, stderr=None, compile_output=None)
    row[field] = base64.encodebytes(output.encode("utf-8")).decode("ascii")
    result = finish(client, identities, created)
    assert result.status_code == 200
    job = result.json["job"]
    assert job["status"] == "completed"
    assert job["output"] == {key: output if key == field else "" for key in ("stdout", "stderr", "compile_output")}
    assert "score" not in job
    assert mongo.db.coding_scores.count_documents({}) == 0


@pytest.mark.parametrize("statuses,expected", [([4, 3, 3, 3, 3], 80), ([4] * 5, 0), ([5] * 5, 0), ([6] * 5, 0), ([11] * 5, 0)])
def test_program_results_receive_proportional_points(app, client, identities, assessment, statuses, expected):
    app.extensions["judge0_client"].statuses = statuses
    job = start(client, identities, assessment).json["job"]
    result = finish(client, identities, job)
    assert result.json["job"]["status"] == "completed"
    assert result.json["job"]["score"] == expected


@pytest.mark.parametrize("statuses,max_points,expected", [
    ([3, 3, 3, 4, 4, 4], 100, 50),
    ([3, 3, 3, 3, 4, 4], 100, 66.67),
    ([3, 4, 4], 100, 33.33),
    ([3, 3, 4], 1, 0.67),
    ([3] + [4] * 7, 1, 0.13),
    ([3] * 6, 250, 250),
])
def test_proportional_scores_use_all_cases_and_round_to_two_decimals(app, client, identities, assessment, statuses, max_points, expected):
    mongo.db.coding_configs.update_one({"challenge_id": ObjectId(assessment)}, {"$set": {
        "tests": [{"stdin": str(index), "expected_output": "OK"} for index in range(len(statuses))], "max_points": max_points,
    }})
    app.extensions["judge0_client"].statuses = statuses
    result = finish(client, identities, start(client, identities, assessment).json["job"])
    assert result.json["job"]["score"] == expected
    assert result.json["best_score"] == expected
    assert result.json["job"]["passed"] == statuses.count(3)
    assert result.json["job"]["total"] == len(statuses)
    score = mongo.db.coding_scores.find_one({})
    assert ("solved_at" in score) == (expected == max_points)
    board = client.get(f"/api/challenges/{assessment}/coding/results", headers=identities["owner"]["headers"])
    assert board.json["results"][0]["score"] == expected


def test_best_partial_score_survives_repeats_worse_answers_and_service_failures(app, client, identities, assessment):
    for statuses, expected, best in [([3, 3, 4, 4, 4], 40, 40), ([3, 3, 3, 4, 4], 60, 60), ([3, 4, 4, 4, 4], 20, 60)]:
        unlock()
        app.extensions["judge0_client"].statuses = statuses
        result = finish(client, identities, start(client, identities, assessment).json["job"])
        assert result.json["job"]["score"] == expected
        assert result.json["best_score"] == best
        assert finish(client, identities, result.json["job"]).json["best_score"] == best
    unlock()
    app.extensions["judge0_client"].statuses = [3, 3, 13, 4, 4]
    failed = finish(client, identities, start(client, identities, assessment).json["job"])
    assert failed.json["job"]["status"] == "service_error"
    assert "score" not in failed.json["job"]
    assert failed.json["best_score"] == 60
    assert mongo.db.coding_scores.count_documents({}) == 1


def test_internal_executor_failure_is_not_a_grade(app, client, identities, assessment):
    app.extensions["judge0_client"].statuses = [13] * 5
    result = finish(client, identities, start(client, identities, assessment).json["job"])
    assert result.json["job"]["status"] == "service_error"
    assert "score" not in result.json["job"]
    assert mongo.db.coding_scores.count_documents({}) == 0


def test_temporary_poll_failure_preserves_pending_job(app, client, identities, assessment):
    job = start(client, identities, assessment).json["job"]
    app.extensions["judge0_client"].fail_read = True
    assert finish(client, identities, job).status_code == 503
    assert mongo.db.coding_jobs.find_one({})["status"] == "processing"
    app.extensions["judge0_client"].fail_read = False
    assert finish(client, identities, job).json["job"]["score"] == 100


def test_submission_failure_releases_active_slot(app, client, identities, assessment):
    app.extensions["judge0_client"].fail_create = True
    assert start(client, identities, assessment).status_code == 503
    assert not mongo.db.coding_jobs.find_one({})["active"]
    unlock()
    app.extensions["judge0_client"].fail_create = False
    assert start(client, identities, assessment).status_code == 202


def test_permissions_prevent_cross_candidate_and_company_access(client, identities, assessment):
    job = start(client, identities, assessment).json["job"]
    for key in ["other_talent", "other_company"]:
        assert client.get(f"/api/code-jobs/{job['_id']}", headers=identities[key]["headers"]).status_code == 403
    assert client.get(f"/api/challenges/{assessment}/coding/results", headers=identities["other_company"]["headers"]).status_code == 403
    assert client.get(f"/api/challenges/{assessment}/coding/results", headers=identities["talent"]["headers"]).status_code == 403
    assert client.get(f"/api/code-jobs/{job['_id']}").status_code == 401
    mongo.db.participations.delete_one({"talent_id": identities["other_talent"]["id"]})
    assert client.get(f"/api/challenges/{assessment}/coding", headers=identities["other_talent"]["headers"]).status_code == 403


def test_private_inputs_and_limits_cannot_be_supplied_by_candidate(app, client, identities, assessment):
    created = start(client, identities, assessment, expected_output="HACK", tests=[], enable_network=True, max_points=9999)
    assert created.status_code == 202
    sent = app.extensions["judge0_client"].created[0]
    assert len(sent["tests"]) == 5
    assert sent["tests"][0]["stdin"] == "FAIL\nOK\nFAIL\n"
    assert sent["limits"]["max_points"] == 100


@pytest.mark.parametrize("payload", [{"mode": "run"}, {"stdin": "arbitrary input"}, {"stdin": ""}])
def test_experimental_requests_cannot_create_jobs(app, client, identities, assessment, payload):
    assert start(client, identities, assessment, **payload).status_code == 400
    assert not app.extensions["judge0_client"].created
    assert mongo.db.coding_jobs.count_documents({}) == 0


def test_submission_mode_is_optional_and_defaults_to_grading(client, identities, assessment):
    response = client.post(f"/api/challenges/{assessment}/code-jobs", headers=identities["talent"]["headers"], json={"language": "python", "source_code": "print(0)"})
    assert response.status_code == 202
    assert response.json["job"]["mode"] == "submit"


def test_demo_endpoint_is_removed(client, identities):
    assert client.post("/api/coding/demo", headers=identities["owner"]["headers"]).status_code == 404


def test_previous_experimental_job_is_not_shown_in_the_editor(app, client, identities, assessment):
    legacy_run(app, identities, assessment)
    response = client.get(f"/api/challenges/{assessment}/coding", headers=identities["talent"]["headers"])
    assert response.status_code == 200
    assert response.json["latest_job"] is None


def test_active_job_and_cooldown_limit_repeated_requests(app, client, identities, assessment):
    app.extensions["judge0_client"].pending = True
    job = start(client, identities, assessment).json["job"]
    assert start(client, identities, assessment).status_code == 409
    app.extensions["judge0_client"].pending = False
    finish(client, identities, job)
    mongo.db.participations.update_many({}, {"$set": {"coding_next_submit_at": now() + timedelta(seconds=3)}})
    assert start(client, identities, assessment).status_code == 429


@pytest.mark.parametrize("status,deadline", [("closed", "2099-01-01"), ("published", "2020-01-01"), ("draft", "2099-01-01")])
def test_closed_draft_and_expired_challenges_reject_execution(client, identities, assessment, status, deadline):
    mongo.db.challenges.update_one({"_id": ObjectId(assessment)}, {"$set": {"status": status, "deadline": deadline}})
    assert start(client, identities, assessment).status_code == 409


def test_configuration_immutable_after_publication(client, identities, assessment):
    headers = identities["owner"]["headers"]
    config = client.get(f"/api/challenges/{assessment}/coding/config", headers=headers).json["config"]
    assert len(config["tests"]) == 5
    assert client.put(f"/api/challenges/{assessment}/coding/config", headers=headers, json=config).status_code == 409
    mongo.db.challenges.update_one({"_id": ObjectId(assessment)}, {"$set": {"status": "draft"}})
    assert client.put(f"/api/challenges/{assessment}/coding/config", headers=headers, json=config).status_code == 200


def test_worker_finalizes_without_a_browser(app, client, identities, assessment):
    start(client, identities, assessment)
    outcome = app.test_cli_runner().invoke(args=["process-code-jobs"])
    assert outcome.exit_code == 0, outcome.output
    assert mongo.db.coding_scores.find_one({})["score"] == 100


def test_stale_job_expires_without_penalty(app, client, identities, assessment):
    job = start(client, identities, assessment).json["job"]
    mongo.db.coding_jobs.update_many({}, {"$set": {"created_at": now() - timedelta(minutes=6)}})
    result = finish(client, identities, job)
    assert result.json["job"]["status"] == "service_error"
    assert mongo.db.coding_scores.count_documents({}) == 0


def test_missing_provider_is_explicit(app, client, identities, assessment):
    app.extensions["judge0_client"].configured = False
    response = start(client, identities, assessment)
    assert response.status_code == 503
    assert mongo.db.coding_jobs.count_documents({}) == 0


@pytest.mark.parametrize("payload", [{"source_code": " "}, {"language": "ruby"}, {"source_code": "x" * 64001}, {"stdin": "x" * 16001}, {"mode": "hack"}, {"mode": []}, {"language": {}}])
def test_invalid_submission_is_rejected(client, identities, assessment, payload):
    assert start(client, identities, assessment, **payload).status_code == 400


def test_company_results_have_one_row_per_candidate(client, identities, assessment):
    finish(client, identities, start(client, identities, assessment).json["job"])
    unlock()
    finish(client, identities, start(client, identities, assessment).json["job"])
    result = client.get(f"/api/challenges/{assessment}/coding/results", headers=identities["owner"]["headers"])
    assert result.status_code == 200
    rows = result.json["results"]
    assert len(rows) == 2
    assert rows[0]["score"] == 100
    assert rows[0]["attempts"] == 2


def test_invalid_config_numbers_and_types():
    valid = {"tests": [{"stdin": "", "expected_output": ""}]}
    for key, value in [("max_points", True), ("cpu_time_limit", float("nan")), ("languages", [{}])]:
        with pytest.raises(ValueError):
            validated_config({**valid, key: value})


def test_invalid_deadline_is_closed():
    assert deadline_passed({"deadline": "invalid"})


def test_worker_reconciles_a_completed_job_after_score_write_interruption(app, client, identities, assessment):
    job = finish(client, identities, start(client, identities, assessment).json["job"]).json["job"]
    mongo.db.coding_scores.delete_many({})
    mongo.db.coding_jobs.update_one({"_id": ObjectId(job["_id"])}, {"$unset": {"score_recorded": ""}})
    outcome = app.test_cli_runner().invoke(args=["process-code-jobs"])
    assert outcome.exit_code == 0
    assert mongo.db.coding_scores.find_one({})["score"] == 100
    assert mongo.db.coding_jobs.find_one({"_id": ObjectId(job["_id"])})["score_recorded"]


def test_editor_details_remain_available_during_provider_outage(app, client, identities, assessment):
    start(client, identities, assessment)
    unlock()
    app.extensions["judge0_client"].fail_read = True
    response = client.get(f"/api/challenges/{assessment}/coding", headers=identities["talent"]["headers"])
    assert response.status_code == 200
    assert response.json["warning"]
    assert response.json["latest_job"]["status"] == "processing"
    assert "tests" not in response.json["config"]


def test_participated_challenge_remains_visible_after_closing(client, identities, assessment):
    mongo.db.challenges.update_one({"_id": ObjectId(assessment)}, {"$set": {"status": "closed"}})
    response = client.get("/api/dashboard", headers=identities["talent"]["headers"])
    assert response.status_code == 200
    assert response.json["challenges"][0]["_id"] == assessment
    assert response.json["challenges"][0]["status"] == "closed"


def test_date_deadline_uses_end_of_sao_paulo_day(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr("app.services.coding.now", lambda: datetime(2030, 1, 2, 2, 59, 59, tzinfo=timezone.utc))
    assert not deadline_passed({"deadline": "2030-01-01"})
    monkeypatch.setattr("app.services.coding.now", lambda: datetime(2030, 1, 2, 3, 0, 0, tzinfo=timezone.utc))
    assert deadline_passed({"deadline": "2030-01-01"})
