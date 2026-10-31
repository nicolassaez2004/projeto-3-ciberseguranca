"""Private test configuration, durable execution jobs and idempotent CTF scores."""
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from flask import current_app
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..extensions import mongo
from ..utils import now
from .judge0 import LANGUAGES, Judge0Error, decoded_output

TERMINAL = {"completed", "service_error"}
STATUS_LABELS = {
    1: "Na fila", 2: "Executando", 3: "Correto", 4: "Resposta incorreta",
    5: "Tempo excedido", 6: "Erro de compilação", 7: "Erro de execução",
    8: "Limite de saída excedido", 9: "Erro de execução", 10: "Erro de execução",
    11: "Erro de execução", 12: "Erro de execução", 13: "Falha no executor", 14: "Formato inválido",
}


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def deadline_passed(challenge):
    value = str(challenge.get("deadline", ""))
    try:
        if len(value) == 10:
            # Date fields mean end of the selected calendar day in São Paulo.
            end = datetime.fromisoformat(value).replace(tzinfo=ZoneInfo("America/Sao_Paulo")) + timedelta(days=1)
        else:
            end = utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        return now() >= end
    except (ValueError, TypeError):
        return True  # Invalid dates cannot open a coding assessment indefinitely.


def validated_config(data):
    if not isinstance(data, dict):
        raise ValueError("Envie um objeto JSON com a configuração.")
    languages = data.get("languages", ["python"])
    if not isinstance(languages, list) or not languages or any(not isinstance(k, str) or k not in LANGUAGES for k in languages):
        raise ValueError("Escolha Python, JavaScript ou C++ como linguagens permitidas.")
    def number(key, default, minimum, maximum, integer=False):
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"Valor inválido para {key}.")
        if integer and not isinstance(value, int):
            raise ValueError(f"{key} deve ser inteiro.")
        return value
    points = number("max_points", 100, 1, 10000, True)
    cpu = number("cpu_time_limit", 2, 0.5, 5)
    memory = number("memory_limit_kb", 128000, 64000, 256000, True)
    tests = data.get("tests")
    if not isinstance(tests, list) or not 1 <= len(tests) <= 20:
        raise ValueError("Cadastre de 1 a 20 testes privados.")
    def case(value):
        if not isinstance(value, dict):
            raise ValueError("Cada teste deve ter stdin e expected_output.")
        output = {}
        for key in ("stdin", "expected_output"):
            text = value.get(key)
            if not isinstance(text, str) or len(text.encode("utf-8")) > 16000:
                raise ValueError(f"{key} deve ser texto de até 16 KB.")
            output[key] = text
        return output
    example = case(data.get("example", {"stdin": "", "expected_output": ""}))
    starters = data.get("starter_code", {})
    if not isinstance(starters, dict) or any(k not in languages or not isinstance(v, str) or len(v.encode("utf-8")) > 64000 for k, v in starters.items()):
        raise ValueError("Código inicial inválido.")
    return {"languages": list(dict.fromkeys(languages)), "max_points": points,
            "cpu_time_limit": cpu, "memory_limit_kb": memory,
            "tests": [case(t) for t in tests], "example": example, "starter_code": starters}


def public_config(config):
    return {key: config[key] for key in (
        "languages", "max_points", "cpu_time_limit", "memory_limit_kb", "example", "starter_code"
    )}


def record_score(job):
    if job.get("mode") != "submit" or job.get("status") != "completed":
        return
    key = {"challenge_id": job["challenge_id"], "talent_id": job["talent_id"]}
    try:
        mongo.db.coding_scores.update_one(key, {
            "$max": {"score": job["score"]},
            "$setOnInsert": {**key, "max_points": job["max_points"], "created_at": now()},
        }, upsert=True)
    except DuplicateKeyError:
        mongo.db.coding_scores.update_one(key, {"$max": {"score": job["score"]}})
    if job["score"] == job["max_points"]:
        mongo.db.coding_scores.update_one({**key, "solved_at": {"$exists": False}}, {
            "$set": {"solved_at": job["finished_at"]},
        })

    mongo.db.coding_jobs.update_one({"_id": job["_id"]}, {"$set": {"score_recorded": True}})


def public_job(job):
    # Explicit allowlist. Never return provider tokens, hidden outputs or test input.
    keys = ("_id", "challenge_id", "mode", "language", "status", "created_at", "finished_at",
            "passed", "total", "score", "max_points", "results", "output", "error")
    return {key: job[key] for key in keys if key in job}


def refresh_job(job):
    if job["status"] in TERMINAL:
        record_score(job)
        return job
    if (now() - utc(job["created_at"])).total_seconds() > 300:
        mongo.db.coding_jobs.update_one({"_id": job["_id"], "status": {"$nin": list(TERMINAL)}}, {
            "$set": {"status": "service_error", "active": False, "finished_at": now(),
                      "error": "O executor não concluiu a tempo. Nenhum ponto foi perdido; envie novamente."},
        })
        return mongo.db.coding_jobs.find_one({"_id": job["_id"]})
    # A short lease limits requests when a browser, another tab and the worker poll together.
    claimed = mongo.db.coding_jobs.find_one_and_update({
        "_id": job["_id"], "status": "processing",
        "$or": [{"refresh_after": {"$exists": False}}, {"refresh_after": {"$lte": now()}}],
    }, {"$set": {"refresh_after": now() + timedelta(seconds=2)}}, return_document=ReturnDocument.AFTER)
    if not claimed:
        return mongo.db.coding_jobs.find_one({"_id": job["_id"]})
    rows = current_app.extensions["judge0_client"].get_batch(claimed["provider_tokens"], include_output=claimed["mode"] == "run")
    statuses = [r.get("status", {}).get("id") if isinstance(r.get("status"), dict) else None for r in rows]
    if any(s not in STATUS_LABELS for s in statuses):
        raise Judge0Error("Resposta inválida do executor. A pontuação ainda não foi calculada.")
    if any(s in {1, 2} for s in statuses):
        return claimed
    values = {"finished_at": now(), "active": False}
    if 13 in statuses:
        values.update(status="service_error", error="Houve uma falha no executor. Envie novamente; esta tentativa não recebeu nota.")
    else:
        passed = statuses.count(3)
        values.update(status="completed", passed=passed, results=[
            {"number": i + 1, "passed": status == 3, "status": STATUS_LABELS[status]}
            for i, status in enumerate(statuses)
        ])
        if claimed["mode"] == "submit":
            values["score"] = float((Decimal(claimed["max_points"]) * passed / len(statuses)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        else:
            row = rows[0]
            values["output"] = {key: decoded_output(row.get(key)) for key in ("stdout", "stderr", "compile_output")}
    mongo.db.coding_jobs.update_one({"_id": claimed["_id"], "status": "processing"}, {"$set": values})
    updated = mongo.db.coding_jobs.find_one({"_id": claimed["_id"]})
    record_score(updated)
    return updated
