"""Judge0 HTTP adapter. Candidate programs are never executed by Flask."""
import base64
import re
from urllib.parse import urlsplit

import requests


class Judge0Error(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


LANGUAGES = {
    "python": ("Python", "python", lambda name: name.startswith("Python (3")),
    "javascript": ("JavaScript", "javascript", lambda name: name.startswith("JavaScript (Node.js")),
    "cpp": ("C++", "cpp", lambda name: name.startswith("C++ (GCC")),
}


class Judge0Client:
    def __init__(self, config):
        self.url = config.get("JUDGE0_URL", "").rstrip("/")
        self.key = config.get("JUDGE0_API_KEY", "")
        self.host = config.get("JUDGE0_RAPIDAPI_HOST", "")
        self.auth_token = config.get("JUDGE0_AUTH_TOKEN", "")
        self.timeout = 10
        self._language_cache = None

    @property
    def configured(self):
        return bool(self.url)

    def _request(self, method, path, payload=None, params=None):
        if not self.configured:
            raise Judge0Error("Execução indisponível. A equipe precisa configurar o Judge0 no backend.", 503)
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise Judge0Error("O endereço do Judge0 precisa usar HTTPS.", 503)
        headers = {"Accept": "application/json"}
        if self.key:
            headers["X-RapidAPI-Key"] = self.key
        if self.host:
            headers["X-RapidAPI-Host"] = self.host
        if self.auth_token:
            headers["X-Auth-Token"] = self.auth_token
        try:
            with requests.request(
                method, self.url + path, headers=headers, json=payload,
                params=params, timeout=(4, self.timeout), allow_redirects=False, stream=True,
            ) as response:
                if response.status_code == 429:
                    raise Judge0Error("O serviço de execução atingiu seu limite. Tente novamente mais tarde.", 503)
                if not 200 <= response.status_code < 300:
                    # Never forward upstream bodies: they can contain keys, test input or source code.
                    raise Judge0Error("O serviço de execução está indisponível. Avise a equipe responsável.", 502)
                body = bytearray()
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise Judge0Error("A resposta do executor excedeu o limite permitido.")
                import json
                return json.loads(body)
        except (requests.RequestException, ValueError) as exc:
            raise Judge0Error("Não foi possível consultar o executor. Tente novamente.", 503) from exc

    def languages(self):
        if self._language_cache is None:
            rows = self._request("GET", "/languages")
            if not isinstance(rows, list):
                raise Judge0Error("Resposta inválida do executor.")
            resolved = {}
            for key, (label, editor, matches) in LANGUAGES.items():
                candidates = [r for r in rows if isinstance(r, dict) and matches(str(r.get("name", "")))]
                if candidates:
                    chosen = max(candidates, key=lambda r: tuple(int(n) for n in re.findall(r"\d+", r["name"])))
                    if isinstance(chosen.get("id"), int):
                        resolved[key] = {"id": chosen["id"], "name": label, "runtime": chosen["name"], "editor": editor}
            self._language_cache = resolved
        return self._language_cache

    def create_batch(self, language, source, tests, limits):
        runtime = self.languages().get(language)
        if not runtime:
            raise Judge0Error("A linguagem escolhida não está disponível neste executor.", 422)
        encode = lambda value: base64.b64encode(value.encode("utf-8")).decode("ascii")
        submissions = []
        for case in tests:
            item = {
                "language_id": runtime["id"], "source_code": encode(source),
                "stdin": encode(case["stdin"]), "enable_network": False,
                "cpu_time_limit": limits["cpu_time_limit"],
                "wall_time_limit": max(10, limits["cpu_time_limit"] * 3),
                "memory_limit": limits["memory_limit_kb"],
                "max_file_size": 64, "max_processes_and_or_threads": 20,
            }
            if case.get("expected_output") is not None:
                item["expected_output"] = encode(case["expected_output"])
            submissions.append(item)
        rows = self._request("POST", "/submissions/batch", {"submissions": submissions}, {"base64_encoded": "true"})
        if not isinstance(rows, list) or len(rows) != len(tests):
            raise Judge0Error("O executor não confirmou todos os testes. Nenhuma pontuação foi atribuída.")
        tokens = [r.get("token") if isinstance(r, dict) else None for r in rows]
        if not all(isinstance(t, str) and re.fullmatch(r"[A-Za-z0-9-]{8,100}", t) for t in tokens) or len(set(tokens)) != len(tokens):
            raise Judge0Error("O executor não confirmou todos os testes. Nenhuma pontuação foi atribuída.")
        return tokens

    def get_batch(self, tokens, include_output=False):
        data = self._request("GET", "/submissions/batch", params={
            "tokens": ",".join(tokens), "base64_encoded": "true",
            "fields": "token,status,time,memory" + (",stdout,stderr,compile_output" if include_output else ""),
        })
        rows = data.get("submissions") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise Judge0Error("Resposta inválida do executor.")
        mapped = {r.get("token"): r for r in rows if isinstance(r, dict)}
        if set(mapped) != set(tokens) or len(rows) != len(tokens):
            raise Judge0Error("O executor ainda não confirmou todos os resultados.", 503)
        return [mapped[token] for token in tokens]


def decoded_output(value):
    if not isinstance(value, str):
        return ""
    try:
        encoded = re.sub(r"[ \t\r\n]+", "", value)
        return base64.b64decode(encoded, validate=True).decode("utf-8", "replace")[:16000]
    except (ValueError, UnicodeError):
        return "[Saída não disponível]"
