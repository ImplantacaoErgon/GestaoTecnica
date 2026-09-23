# Build context deste Dockerfile é a RAIZ do repositório (não a pasta
# backend/) — por isso os caminhos abaixo começam com "backend/" ou
# "frontend/". Isso é necessário porque a imagem final também precisa do
# frontend/ copiado para dentro dela (ver comentário perto de FRONTEND_DIR
# mais abaixo). Ao rodar manualmente: `docker build -f backend/Dockerfile .`
# a partir da raiz do projeto. Com docker-compose (docker-compose.yml e
# docker-compose.supabase.yml) isso já está configurado corretamente.
FROM python:3.11-slim

# psql client — usado por app/db.py para falar com o Postgres (ver comentário lá)
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/run.py .

# Em desenvolvimento local (docker-compose.yml / docker-compose.supabase.yml)
# o volume "./frontend:/frontend:ro" sobrepõe esta cópia, então editar
# frontend/index.html reflete na hora, sem rebuild. Já em produção (Render,
# ou qualquer deploy que rode só esta imagem sem docker-compose) não existe
# esse volume, então o frontend PRECISA estar copiado dentro da imagem —
# é para isso que esta linha serve.
COPY frontend /frontend

ENV FRONTEND_DIR=/frontend
RUN mkdir -p /app/uploads

EXPOSE 8000
# --timeout 900: bumped na 55ª rodada por causa da importação de Comparação
# Folha (app/comparacao_folha_import.py) — ~300 mil linhas por arquivo,
# ~130MB, importadas via COPY em streaming. Medido em teste de escala local
# (100 mil linhas reais, duplicadas com sufixo único): ~50s e ~76MB de pico
# de memória Python, o que extrapola (linear) pra ~150-160s pra 300 mil
# linhas — mas isso foi medido num Postgres local, sem a etapa de download
# do arquivo do Google Drive nem a latência de um Postgres remoto (Render).
# 900s dá margem generosa pra tudo isso (download + import) mesmo num
# cenário bem mais lento que o medido, sem deixar o worker do gunicorn matar
# a requisição no meio da carga — mesma razão que já justificava o valor
# anterior (240s) pra importação de cronograma em lotes
# (app/cronograma_import.py), só que agora pra uma carga bem mais pesada.
# O timeout interno da própria carga (db.execute_stream /
# importar_comparacao_folha) já usa os mesmos 900s como padrão — os dois
# devem ficar alinhados.
#
# 0.0.0.0:${PORT:-8000} (em vez de 8000 fixo): plataformas como o Render
# escolhem a porta e a informam pela variável de ambiente PORT (normalmente
# 10000) — o servidor precisa escutar nela. Localmente (docker-compose), PORT
# não é definida, então cai no padrão 8000, que é o que o compose já expõe.
# Forma "shell" do CMD (sem colchetes) porque só ela expande variáveis de
# ambiente; com a forma "exec" ("${PORT}" ficaria literal).
CMD gunicorn -w 4 -b 0.0.0.0:${PORT:-8000} --timeout 900 run:app
