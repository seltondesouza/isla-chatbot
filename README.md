# ISLA Assistente

Assistente académico para estudantes do ISLA Santarém. Cada estudante entra com a sua conta e pergunta:

- **"Que aulas tenho hoje?"** → horário real da sua turma, já com feriados e interrupções.
- **"O que tenho de pagar este mês e porquê?"** → propinas/emolumentos, prazo, estado (em atraso?) e o motivo.
- **"Que atividades há?" / "Quero inscrever-me"** → eventos do site + atividades da escola, com candidatura pela app.
- **"Como me candidato a uma bolsa?"** → resposta a partir do site e dos regulamentos, com link para a fonte.

## Como funciona

```
Browser (app/static)  →  FastAPI (app/main.py)
                           ├─ agent.py    LLM (OpenRouter) com ferramentas, ou modo básico sem IA
                           ├─ tools.py    horário, pagamentos, calendário, atividades, pesquisa — sempre só os dados do utilizador autenticado
                           ├─ search.py   pesquisa full-text (SQLite FTS5) em páginas, PDFs e documentos
                           ├─ crawler.py  lê o site da escola a cada 12 h (páginas, notícias, eventos, PDFs de regulamentos/propinas)
                           └─ importers.py  CSV da secretaria: estudantes, UCs, inscrições, horário, calendário, pagamentos, atividades
SQLite (data/isla.db)
```

O modelo nunca vê a base de dados: pede dados através de ferramentas que filtram sempre pelo estudante com sessão iniciada. Candidaturas exigem confirmação explícita.

## Arrancar

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env          # coloca a OPENROUTER_API_KEY
.venv/bin/python -m scripts.seed_demo   # opcional: dados de demonstração
.venv/bin/uvicorn app.main:app --port 8080
```

Abre http://localhost:8080. A conta de admin é criada no primeiro arranque (`ADMIN_PASSWORD` no `.env`, ou uma palavra-passe aleatória em `data/admin_password.txt`). As contas de demonstração ficam em `data/demo_credentials.txt`.

Sem `OPENROUTER_API_KEY` funciona em **modo básico** (respostas automáticas sem IA) — útil para testar.

## Dados da escola

Na página **/admin → Importar dados** a secretaria carrega CSV (há um modelo para descarregar em cada tipo; exemplos em `app/sample_data/`). Importar outra vez atualiza, não duplica. Ao importar estudantes é gerado um CSV com as palavras-passe temporárias; no primeiro acesso o estudante tem de definir uma nova.

Documentos das UCs: carregar em **/admin → Documentos** (com o código da UC — só os inscritos os veem na pesquisa) ou em lote:

```bash
.venv/bin/python -m scripts.import_folder caminho/para/courses   # subpastas com o código da UC
```

## Modelo de IA

Por omissão `anthropic/claude-haiku-5.5` (≈ 0,80 $ por 1000 mensagens; bom com ferramentas e português europeu). Para respostas mais fortes: `LLM_MODEL=anthropic/claude-sonnet-5.5`. Evitar modelos `:free` com dados pessoais; ativar "zero data retention" no OpenRouter.

## Testes

```bash
.venv/bin/python -m pytest -q
```

## Produção

`docker build -t isla . && docker run -p 8080:8080 -v isla-data:/srv/data --env-file .env isla`, atrás de HTTPS (com `COOKIE_SECURE=true`). Um único processo chega para uma escola; faz cópia de segurança da pasta `data/`.
