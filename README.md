# Return Tech — como rodar

Requisitos: Python 3.11+, Node.js 24, MongoDB acessível e uma instância do Judge0 configurada. No Windows, o Judge0 local usa a VM Ubuntu já preparada no Multipass.

## 1. Configuração

Use o `.env` existente do backend, com os valores da sua instalação:

```env
MONGO_URI=sua-uri-do-mongodb
JWT_SECRET_KEY=sua-chave-privada
FRONTEND_URL=http://localhost:5173
PORT=5000
FLASK_DEBUG=0
JUDGE0_URL=http://127.0.0.1:2358
JUDGE0_AUTH_TOKEN=token-da-sua-instancia
```

No `.env` do frontend:

```env
VITE_API_URL=http://localhost:5000/api
```

O endereço do Judge0 deve estar acessível pelo backend. Para uma instância remota, use HTTPS. Detalhes: [INTEGRACAO_JUDGE0.md](INTEGRACAO_JUDGE0.md).

## 2. Instalar dependências uma vez

Dentro de `Return_Tech_Backend`, no Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

No Linux:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

Se a `.venv` já existir nesse computador, execute somente a instalação das dependências.

Dentro de `Return_Tech_Frontend`:

```bash
npm ci
```

## 3. Iniciar o Judge0

Se estiver usando a VM já configurada neste projeto, execute no terminal do computador:

```text
multipass start returntech-judge0
multipass exec returntech-judge0 -- sudo docker compose -f /home/ubuntu/judge0-v1.13.1/docker-compose.yml up -d
```

No Windows, o redirecionamento de `127.0.0.1:2358` deve apontar para o IP atual da VM. Se usar outro serviço Judge0, mantenha-o ativo no endereço definido em `JUDGE0_URL`.

## 4. Iniciar o site

Abra três terminais e mantenha-os em execução:

| Terminal e pasta | Windows | Linux |
|---|---|---|
| Backend — `Return_Tech_Backend` | `.\.venv\Scripts\python.exe run.py` | `./.venv/bin/python run.py` |
| Fila de correções — `Return_Tech_Backend` | `.\.venv\Scripts\python.exe -m flask --app run process-code-jobs --watch` | `./.venv/bin/python -m flask --app run process-code-jobs --watch` |
| Frontend — `Return_Tech_Frontend` | `npm run dev` | `npm run dev` |

Abra **http://localhost:5173**. A fila acompanha o Judge0 e salva as notas. MongoDB e Judge0 precisam continuar disponíveis.

Para parar, use `Ctrl+C` nos três terminais. Para desligar também a VM local: `multipass stop returntech-judge0`.
